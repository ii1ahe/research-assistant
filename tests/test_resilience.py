"""Running an operation under a policy, and bounding it in time.

The policy's own arithmetic lives in ``test_retry_policy.py``; this module is
about what happens when it is applied — how many times the operation is called,
what the waits between calls are, and what escapes at the end.

Every test injects the sleep, so the backoff schedule is asserted exactly and
the suite never waits for it.
"""

from __future__ import annotations

import asyncio

import pytest

from researcher.errors import ConfigurationError, StorageError, UpstreamError, UpstreamTimeoutError
from researcher.services.resilience import Attempts, RetryPolicy, deadline, execute

pytestmark = pytest.mark.asyncio


class Recorder:
    """A sleep replacement that records the delays it was asked for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def test_success_on_the_first_attempt_does_not_sleep() -> None:
    sleeper = Recorder()
    attempts = Attempts()

    async def operation() -> str:
        return "ok"

    result = await execute(
        operation, policy=RetryPolicy(), description="test", attempts=attempts, sleep=sleeper
    )

    assert result == "ok"
    assert attempts.count == 1
    assert sleeper.delays == []


async def test_a_transient_failure_is_retried_and_can_succeed() -> None:
    sleeper = Recorder()
    attempts = Attempts()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UpstreamError("first attempt fails", source="arxiv")
        return "recovered"

    result = await execute(
        operation,
        policy=RetryPolicy(max_attempts=3, initial_backoff=0.5),
        description="test",
        attempts=attempts,
        sleep=sleeper,
        uniform=lambda: 1.0,
    )

    assert result == "recovered"
    assert attempts.count == 2
    assert sleeper.delays == [0.5]


async def test_attempts_are_exhausted_before_the_error_escapes() -> None:
    """The failure is re-raised unchanged, not wrapped in a retry error."""
    sleeper = Recorder()
    attempts = Attempts()

    async def operation() -> str:
        raise UpstreamError("always fails", source="web")

    with pytest.raises(UpstreamError, match="always fails") as caught:
        await execute(
            operation,
            policy=RetryPolicy(max_attempts=3, initial_backoff=1.0),
            description="test",
            attempts=attempts,
            sleep=sleeper,
            uniform=lambda: 1.0,
        )

    assert attempts.count == 3
    assert sleeper.delays == [1.0, 2.0]
    assert caught.value.source == "web"


async def test_a_non_retryable_failure_stops_immediately() -> None:
    """One attempt, no sleep — the point of classifying in the first place."""
    sleeper = Recorder()
    attempts = Attempts()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ConfigurationError("GOOGLE_API_KEY is not set")

    with pytest.raises(ConfigurationError):
        await execute(
            operation,
            policy=RetryPolicy(max_attempts=5),
            description="test",
            attempts=attempts,
            sleep=sleeper,
        )

    assert calls == 1
    assert attempts.count == 1
    assert sleeper.delays == []


async def test_jitter_is_drawn_from_below_the_ceiling() -> None:
    """Full jitter: the wait is spread over the interval, not pinned to its end.

    Pinned backoff is what synchronises a retry storm — several sources failing
    together would all return to the provider at the same instant.
    """
    sleeper = Recorder()

    async def operation() -> str:
        raise UpstreamError("fails", source="web")

    for fraction, expected in ((0.0, 0.0), (0.25, 0.25), (1.0, 1.0)):
        sleeper.delays.clear()
        with pytest.raises(UpstreamError):
            await execute(
                operation,
                policy=RetryPolicy(max_attempts=2, initial_backoff=1.0),
                description="test",
                sleep=sleeper,
                uniform=lambda f=fraction: f,
            )
        assert sleeper.delays == [expected]


async def test_cancellation_is_not_swallowed() -> None:
    """A cancelled task must stay cancelled.

    ``except ResearcherError`` deliberately does not catch ``CancelledError``,
    which is a ``BaseException``. Catching it would break timeouts and shutdown
    — and would look, from the outside, exactly like a retry.
    """
    sleeper = Recorder()

    async def operation() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await execute(
            operation, policy=RetryPolicy(max_attempts=3), description="test", sleep=sleeper
        )

    assert sleeper.delays == []


async def test_an_unexpected_error_type_is_not_retried() -> None:
    """Only our own taxonomy is retried; anything else is a bug worth surfacing."""
    sleeper = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ZeroDivisionError("programming error")

    with pytest.raises(ZeroDivisionError):
        await execute(
            operation, policy=RetryPolicy(max_attempts=3), description="test", sleep=sleeper
        )

    assert calls == 1


async def test_a_storage_failure_is_not_retried_here() -> None:
    """Retryability is the error's decision, and StorageError declines by default."""
    sleeper = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise StorageError("write failed")

    with pytest.raises(StorageError):
        await execute(
            operation, policy=RetryPolicy(max_attempts=3), description="test", sleep=sleeper
        )

    assert calls == 1


