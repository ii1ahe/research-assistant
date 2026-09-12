"""Retry policy and deadlines for outbound calls.

Every call this application makes leaves the process: a source fetch over HTTP,
or a provider call for synthesis. Each can fail transiently, and each needs an
upper bound on how long it may take. Both questions are really the same
question — how long to keep trying — so they are answered together here.

Classification is deliberately *not* done in this module. A failure arrives
already translated into a :class:`~researcher.errors.ResearcherError`, and this
module retries exactly when that error reports itself retryable. The reason is
that translation is the only step that can see the dependency's own exception,
and therefore the only step that can tell a missing credential from a dropped
connection: the supplied package raises one coarse ``ProviderError`` for both.
Keeping that judgement at the boundary and consuming its verdict here means
there is one classifier rather than two that can drift apart.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from researcher.errors import ResearcherError, UpstreamTimeoutError

__all__ = ["Attempts", "RetryPolicy", "deadline", "execute"]

logger = logging.getLogger(__name__)

#: Signature of the sleep used between attempts. Injected so tests exercise the
#: backoff policy without waiting in real time.
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class Attempts:
    """A tally of how many attempts an :func:`execute` call has made.

    Passed in by callers that must report the count *alongside* the outcome, on
    both the success and the failure path. :class:`~researcher.models.
    SourceOutcome` carries exactly that field, and a successful return can carry
    a value or an attempt count but not both — so the count is handed back
    through here rather than complicating every caller's return type.

    Deliberately mutable, and deliberately not shared between calls.
    """

    count: int = field(default=0)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many attempts to make, and how long to wait between them.

    Attributes:
        max_attempts: Total attempts, including the first. One means no retry.
        initial_backoff: Ceiling on the wait after the first failure.
        max_backoff: Ceiling on the wait after any failure.
        retry_timeouts: Whether a timeout is worth another attempt. True for a
            fetch, which is cheap and idempotent. False for synthesis — see
            :class:`~researcher.errors.UpstreamTimeoutError` for why a timed-out
            synthesis may still be running, and paying, after it is abandoned.
    """

    max_attempts: int = 3
    initial_backoff: float = 0.5
    max_backoff: float = 8.0
    retry_timeouts: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {self.max_attempts}")
        if self.initial_backoff < 0:
            raise ValueError(f"initial_backoff must not be negative, got {self.initial_backoff}")
        if self.max_backoff < self.initial_backoff:
            raise ValueError(
                f"max_backoff ({self.max_backoff}) must not be below "
                f"initial_backoff ({self.initial_backoff})"
            )

    def backoff_ceiling(self, attempt: int) -> float:
        """Return the longest wait permitted *after* attempt number ``attempt``.

        Doubles per attempt and saturates at :attr:`max_backoff`. This is the
        ceiling rather than the wait itself because the actual wait is jittered
        down from it — see :func:`execute`.

        The doubling stops once it reaches the cap instead of being computed and
        then clamped. ``2.0 ** n`` raises ``OverflowError`` from ``n = 1024``,
        so the obvious one-liner — ``min(max_backoff, initial * 2 ** (n - 1))``
        — crashes for a large attempt count rather than returning the cap: the
        clamp is never reached, because the exponentiation raises first. A
        generous :attr:`max_attempts` would turn the retry loop into an
        ``OverflowError`` at exactly the moment it was meant to be patient.
        """
        ceiling = self.initial_backoff
        for _ in range(attempt - 1):
            grown = min(ceiling * 2.0, self.max_backoff)
            if grown == ceiling:
                # Saturated, or `initial_backoff` is zero and doubling cannot
                # change that. Either way, waiting longer cannot change it.
                break
            ceiling = grown
        return ceiling

    def should_retry(self, exc: BaseException) -> bool:
        """Return whether ``exc`` justifies spending another attempt."""
        if isinstance(exc, UpstreamTimeoutError) and not self.retry_timeouts:
            return False
        return isinstance(exc, ResearcherError) and exc.retryable


async def execute[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    description: str,
    attempts: Attempts | None = None,
    sleep: Sleeper = asyncio.sleep,
    uniform: Callable[[], float] = random.random,
) -> T:
    """Run ``operation``, retrying the failures ``policy`` permits.

    The wait before a retry is drawn uniformly from ``[0, ceiling)`` rather
    than being the ceiling itself. That is full jitter: when several sources
    fail together — which is what a provider outage looks like — fixed backoff
    would march every one of them back to the same endpoint at the same instant
    and trip the rate limit that caused the failure. Spreading them out is the
    difference between recovering and synchronising the retry storm.

    Args:
        operation: A zero-argument coroutine function performing one attempt.
            It must be safe to call again. Every operation retried here is
            either an idempotent HTTP GET or a provider call that failed
            without returning a result.
        policy: Attempts, backoff and timeout handling.
        description: Short phrase naming the operation, for log records.
        attempts: When supplied, updated with the number of attempts made.
            Read it after this returns *or raises*; it is correct on both paths.
        sleep: Injected in tests.
        uniform: Source of jitter in ``[0, 1)``. Injected in tests.

    Returns:
        Whatever ``operation`` returned on its first successful attempt.

    Raises:
        ResearcherError: The failure from the final attempt, re-raised
            unchanged. Nothing is wrapped, so the caller sees the same error
            whether it took one attempt or three.
    """
    attempt = 0
    while True:
        attempt += 1
        if attempts is not None:
            attempts.count = attempt
        try:
            return await operation()
        except ResearcherError as exc:
            if attempt >= policy.max_attempts or not policy.should_retry(exc):
                # Bare re-raise: the original traceback is part of the
                # diagnostic, and `raise exc` would append this frame to it.
                raise
            delay = uniform() * policy.backoff_ceiling(attempt)
            logger.warning(
                "%s failed on attempt %d/%d (%s: %s); retrying in %.2fs",
                description,
                attempt,
                policy.max_attempts,
                exc.code,
                exc.message,
                delay,
            )
            await sleep(delay)


@asynccontextmanager
async def deadline(seconds: float, *, source: str) -> AsyncIterator[None]:
    """Bound the enclosed block to ``seconds`` of wall-clock time.

    Applied per source rather than around a gather: a single deadline over the
    whole retrieval would let the slowest source consume the budget of the
    others, which is the case the topic contract exists to prevent.

    Note:
        A ``TimeoutError`` raised *inside* the block is indistinguishable from
        the one this context manager produces, and is translated the same way.
        Acceptable because the enclosed operations do not raise it themselves —
        ``httpx`` reports its own timeouts as ``httpx.TimeoutException`` — but
        worth knowing before wrapping anything that does.

    Raises:
        UpstreamTimeoutError: The block exceeded its deadline.
    """
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError as exc:
        raise UpstreamTimeoutError(f"exceeded its {seconds:g}s deadline", source=source) from exc
