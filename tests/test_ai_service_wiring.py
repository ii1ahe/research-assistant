"""Getting credentials into the supplied providers.

Separated from ``test_ai_service.py`` because none of it needs an event loop:
this is construction and argument-passing, and keeping it apart stops a
module-level asyncio mark from landing on sync tests.

The subject is narrow and load-bearing. The supplied providers read
``os.getenv`` and nothing loads ``.env`` into the process environment, so a
:class:`~researcher.config.Settings` object gets a provider nowhere on its own.
Constructing each provider with an explicit ``api_key`` and ``model`` is the
only path by which a configured credential arrives — which is why these tests
assert on constructor *arguments* rather than on a stubbed-out provider's
behaviour. A test that replaced the provider wholesale would still pass with
that line deleted.

The private ``_llm_provider`` / ``_web_provider`` seam is what is called here,
deliberately: it is the function that performs the hand-off, and reaching it
through a fetch would mean a network call to observe an argument.
"""

from __future__ import annotations

import pytest

from researcher.errors import ConfigurationError
from researcher.services.ai_service import AIService

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_a_missing_llm_credential_fails_at_construction(make_settings) -> None:
    """Every run ends in synthesis, so a missing key makes retrieval pointless.

    Failing here turns a late, confusing provider error into an immediate one
    that names the variable to set.
    """
    with pytest.raises(ConfigurationError, match="no API key configured"):
        AIService(make_settings(google_api_key=None))


def test_the_error_message_names_the_provider_without_echoing_the_key(make_settings) -> None:
    with pytest.raises(ConfigurationError) as caught:
        AIService(make_settings(google_api_key=None))

    assert "gemini" in caught.value.message
    assert caught.value.source == "synthesis"


def test_a_missing_web_credential_does_not_fail_at_construction(make_settings) -> None:
    """Someone running --sources wiki,arxiv has no reason to hold a Tavily key."""
    service = AIService(make_settings(tavily_api_key=None))
    assert service.provider_identity.web_search_provider == "tavily"


def test_the_provider_identity_reports_what_will_actually_run(make_settings) -> None:
    """The session snapshot records this, so it must reflect the resolved model.

    An unset ``LLM_MODEL`` falls back to the provider's default, and reporting
    the empty string instead of that default would make a stored session
    unreproducible.
    """
    identity = AIService(make_settings(llm_model=None)).provider_identity

    assert identity.llm_provider == "gemini"
    assert identity.llm_model
    assert identity.web_search_provider == "tavily"


# ---------------------------------------------------------------------------
# The LLM bridge
# ---------------------------------------------------------------------------


def test_the_llm_provider_receives_the_configured_model_and_key(
    make_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole bridge, asserted on the arguments that cross it."""
    captured: dict[str, object] = {}

    class FakeGemini:
        def __init__(self, model: str | None = None, *, api_key: str | None = None) -> None:
            captured["model"] = model
            captured["api_key"] = api_key

    monkeypatch.setattr("ai.providers.google.GeminiLLM", FakeGemini)

    service = AIService(make_settings(llm_model="gemini-3.8-flash", google_api_key="bridge-me"))
    service._llm_provider()

    assert captured == {"model": "gemini-3.8-flash", "api_key": "bridge-me"}


@pytest.mark.parametrize(
    ("provider", "module_path", "key_field"),
    [
        ("anthropic", "ai.providers.anthropic.AnthropicLLM", "anthropic_api_key"),
        ("openai", "ai.providers.openai.OpenAILLM", "openai_api_key"),
    ],
)
def test_the_other_llm_providers_are_reached_the_same_way(
    make_settings, monkeypatch: pytest.MonkeyPatch, provider: str, module_path: str, key_field: str
) -> None:
    """All three suppliers are wired identically; only the import path differs."""
    captured: dict[str, object] = {}

    class FakeLLM:
        def __init__(self, model: str | None = None, *, api_key: str | None = None) -> None:
            captured["model"] = model
            captured["api_key"] = api_key

    monkeypatch.setattr(module_path, FakeLLM)

    service = AIService(make_settings(llm_provider=provider, **{key_field: "other-key"}))
    service._llm_provider()

    assert captured["api_key"] == "other-key"


def test_the_llm_provider_is_built_once(make_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Providers hold a client each; rebuilding per call would leak them."""
    builds = 0

    class FakeGemini:
        def __init__(self, model: str | None = None, *, api_key: str | None = None) -> None:
            nonlocal builds
            builds += 1

    monkeypatch.setattr("ai.providers.google.GeminiLLM", FakeGemini)

    service = AIService(make_settings())
    service._llm_provider()
    service._llm_provider()

    assert builds == 1


# ---------------------------------------------------------------------------
# The web-search bridge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "module_path", "key_field"),
    [
        ("tavily", "ai.sources.TavilyProvider", "tavily_api_key"),
        ("serper", "ai.sources.SerperProvider", "serper_api_key"),
    ],
)
def test_the_web_provider_receives_the_configured_key(
    make_settings, monkeypatch: pytest.MonkeyPatch, provider: str, module_path: str, key_field: str
) -> None:
    captured: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, api_key: str | None = None, *, timeout: float = 10.0) -> None:
            captured["api_key"] = api_key
            captured["timeout"] = timeout

    monkeypatch.setattr(module_path, FakeProvider)

    service = AIService(make_settings(web_search_provider=provider, **{key_field: "search-key"}))
    service._web_provider()

    assert captured["api_key"] == "search-key"
    # The provider's own timeout is aligned with the source deadline rather than
    # left at whatever the supplied package defaults to, so the two cannot
    # disagree about how long a web search may take.
    assert captured["timeout"] == 5.0


def test_duckduckgo_needs_no_key(make_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one provider that always works, which is why it is the demo default."""
    built: list[object] = []

    class FakeDdg:
        def __init__(self) -> None:
            built.append(self)

    monkeypatch.setattr("ai.sources.DuckDuckGoProvider", FakeDdg)

    service = AIService(make_settings(web_search_provider="duckduckgo", tavily_api_key=None))

    assert service._web_provider() is built[0]


def test_the_web_provider_is_built_once(make_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    builds = 0

    class FakeProvider:
        def __init__(self, api_key: str | None = None, *, timeout: float = 10.0) -> None:
            nonlocal builds
            builds += 1

    monkeypatch.setattr("ai.sources.TavilyProvider", FakeProvider)

    service = AIService(make_settings())
    service._web_provider()
    service._web_provider()

    assert builds == 1
