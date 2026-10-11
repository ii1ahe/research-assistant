"""Reads and checks environment settings, resolves provider keys and model names, and sets
timeouts and result limits.
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

# Accepted LLM providers; google is mapped to gemini.
LLMProviderName = Literal["anthropic", "openai", "gemini"]

# Accepted web providers; ddg is mapped to duckduckgo.
WebSearchProviderName = Literal["tavily", "serper", "duckduckgo"]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
WikipediaSearch = Literal["fulltext", "opensearch"]

# Default model IDs. The Gemini default differs from the supplied package.
# The September 18, 2026 project runs used gemini-3.1-flash-lite after older
# models were rejected or timed out; details are in README.md.
_DEFAULT_LLM_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-3.1-flash-lite",
}

# Catch a model ID left over from a different provider.
_MODEL_PREFIXES: dict[LLMProviderName, tuple[str, ...]] = {
    "anthropic": ("claude",),
    "openai": ("gpt", "o1", "o3", "o4", "chatgpt"),
    "gemini": ("gemini", "models/gemini"),
}


def model_family(model: str) -> LLMProviderName | None:
    """Infer which provider a model id belongs to."""
    candidate = model.strip().lower()
    for family, prefixes in _MODEL_PREFIXES.items():
        if candidate.startswith(prefixes):
            return family
    return None


class Settings(BaseSettings):
    """Validated application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Treat blank environment values as missing credentials.
        env_ignore_empty=True,
        # Allow .env to contain settings for other tools.
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

    # Application settings. Cached sources are stored in PostgreSQL, not files.
    log_level: LogLevel = "INFO"
    cache_ttl_seconds: int = Field(default=86400, ge=0)
    per_source_timeout_seconds: float = Field(default=10.0, gt=0)
    # Answer generation has its own deadline because it can take longer than a fetch.
    # This includes retries and is separate from per_source_timeout_seconds.
    synthesis_timeout_seconds: float = Field(default=30.0, gt=0)
    max_results_per_source: int = Field(default=3, ge=1, le=20)
    max_parallel_sources: int = Field(default=3, ge=1, le=16)
    max_question_length: int = Field(default=500, ge=1)
    # Use full-text search for questions. Keep opensearch selectable for the supplied path.
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
        # Treat blank values passed directly to Settings as missing too.
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
        """The credential the configured web-search provider will use."""
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
    """Summarise a validation failure without echoing configured values."""
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
    """
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration — {_describe(exc)}") from exc
