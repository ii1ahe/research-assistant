"""Shared fixtures for Topic 4 smoke tests."""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import timedelta
from typing import Any

import pytest

from ai.providers.base import LLMProvider
from ai.schemas import AnswerWithCitations, Citation, Source
from ai.sources import WebSearchProvider
from researcher.config import Settings
from researcher.errors import StorageError
from researcher.models import (
    PersistenceStatus,
    ProviderIdentity,
    ResearchRequest,
    ResearchResult,
    ResultStatus,
    SourceName,
    SourceOutcome,
    SourceStatus,
    TimingInfo,
    utc_now,
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


def make_success(
    source: SourceName = SourceName.WIKIPEDIA,
    *,
    count: int = 1,
    elapsed_seconds: float = 0.4,
    cache_hit: bool = False,
) -> SourceOutcome:
    """Build a successful outcome carrying ``count`` sources."""
    return SourceOutcome(
        source=source,
        status=SourceStatus.SUCCESS,
        sources=tuple(make_source(source.value, n) for n in range(1, count + 1)),
        elapsed_seconds=elapsed_seconds,
        attempts=0 if cache_hit else 1,
        cache_hit=cache_hit,
    )


def make_result(
    *,
    question: str = "what is photosynthesis",
    status: ResultStatus = ResultStatus.SUCCESS,
    answer: AnswerWithCitations | None = None,
    outcomes: Sequence[SourceOutcome] = (),
    warnings: Sequence[str] = (),
    persistence: PersistenceStatus = PersistenceStatus.SKIPPED,
) -> ResearchResult:
    """Build a :class:`ResearchResult` that satisfies its own answer/status rule.

    ``ResearchResult`` refuses an answer whose presence disagrees with the
    status, so a caller that wants to exercise the *rendering* of a failure
    cannot simply pass ``answer=None`` and ``status=SUCCESS``. The answer is
    therefore derived from the status when one is not supplied, which keeps each
    test free to state only the field it is about.
    """
    produced = status in (ResultStatus.SUCCESS, ResultStatus.PARTIAL)
    if produced and answer is None:
        cited = [outcome.sources[0] for outcome in outcomes if outcome.sources] or [
            make_source("wikipedia")
        ]
        answer = AnswerWithCitations(
            question=question,
            answer=" ".join(f"A claim [{index}]." for index in range(1, len(cited) + 1)),
            citations=[
                Citation(index=index, source=source)
                for index, source in enumerate(cited, start=1)
            ],
        )
    elif not produced:
        answer = None

    started = utc_now()
    return ResearchResult(
        request_id=uuid.uuid4(),
        question=question,
        status=status,
        answer=answer,
        outcomes=tuple(outcomes),
        warnings=tuple(warnings),
        timing=TimingInfo(
            started_at=started,
            finished_at=started + timedelta(seconds=1.0),
            retrieval_seconds=0.6,
            synthesis_seconds=0.4 if produced else 0.0,
        ),
        persistence=persistence,
    )


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
            "synthesis_timeout_seconds": 5.0,
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


#: Hosts a test may reach: the local machine. The PostgreSQL integration tests
#: in ``test_storage.py`` talk to a real server on loopback, and loopback still
#: works with the cable pulled, so allowing it keeps the offline guarantee
#: meaningful instead of merely strict.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", ""})


@pytest.fixture(autouse=True)
def no_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to reach off this machine.

    The suite is documented as passing with the network cable pulled, and that
    was true only by discipline: a test that patched the wrong seam, or a
    default that changed under it, would quietly reach the live internet and
    pass anyway. That is the worst kind of test — it is slow, it is flaky, and
    it reports success for behaviour nobody mocked. This happened once, when the
    Wikipedia fetcher gained a second implementation: five tests kept passing by
    calling Wikipedia for real, and the only visible symptom was a run that took
    two seconds longer.

    Patching ``connect`` rather than ``getaddrinfo`` so the failure is raised at
    the line that needs fixing. Mocked transports never reach it — ``respx``
    replaces the transport, so the HTTP layer needs no patch of its own.
    """
    real_connect = socket.socket.connect

    def guard(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(address, tuple) and host in _LOOPBACK:
            return real_connect(self, address)
        raise RuntimeError(
            f"the test suite tried to reach the network at {address!r}. Mock it "
            "with respx or patch the fetcher instead — the suite must run offline."
        )

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def restore_researcher_logger() -> Iterator[None]:
    """Put the ``researcher`` logger back exactly as it was.

    :func:`researcher.bootstrap.configure_logging` mutates process-global state:
    it installs a handler, sets a level and — the dangerous one — disables
    ``propagate``. Left in place, that would silence ``caplog`` for every test
    that ran afterwards, so the suite would pass or fail depending on file
    order. Requested explicitly by the two modules that call it rather than
    applied globally, because a fixture that changes nothing should not be
    invisible everywhere else.
    """
    logger = logging.getLogger("researcher")
    handlers = list(logger.handlers)
    level, propagate = logger.level, logger.propagate
    yield

    for handler in list(logger.handlers):
        if handler not in handlers:
            logger.removeHandler(handler)
            handler.close()
    for handler in handlers:
        if handler not in logger.handlers:
            logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = propagate


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
