"""Typed, validated application settings.

Settings are read from the process environment, falling back to a ``.env`` file
in the working directory. They are validated once, at startup, so a
misconfiguration surfaces immediately as a clear message and exit status 2
rather than as a confusing provider error halfway through a request.

Integration with the supplied ``ai`` package
--------------------------------------------
``ai.providers.factory.get_llm()`` and the ``Provider`` classes read
``os.environ`` directly, and the factories accept no arguments. This module
therefore does **not** mutate ``os.environ``: it exposes the validated values
plus the resolution helpers below (:attr:`resolved_llm_api_key`,
:attr:`effective_llm_model`, :attr:`web_search_api_key`) so the ``ai_service``
boundary can construct provider objects explicitly and inject them. That keeps
one authoritative source of configuration, makes the model recorded in a
session snapshot provably the model that answered, and makes tests injectable
without patching the environment.

The provider-name aliases accepted here mirror the supplied factories exactly
(``google`` -> ``gemini``, ``ddg`` -> ``duckduckgo``), and the per-provider
model defaults mirror the defaults in ``ai/providers/*.py``.

Security
--------
Validation messages deliberately report only field names and failure reasons.
Pydantic's machine-readable ``errors()`` entries carry the offending input
value, and those values are API keys, so the raw entries are never included in
an error message.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from researcher.errors import ConfigurationError

__all__ = [
    "LLMProviderName",
    "Settings",
    "WebSearchProviderName",
    "get_settings",
    "model_family",
]

#: Providers the supplied ``ai.providers.factory`` can build. ``google`` is
#: accepted as an alias of ``gemini`` and normalised to ``gemini`` here.
LLMProviderName = Literal["anthropic", "openai", "gemini"]

#: Web-search providers the supplied ``ai.sources`` can build. ``ddg`` is
#: accepted as an alias of ``duckduckgo`` and normalised here.
WebSearchProviderName = Literal["tavily", "serper", "duckduckgo"]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
WikipediaSearch = Literal["fulltext", "opensearch"]

#: Per-provider default model ids.
#:
#: For ``anthropic`` and ``openai`` these mirror the fallbacks the supplied
#: provider classes apply when ``LLM_MODEL`` is unset, so
#: :attr:`Settings.effective_llm_model` describes the model that will actually
#: answer.
#:
#: ``gemini`` deliberately does *not* mirror the supplied code. That class still
#: falls back to ``gemini-2.0-flash``, which the API now rejects with
#: ``404 NOT_FOUND`` — the model has been retired. Mirroring a retired id
#: faithfully would make the application fail on its first live call, so this
#: diverges on purpose, and the divergence is recorded rather than silent.
#: ``ai/`` is supplied code and not ours to correct.
#:
#: Verified against the live API: ``gemini-2.0-flash`` -> 404, and
#: ``gemini-2.5-flash`` -> 404 ("no longer available to new users"), so 2.x is
#: not a usable target at all. ``gemini-3.8-flash`` was confirmed working
#: end-to-end through ``ai.synthesize``.
_DEFAULT_LLM_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-3.8-flash",
}

#: Recognisable model-name prefixes, used only to catch the shared-``LLM_MODEL``
#: trap: one variable feeds every provider, so switching ``LLM_PROVIDER``
#: without switching ``LLM_MODEL`` pairs, say, a Claude id with OpenAI.
_MODEL_PREFIXES: dict[LLMProviderName, tuple[str, ...]] = {
    "anthropic": ("claude",),
    "openai": ("gpt", "o1", "o3", "o4", "chatgpt"),
    "gemini": ("gemini", "models/gemini"),
}


def model_family(model: str) -> LLMProviderName | None:
    """Infer which provider a model id belongs to.

    Returns ``None`` for ids that match no known family, such as aliases served
    by a proxy or gateway. Those are permitted rather than rejected, because a
    custom name is not evidence of a mistake.
    """
    candidate = model.strip().lower()
    for family, prefixes in _MODEL_PREFIXES.items():
        if candidate.startswith(prefixes):
            return family
    return None


class Settings(BaseSettings):
    """Validated application settings.

    Instances are immutable. Build one through :func:`get_settings`, which
    caches the result and translates validation failures into
    :class:`~researcher.errors.ConfigurationError`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # An empty value in `.env` (`TAVILY_API_KEY=`) means "not configured".
        # Without this, it would arrive as `''` — falsy but not `None` — and
        # every `is None` check downstream would wrongly conclude a credential
        # is present. `.env.example` ships with these keys blank, so this case
        # is the normal path, not an edge case.
        env_ignore_empty=True,
        # A `.env` may legitimately hold variables for other tools (editor
        # settings, shell config). Unknown keys are not our business.
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    # --- LLM provider ------------------------------------------------------
    llm_provider: LLMProviderName = "anthropic"
    llm_model: str | None = None
    #: Generic credential honoured by every provider as a fallback, matching
    #: the lookup chains in ``ai/providers/*.py``.
    llm_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None
    gemini_api_key: str | None = None

    # --- Web search provider ----------------------------------------------
    web_search_provider: WebSearchProviderName = "tavily"
    tavily_api_key: str | None = None
    serper_api_key: str | None = None

    # --- Application -------------------------------------------------------
    #: There is deliberately no ``CACHE_DIR``: ADR-002 stores cached retrieval
    #: results in PostgreSQL alongside sessions, so there is no filesystem
    #: cache to point at. A setting nothing reads is a setting that lies.
    log_level: LogLevel = "INFO"
    cache_ttl_seconds: int = Field(default=86400, ge=0)
    per_source_timeout_seconds: float = Field(default=10.0, gt=0)
    #: The deadline for one synthesis, retries included. Deliberately separate
    #: from ``per_source_timeout_seconds``, because the two bound different
    #: things. A search request that has not answered in ten seconds is not
    #: going to, and abandoning it costs one round trip; an LLM completion
    #: legitimately takes tens of seconds, and abandoning it throws away work
    #: that was nearly finished.
    #:
    #: Sharing one knob made that distinction invisible. Raising the fetch
    #: budget to accommodate a slow search provider silently bought synthesis
    #: time as well, and lowering it to fail fast on a dead host silently
    #: started killing synthesis. Phase 7 found the cost: measured against the
    #: configured Gemini model, synthesis ran at 2.9s, 3.9s, 6.1s and 7.6s on a
    #: quiet run and then hit the ten-second ceiling — roughly one synthesis in
    #: six refused, on a machine doing nothing else.
    synthesis_timeout_seconds: float = Field(default=30.0, gt=0)
    max_results_per_source: int = Field(default=3, ge=1, le=20)
    max_parallel_sources: int = Field(default=3, ge=1, le=16)
    max_question_length: int = Field(default=500, ge=1)
    #: Which Wikipedia search the application uses. ``fulltext`` sends the
    #: question to the MediaWiki search API; ``opensearch`` uses the supplied
    #: ``ai.sources.fetch_wikipedia``. The default is ``fulltext`` because the
    #: supplied fetcher prefix-matches the whole query against article *titles*,
    #: so it answers a natural-language question with nothing — see
    #: ``researcher/services/wikipedia.py``. ``opensearch`` is kept selectable
    #: so the supplied path stays reachable and testable.
    wikipedia_search: WikipediaSearch = "fulltext"

    # --- Persistence -------------------------------------------------------
    #: PostgreSQL DSN. When unset, sessions are not persisted and the status is
    #: reported as ``skipped`` rather than as a failure.
    database_url: str | None = None
    #: Set false to skip session writes; a configured cache still uses the database.
    persist_sessions: bool = True

    # --- Normalisation -----------------------------------------------------
    @field_validator("llm_provider", mode="before")
    @classmethod
    def _normalise_llm_provider(cls, value: object) -> object:
        if isinstance(value, str):
            normalised = value.strip().lower()
            return "gemini" if normalised == "google" else normalised
        return value

    @field_validator("web_search_provider", mode="before")
    @classmethod
    def _normalise_web_provider(cls, value: object) -> object:
        if isinstance(value, str):
            normalised = value.strip().lower()
            return "duckduckgo" if normalised == "ddg" else normalised
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator(
        "llm_model",
        "llm_api_key",
        "anthropic_api_key",
        "openai_api_key",
        "google_api_key",
        "gemini_api_key",
        "tavily_api_key",
        "serper_api_key",
        "database_url",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        # `env_ignore_empty` covers the environment and `.env`, but a value set
        # programmatically (or via a shell that exports an empty string) can
        # still arrive blank. Treat blank as absent everywhere.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # --- Cross-field checks ------------------------------------------------
    @model_validator(mode="after")
    def _model_matches_provider(self) -> Settings:
        if self.llm_model is None:
            return self
        family = model_family(self.llm_model)
        if family is not None and family != self.llm_provider:
            raise ValueError(
                f"LLM_MODEL={self.llm_model!r} looks like a {family} model id but "
                f"LLM_PROVIDER is {self.llm_provider!r}. LLM_MODEL is shared by "
                "every provider, so it must be changed whenever LLM_PROVIDER is."
            )
        return self

    # --- Resolution helpers ------------------------------------------------
    @property
    def effective_llm_model(self) -> str:
        """The model id that will actually answer.

        Falls back to the same per-provider default the supplied provider class
        would apply, so this is safe to record in a session snapshot.
        """
        return self.llm_model or _DEFAULT_LLM_MODELS[self.llm_provider]

    @property
    def resolved_llm_api_key(self) -> str | None:
        """The credential the configured LLM provider will use.

        Mirrors the fallback chain inside each supplied provider class.
        """
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key or self.llm_api_key
        if self.llm_provider == "openai":
            return self.openai_api_key or self.llm_api_key
        return self.google_api_key or self.gemini_api_key or self.llm_api_key

    @property
    def web_search_api_key(self) -> str | None:
        """The credential the configured web-search provider will use.

        ``None`` is not an error here: DuckDuckGo needs no credential, which is
        why it is the recommended provider for a key-free demo run.
        """
        if self.web_search_provider == "tavily":
            return self.tavily_api_key
        if self.web_search_provider == "serper":
            return self.serper_api_key
        return None

    @property
    def persistence_enabled(self) -> bool:
        """Whether completed sessions should be written to the database."""
        return self.persist_sessions and self.database_url is not None


def _describe(exc: ValidationError) -> str:
    """Summarise a validation failure without echoing configured values.

    Pydantic attaches the offending input to each error entry; those inputs are
    routinely credentials, so only the location and the reason are reported.
    """
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use.

    Raises:
        ConfigurationError: The environment contains an invalid value, or two
            values contradict each other. The message names the offending
            fields but never their contents.

    Call ``get_settings.cache_clear()`` to force a reload, which is what tests
    do after changing the environment.
    """
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration — {_describe(exc)}") from exc
