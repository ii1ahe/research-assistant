"""The classification boundary: ``ProviderError`` text becomes our taxonomy.

``AIService`` translates the supplied package's one coarse ``ProviderError``
into the errors the rest of the application reasons about. The split that
matters here is three-way: a misconfiguration passes its actionable text
through, a throttle is recognised — and the delay the provider names is parsed
out of the payload while it still exists — and everything else is replaced
with a generic message so provider payload can never reach a log or a user.

Kept sync and separate from ``test_ai_service.py`` because these are pure
string functions under no event loop, and that module is marked asyncio
strict. The consumption of the parsed delay is covered by
``test_resilience.py``; this module is about what the boundary produces.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Throttle classification
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# The payload does not travel
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Delay parsing
# ---------------------------------------------------------------------------


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
