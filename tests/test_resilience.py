"""Checks retries, backoff, provider wait hints, cancellation, and deadlines."""

from __future__ import annotations

import asyncio

import pytest

from researcher.errors import (
    ConfigurationError,
    StorageError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)
from researcher.services.resilience import Attempts, RetryPolicy, deadline, execute

pytestmark = pytest.mark.asyncio


class Recorder:
    """A sleep replacement that records the delays it was asked for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


# Execution


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
    """Full jitter: the wait is spread over the interval, not pinned to its end."""
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
    """A cancelled task must stay cancelled."""
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


# Provider hints


async def test_a_provider_delay_larger_than_the_backoff_is_waited() -> None:
    """The provider's own number beats ours when it is larger."""
    sleeper = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UpstreamRateLimitError(
                "synthesis is rate-limited", source="synthesis", retry_after_seconds=2.0
            )
        return "recovered"

    result = await execute(
        operation,
        policy=RetryPolicy(max_attempts=3, initial_backoff=0.1),
        description="synthesis",
        sleep=sleeper,
        uniform=lambda: 1.0,  # the largest draw our backoff allows, still below the hint
    )

    assert result == "recovered"
    assert sleeper.delays == [2.0]


async def test_the_backoff_is_kept_when_it_is_larger_than_the_provider_delay() -> None:
    """The hint is a floor, not a replacement: a small hint must not *shorten*
    our own wait, which the jitter exists to spread."""
    sleeper = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UpstreamRateLimitError(
                "web is rate-limited", source="web", retry_after_seconds=0.1
            )
        return "recovered"

    result = await execute(
        operation,
        policy=RetryPolicy(max_attempts=3, initial_backoff=1.0),
        description="web fetch",
        sleep=sleeper,
        uniform=lambda: 0.5,
    )

    assert result == "recovered"
    assert sleeper.delays == [0.5]


async def test_the_provider_delay_is_waited_on_every_attempt() -> None:
    """The hint applies per attempt, and survives the error on the way out."""
    sleeper = Recorder()
    attempts = Attempts()

    async def operation() -> str:
        raise UpstreamRateLimitError(
            "synthesis is rate-limited", source="synthesis", retry_after_seconds=1.5
        )

    with pytest.raises(UpstreamRateLimitError) as caught:
        await execute(
            operation,
            policy=RetryPolicy(max_attempts=3, initial_backoff=0.0),
            description="synthesis",
            attempts=attempts,
            sleep=sleeper,
            uniform=lambda: 0.0,
        )

    assert attempts.count == 3
    assert sleeper.delays == [1.5, 1.5]
    assert caught.value.retry_after_seconds == 1.5


async def test_a_rate_limit_without_a_named_delay_uses_the_ordinary_backoff() -> None:
    """No hint, no override: the throttle is retried like any transient failure."""
    sleeper = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UpstreamRateLimitError("web is rate-limited", source="web")
        return "recovered"

    result = await execute(
        operation,
        policy=RetryPolicy(max_attempts=3, initial_backoff=1.0),
        description="web fetch",
        sleep=sleeper,
        uniform=lambda: 0.25,
    )

    assert result == "recovered"
    assert sleeper.delays == [0.25]


# Deadlines


async def test_deadline_lets_a_prompt_operation_through() -> None:
    async with deadline(5.0, source="arxiv"):
        await asyncio.sleep(0)


async def test_deadline_raises_our_timeout_error_not_the_builtin() -> None:
    """Callers see ``UpstreamTimeoutError``, and it carries the source."""
    with pytest.raises(UpstreamTimeoutError) as caught:
        async with deadline(0.01, source="wikipedia"):
            await asyncio.sleep(5)

    assert caught.value.code == "upstream_timeout"
    assert caught.value.source == "wikipedia"
    # Retryable, because a fetch that timed out is worth trying again.
    assert caught.value.retryable
    assert isinstance(caught.value.__cause__, TimeoutError)


async def test_deadline_measures_the_whole_block_not_each_step() -> None:
    """It bounds *total* time in the block, which is what bounds a source."""

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
    """A timeout raised by the deadline is a retryable error, so it is retried."""
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
