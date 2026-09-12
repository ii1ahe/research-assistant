"""Shared fixtures for Topic 4 smoke tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ai.providers.base import LLMProvider
from ai.sources import WebSearchProvider
from ai.schemas import Source
from researcher.config import Settings


class FakeLLM(LLMProvider):
    """Returns a fixed text response. Records calls for inspection."""

    def __init__(self, response: str | None = None) -> None:
        self.response = response or (
            "Photosynthesis is the process by which plants convert light "
            "energy into chemical energy [1]. The reaction takes place in "
            "the chloroplasts and produces oxygen as a byproduct [2]."
        )
        self.calls: list[str] = []

    def complete(
        self,
        prompt: str,
        *,
        json_schema: dict | None = None,
        max_tokens: int = 1024,
    ) -> str:
        self.calls.append(prompt)
        return self.response


class FakeWebSearch(WebSearchProvider):
    """Returns canned web results without touching the network."""

    def __init__(self, results: list[Source] | None = None) -> None:
        self.results = results or [
            Source(
                title="Photosynthesis — Encyclopedia",
                url="https://example.com/photosynthesis",
                snippet="A biological process used by plants and some bacteria.",
                origin="web",
            )
        ]
        self.calls: list[str] = []

    async def search(
        self,
        query: str,
        *,
        max_results: int = 3,
        client: Any = None,
    ) -> list[Source]:
        self.calls.append(query)
        return self.results[:max_results]


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Return a factory for :class:`Settings` that is hermetic by construction.

    Two things would otherwise leak into a test from the machine running it.
    ``_env_file=None`` stops pydantic-settings reading the repository's ``.env``,
    so a developer's real credentials never reach an assertion. ``llm_model`` is
    pinned to ``None`` because the *default* is then applied rather than
    whatever ``LLM_MODEL`` happens to be exported in the shell.

    Each test names only the settings it cares about; the rest are a valid,
    unremarkable configuration.
    """

    def build(**overrides: object) -> Settings:
        base: dict[str, object] = {
            "llm_provider": "gemini",
            "llm_model": None,
            "google_api_key": "test-google-key",
            "web_search_provider": "tavily",
            "tavily_api_key": "test-tavily-key",
            "database_url": None,
            "per_source_timeout_seconds": 5.0,
        }
        return Settings(_env_file=None, **(base | overrides))  # type: ignore[arg-type]

    return build


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_web() -> FakeWebSearch:
    return FakeWebSearch()


@pytest.fixture
def sample_sources() -> list[Source]:
    return [
        Source(
            title="Photosynthesis (Wikipedia)",
            url="https://en.wikipedia.org/wiki/Photosynthesis",
            snippet="Photosynthesis is a process used by plants and other organisms "
                    "to convert light energy into chemical energy.",
            origin="wikipedia",
        ),
        Source(
            title="Calvin cycle (Wikipedia)",
            url="https://en.wikipedia.org/wiki/Calvin_cycle",
            snippet="The Calvin cycle is a series of biochemical redox reactions "
                    "in the stroma of chloroplasts.",
            origin="wikipedia",
        ),
    ]
