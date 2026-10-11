"""Checks how provider errors and retry hints become app errors without exposing raw payloads."""

from __future__ import annotations

import httpx
import pytest

from ai.providers.base import ProviderError
from researcher.errors import ConfigurationError, UpstreamError, UpstreamRateLimitError
from researcher.services.ai_service import _retry_after_hint, _translate

#: The payload measured in the Phase 8 container (docs/security.md §C), which
#: started this whole fix. Abridged, as the provider printed it.
_GEMINI_THROTTLE = (
    "Gemini call failed: 429 RESOURCE_EXHAUSTED. limit: 20, model: "
    "gemini-3.8-flash. Please retry in 21.9s"
)


class _StatusError(Exception):
    """The shape a provider SDK's status error has: an exception with a response."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"status {response.status_code}")
        self.response = response


def _provider_error(message: str, *, retry_after: str | None = None) -> ProviderError:
    """Build what ``ai.providers.*`` raises: a ``ProviderError`` caused by the SDK's."""
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    try:
        raise _StatusError(httpx.Response(429, headers=headers))
    except _StatusError as cause:
        error = ProviderError(message)
        error.__cause__ = cause
        return error


# Throttle classification


def test_a_throttle_that_names_a_delay_carries_it() -> None:
    error = _translate(ProviderError(_GEMINI_THROTTLE), source="synthesis")

    assert isinstance(error, UpstreamRateLimitError)
    assert error.retry_after_seconds == 21.9
    assert error.code == "upstream_rate_limit"
    assert error.source == "synthesis"
    assert error.retryable


def test_a_throttle_that_names_no_delay_is_still_a_rate_limit() -> None:
    """The category matters even when the hint does not parse: the policy may
    not recover the lost time, but the diagnostics now name the real cause."""
    error = _translate(
        ProviderError("Gemini call failed: 429 Too Many Requests"), source="synthesis"
    )

    assert isinstance(error, UpstreamRateLimitError)
    assert error.retry_after_seconds is None
    assert error.retryable


def test_a_rate_limited_source_fetch_is_classified_the_same_way() -> None:
    """``_translate`` serves both doors, so a throttled web search carries the
    hint into its outcome rather than being flattened to a generic error."""
    error = _translate(ProviderError("Tavily search failed: 429. Please retry in 5s"), source="web")

    assert isinstance(error, UpstreamRateLimitError)
    assert error.retry_after_seconds == 5.0
    assert error.source == "web"


# The payload does not travel


def test_the_provider_payload_never_reaches_the_message() -> None:
    """The hint is a parsed number; the text it came from stays in ``__cause__``."""
    error = _translate(ProviderError(_GEMINI_THROTTLE), source="synthesis")

    for fragment in ("RESOURCE_EXHAUSTED", "limit: 20", "gemini-3.8-flash", "429"):
        assert fragment not in error.message
    # ...but the *number* is safe to name, so the user knows what is happening.
    assert "21.9" in error.message


def test_a_plain_failure_is_still_a_generic_upstream_error() -> None:
    """Anything without a throttle marker is replaced, not quoted."""
    error = _translate(ProviderError("Tavily search failed: boom"), source="web")

    assert isinstance(error, UpstreamError)
    assert not isinstance(error, UpstreamRateLimitError)
    assert "boom" not in error.message


def test_a_misconfiguration_is_classified_before_throttling() -> None:
    """Marker precedence: a missing key is permanent no matter what else the
    text says, so it must not become a retryable throttle."""
    error = _translate(
        ProviderError("GOOGLE_API_KEY (or LLM_API_KEY) is not set."), source="synthesis"
    )

    assert isinstance(error, ConfigurationError)
    assert not error.retryable


# Delay parsing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Please retry in 21.9s", 21.9),
        ("please RETRY IN 30s", 30.0),
        ("retry in 1.5 seconds", 1.5),
        ("retry in 60 sec", 60.0),
        ("Retry-After: 45", 45.0),
        ("retry-after: 60s", 60.0),
    ],
)
def test_the_delay_is_parsed_in_every_shape_a_provider_might_name(
    text: str, expected: float
) -> None:
    assert _retry_after_hint(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "request took 3.2s",  # a duration, not an instruction
        "429 Too Many Requests",  # a throttle with no named delay
        "retry in 45 minutes",  # units we would misread as seconds
        "",  # nothing at all
    ],
)
def test_text_that_is_not_an_instruction_produces_no_hint(text: str) -> None:
    """Narrow matching: without a hint the policy still retries, on its own
    backoff, which is the correct behaviour for a value we cannot be sure of."""
    assert _retry_after_hint(text) is None


# The HTTP header


def test_a_retry_after_header_is_honoured_when_the_text_names_no_delay() -> None:
    """A header is machine-readable; the text is prose we pattern-match."""
    error = _translate(
        _provider_error("Gemini call failed: 429 Too Many Requests", retry_after="21.9"),
        source="synthesis",
    )

    assert isinstance(error, UpstreamRateLimitError)
    assert error.retry_after_seconds == 21.9
    assert "21.9" in error.message


def test_a_header_makes_a_throttle_the_text_never_named() -> None:
    """Without the header this is an unclassified failure carrying no delay."""
    error = _translate(
        _provider_error("Gemini call failed: request rejected", retry_after="12"),
        source="synthesis",
    )

    assert isinstance(error, UpstreamRateLimitError)
    assert error.retry_after_seconds == 12.0


def test_the_text_hint_keeps_precedence_over_the_header() -> None:
    """The existing behaviour is preserved, not replaced."""
    error = _translate(
        _provider_error("Gemini call failed: 429. Please retry in 5s", retry_after="30"),
        source="synthesis",
    )

    assert error.retry_after_seconds == 5.0


def test_an_http_date_header_is_deliberately_not_read() -> None:
    """A date is only as trustworthy as the clock that reads it."""
    error = _translate(
        _provider_error("Gemini call failed: 429", retry_after="Wed, 21 Oct 2026 07:28:00 GMT"),
        source="synthesis",
    )

    assert isinstance(error, UpstreamRateLimitError)
    assert error.retry_after_seconds is None


@pytest.mark.parametrize("raw", ["", "soon", "-5", "nan", "inf", "1e999"])
def test_a_header_that_is_not_a_usable_delay_is_discarded(raw: str) -> None:
    error = _translate(
        _provider_error("Gemini call failed: 429", retry_after=raw), source="synthesis"
    )

    assert isinstance(error, UpstreamRateLimitError)
    assert error.retry_after_seconds is None


def test_a_header_on_a_misconfiguration_changes_nothing() -> None:
    """Marker precedence survives the new source of delays: a permanent fault
    must not become retryable because a response happened to carry a header."""
    error = _translate(
        _provider_error("GOOGLE_API_KEY is not set.", retry_after="30"), source="synthesis"
    )

    assert isinstance(error, ConfigurationError)
    assert not error.retryable


def test_a_broken_cause_chain_does_not_break_translation() -> None:
    """Introspection is best-effort, and the boundary must still classify."""

    class HostileError(Exception):
        """Every attribute access is a landmine."""

        def __getattr__(self, name: str) -> object:
            raise RuntimeError(f"no {name} for you")

    error = ProviderError("Gemini call failed: 429")
    error.__cause__ = HostileError("boom")

    translated = _translate(error, source="synthesis")

    assert isinstance(translated, UpstreamRateLimitError)
    assert translated.retry_after_seconds is None