async def test_a_timeout_is_not_retried_under_a_no_timeout_policy() -> None:
    """End to end: the policy flag reaches the retry decision, not just the predicate."""
    sleeper = Recorder()
    attempts = Attempts()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise UpstreamTimeoutError("took too long", source="synthesis")

    with pytest.raises(UpstreamTimeoutError):
        await execute(
            operation,
            policy=RetryPolicy(max_attempts=3, retry_timeouts=False),
            description="synthesis",
            attempts=attempts,
            sleep=sleeper,
        )

    assert calls == 1
    assert attempts.count == 1


async def test_the_attempt_tally_is_optional() -> None:
    """Callers that do not need the count must not have to supply a tally."""

    async def operation() -> str:
        return "ok"

    assert await execute(operation, policy=RetryPolicy(), description="test") == "ok"


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


async def test_deadline_lets_a_prompt_operation_through() -> None:
    async with deadline(5.0, source="arxiv"):
        await asyncio.sleep(0)


async def test_deadline_raises_our_timeout_error_not_the_builtin() -> None:
    """Callers see ``UpstreamTimeoutError``, and it carries the source.

    ``asyncio.timeout`` raises the builtin ``TimeoutError``; letting that escape
    would give the application two timeout types to reason about.
    """
    with pytest.raises(UpstreamTimeoutError) as caught:
        async with deadline(0.01, source="wikipedia"):
            await asyncio.sleep(5)

    assert caught.value.code == "upstream_timeout"
    assert caught.value.source == "wikipedia"
    # Retryable, because a fetch that timed out is worth trying again.
    assert caught.value.retryable
    assert isinstance(caught.value.__cause__, TimeoutError)


async def test_deadline_measures_the_whole_block_not_each_step() -> None:
    """It bounds *total* time in the block, which is what bounds a source.

    Ten steps of 0.02s is 0.2s in total; any one of them is far inside the
    0.05s deadline. Only a deadline that measures the whole block can fail
    this, so it is the assertion that tells the two readings apart.
    """

    async def steps() -> None:
        async with deadline(0.05, source="arxiv"):
            for _ in range(10):
                await asyncio.sleep(0.02)

    with pytest.raises(UpstreamTimeoutError):
        await steps()


async def test_a_researcher_error_inside_the_block_is_untouched() -> None:
    """The deadline translates timeouts, not everything that passes through it."""
    with pytest.raises(UpstreamError, match="real failure"):
        async with deadline(5.0, source="arxiv"):
            raise UpstreamError("real failure", source="arxiv")


async def test_a_timeout_inside_execute_is_retried_by_default() -> None:
    """A timeout raised by the deadline is a retryable error, so it is retried.

    Stated as a test because it is the interaction between the two pieces, and
    the interaction is what a reader would otherwise have to reconstruct. In the
    application this is bounded by putting the deadline *outside* ``execute`` —
    see ``AIService.fetch_source`` — so retries share one source-level budget
    rather than each getting a fresh one.
    """
    attempts = Attempts()
    sleeper = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        async with deadline(0.01, source="arxiv"):
            await asyncio.sleep(5)
        return "unreachable"

    with pytest.raises(UpstreamTimeoutError):
        await execute(
            operation,
            policy=RetryPolicy(max_attempts=3),
            description="test",
            attempts=attempts,
            sleep=sleeper,
        )

    assert calls == 3
    assert attempts.count == 3
