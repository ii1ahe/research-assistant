"""Defines retry delays, respects provider retry hints, and limits how long an operation may run."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from researcher.errors import ResearcherError, UpstreamRateLimitError, UpstreamTimeoutError

__all__ = ["Attempts", "RetryPolicy", "deadline", "execute"]

logger = logging.getLogger(__name__)

# Inject sleep so retry tests do not wait for real backoff delays.
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class Attempts:
    """Track attempts for one call, including calls that fail. Do not share this object between
    calls.
    """

    count: int = field(default=0)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Settings for retry counts and delays.

    Attributes:
        max_attempts: Total attempts, including the first.
        initial_backoff: Maximum random delay after the first failure.
        max_backoff: Maximum random delay after later failures.
        retry_timeouts: Whether to retry timeouts. Fetches may retry; answer requests
            may still run and be billed after a timeout, so they do not retry by default.
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
        """Double the retry delay per attempt, stopping at max_backoff before a large exponent
        can overflow.
        """
        ceiling = self.initial_backoff
        for _ in range(attempt - 1):
            grown = min(ceiling * 2.0, self.max_backoff)
            if grown == ceiling:
                # Stop when doubling no longer changes the delay.
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
    """Retry eligible failures with a random delay up to the backoff ceiling. A provider
    Retry-After hint sets the minimum delay and may exceed max_backoff; the outer deadline
    still applies. Update attempts on success and failure, and re-raise the last error
    unchanged.
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
                # Bare raise preserves the original traceback.
                raise
            delay = uniform() * policy.backoff_ceiling(attempt)
            retry_after = (
                exc.retry_after_seconds if isinstance(exc, UpstreamRateLimitError) else None
            )
            if retry_after is not None and retry_after > delay:
                logger.warning(
                    "%s failed on attempt %d/%d (%s: %s); provider said to retry "
                    "in %.2fs, honouring that over the %.2fs backoff",
                    description,
                    attempt,
                    policy.max_attempts,
                    exc.code,
                    exc.message,
                    retry_after,
                    delay,
                )
                delay = retry_after
            else:
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
    """Limit the enclosed operation to seconds of elapsed time.

    Note:
        Any TimeoutError from inside the block is also translated. HTTPX uses its
        own timeout exception, so its request failures stay separate.

    Raises:
        UpstreamTimeoutError: The block exceeds its deadline or raises TimeoutError.
    """
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError as exc:
        raise UpstreamTimeoutError(f"exceeded its {seconds:g}s deadline", source=source) from exc
