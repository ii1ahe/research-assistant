"""Shared fixtures for Topic 4 smoke tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from ai.providers.base import LLMProvider
from ai.schemas import AnswerWithCitations, Citation, Source
from ai.sources import WebSearchProvider
from researcher.config import Settings
from researcher.errors import StorageError
from researcher.models import (
    ProviderIdentity,
    SourceName,
    SourceOutcome,
    SourceStatus,
)


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


def make_source(name: str, n: int = 1) -> Source:
    """Build a distinct source for a given origin."""
    return Source(
        title=f"{name} result {n}",
        url=f"https://example.com/{name}/{n}",
        snippet=f"a snippet from {name}",
        origin=name,
    )


class StubAIService:
    """A stand-in for :class:`~researcher.services.ai_service.AIService`.

    ``AIService`` is a concrete class rather than a protocol, so this substitutes
    by duck typing at the one seam the orchestrator and the application service
    use: ``fetch_source`` for retrieval, ``synthesize`` for the answer, and
    ``provider_identity`` for the session snapshot. The real boundary is covered
    by ``test_ai_service.py``; what is being tested here is the *sequencing*,
    and a stub makes that visible instead of hiding it behind real retry logic.

    Retrieval answers from ``outcomes`` when the source is present there, so a
    test states only the sources it cares about; anything else succeeds with one
    source of its own, which keeps unrelated cases out of the way.
    """

    def __init__(
        self,
        *,
        outcomes: Mapping[SourceName, SourceOutcome] | None = None,
        answer: AnswerWithCitations | None = None,
        error: Exception | None = None,
    ) -> None:
        self._outcomes = dict(outcomes or {})
        self._answer = answer
        self._error = error
        self.fetched: list[tuple[SourceName, str, int | None]] = []
        self.synthesized: list[tuple[str, tuple[Source, ...]]] = []

    async def fetch_source(
        self, source: SourceName, query: str, *, max_results: int | None = None
    ) -> SourceOutcome:
        """Return the scripted outcome, or a one-source success."""
        self.fetched.append((source, query, max_results))
        if source in self._outcomes:
            return self._outcomes[source]
        return SourceOutcome(
            source=source,
            status=SourceStatus.SUCCESS,
            sources=(make_source(source.value),),
        )

    async def synthesize(self, question: str, sources: Sequence[Source]) -> AnswerWithCitations:
        """Return the scripted answer, or raise the scripted error."""
        materialised = tuple(sources)
        self.synthesized.append((question, materialised))
        if self._error is not None:
            raise self._error
        if self._answer is not None:
            return self._answer
        # Cite every source, which is what the real synthesizer does when the
        # answer uses them all.
        return AnswerWithCitations(
            question=question,
            answer=" ".join(f"Claim [{index}]." for index in range(1, len(materialised) + 1)),
            citations=[
                Citation(index=index, source=source)
                for index, source in enumerate(materialised, start=1)
            ],
        )

    @property
    def provider_identity(self) -> ProviderIdentity:
        """The stack this stub claims to be."""
        return ProviderIdentity(
            llm_provider="gemini",
            llm_model="gemini-3.8-flash",
            web_search_provider="tavily",
        )


class BrokenCache:
    """A :class:`~researcher.storage.interfaces.SourceCache` that always fails.

    Used to prove the CACHE cannot break a run. Every method raises the same
    :class:`~researcher.errors.StorageError` the PostgreSQL implementation
    would raise, so the degradation path is exercised without a database.
    """

    def __init__(self) -> None:
        self.reads = 0
        self.writes = 0

    async def get(self, key: Any, *, now: Any = None) -> Any:
        """Fail."""
        self.reads += 1
        raise StorageError("cache unavailable", source="storage")

    async def put(self, entry: Any) -> None:
        """Fail."""
        self.writes += 1
        raise StorageError("cache unavailable", source="storage")

    async def purge_expired(self, *, now: Any = None) -> int:
        """Fail."""
        raise StorageError("cache unavailable", source="storage")


@pytest.fixture
def stub_ai() -> StubAIService:
    return StubAIService()


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
