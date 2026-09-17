"""The retry policy itself: bounds, backoff schedule and classification.

Kept apart from ``test_resilience.py`` because none of it needs an event loop —
the policy is pure arithmetic and one ``isinstance`` chain, and testing it
without a loop keeps the async half of the suite about concurrency. The split
is also what stops a module-level asyncio mark from landing on sync tests.
"""

from __future__ import annotations

import pytest

from researcher.errors import (
    ConfigurationError,
    ResearcherError,
    StorageError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)
from researcher.services.resilience import RetryPolicy

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_policy_rejects_fewer_than_one_attempt(max_attempts: int) -> None:
    """Zero attempts is not a policy, it is a bug: nothing would ever run."""
    with pytest.raises(ValueError, match="max_attempts must be at least 1"):
        RetryPolicy(max_attempts=max_attempts)


def test_policy_rejects_a_negative_initial_backoff() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        RetryPolicy(initial_backoff=-1.0)


def test_policy_rejects_a_max_backoff_below_the_initial_one() -> None:
    """The cap must not silently truncate the very first wait."""
    with pytest.raises(ValueError, match="must not be below"):
        RetryPolicy(initial_backoff=4.0, max_backoff=1.0)


# ---------------------------------------------------------------------------
# Backoff schedule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (5, 8.0), (6, 8.0), (50, 8.0)],
)
def test_backoff_ceiling_doubles_then_saturates(attempt: int, expected: float) -> None:
    """Exponential growth, capped — asserted across the growth *and* the plateau."""
    assert RetryPolicy(initial_backoff=0.5, max_backoff=8.0).backoff_ceiling(attempt) == expected


@pytest.mark.parametrize("attempt", [1_025, 10_000, 10**9])
def test_a_large_attempt_number_saturates_instead_of_overflowing(attempt: int) -> None:
    """``2.0 ** n`` raises ``OverflowError`` from n = 1024.

    The naive ``min(max_backoff, initial * 2 ** (attempt - 1))`` therefore
    crashes rather than returning the cap, because the clamp is never reached.
    A retry loop that has been told to be patient must not die of impatience
    with big numbers.
    """
    assert RetryPolicy(initial_backoff=0.5, max_backoff=8.0).backoff_ceiling(attempt) == 8.0


def test_a_zero_initial_backoff_stays_zero() -> None:
    """Retrying immediately is a legitimate policy and must not spin the loop."""
    policy = RetryPolicy(initial_backoff=0.0, max_backoff=8.0)
    assert policy.backoff_ceiling(1) == 0.0
    assert policy.backoff_ceiling(1_000) == 0.0


def test_a_zero_max_backoff_stays_zero() -> None:
    policy = RetryPolicy(initial_backoff=0.0, max_backoff=0.0)
    assert policy.backoff_ceiling(1) == 0.0
    assert policy.backoff_ceiling(100) == 0.0


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_retryable_errors_are_retried() -> None:
    assert RetryPolicy().should_retry(UpstreamError("boom", source="arxiv"))


def test_non_retryable_errors_are_not_retried() -> None:
    """A bad configuration cannot be repaired by asking again."""
    policy = RetryPolicy()
    assert not policy.should_retry(ConfigurationError("no key"))
    assert not policy.should_retry(StorageError("no database"))


def test_errors_outside_our_taxonomy_are_never_retried() -> None:
    """An unexpected exception is a bug, and repeating a bug is not resilience."""
    policy = RetryPolicy()
    assert not policy.should_retry(ValueError("programming error"))
    assert not policy.should_retry(KeyError("missing"))


def test_timeouts_are_retried_by_default() -> None:
    assert RetryPolicy().should_retry(UpstreamTimeoutError("slow", source="arxiv"))


def test_a_rate_limit_is_retryable_whatever_it_carries() -> None:
    """A throttle is exactly the failure another attempt is meant to recover."""
    assert RetryPolicy().should_retry(UpstreamRateLimitError("web is rate-limited", source="web"))
    assert RetryPolicy().should_retry(
        UpstreamRateLimitError(
            "synthesis is rate-limited", source="synthesis", retry_after_seconds=21.9
        )
    )


def test_timeouts_are_abandoned_when_the_policy_says_so() -> None:
    """The synthesis case: the work may still be running and billing upstream."""
    policy = RetryPolicy(retry_timeouts=False)
    assert not policy.should_retry(UpstreamTimeoutError("slow", source="synthesis"))
    # ...but the flag must not disable retrying of *other* failures.
    assert policy.should_retry(UpstreamError("boom", source="synthesis"))


@pytest.mark.parametrize(
    "error",
    [
        ConfigurationError("bad config"),
        UpstreamError("boom", source="web"),
        UpstreamRateLimitError("web is rate-limited", source="web"),
        UpstreamTimeoutError("slow", source="arxiv"),
        StorageError("no database"),
    ],
)
def test_every_error_carries_a_stable_code_and_a_retryability_flag(
    error: ResearcherError,
) -> None:
    """The code is what log records and failure records carry; it is never empty."""
    assert error.code
    assert isinstance(error.retryable, bool)
