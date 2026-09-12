"""The application layer: what one request does, and what each failure costs.

The service is the only place that knows the order of the work, so these tests
are written as claims about that order rather than as claims about the pieces.

1. *Bad input costs nothing.* An unacceptable request or configuration must
   fail before a single provider is contacted, because that is exit status 2 —
   the user has to change something, and retrying spends quota to learn nothing.
2. *A bad run still produces a result.* Retrieval degrading, or synthesis
   failing outright, is reported inside a :class:`ResearchResult` rather than
   raised. A run where all three sources died is still a run that happened, and
   it is persisted, which is what makes it diagnosable afterwards.
3. *One exception to both.* A rejected credential at synthesis time is a
   property of the environment, not of this run, so it propagates.

Storage is the in-memory double from ADR-004 and the ``AIService`` is the stub
from ``conftest``; the real boundary is covered by ``test_ai_service.py``. What
is tested here is sequencing, and a stub makes that visible.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from ai.schemas import AnswerWithCitations, Citation
from researcher.config import Settings
from researcher.core.researcher import ResearchService
from researcher.errors import (
    ConfigurationError,
    InvalidRequestError,
    StorageError,
    UpstreamError,
)
from researcher.models import (
    FailureDetail,
    PersistenceStatus,
    ResearchRequest,
    ResearchSession,
    ResultStatus,
    SourceName,
    SourceOutcome,
    SourceStatus,
)
from researcher.services.cache import CacheService
from researcher.services.orchestrator import Orchestrator
from researcher.storage.memory import InMemorySourceCache, InMemoryStorage
from tests.conftest import StubAIService, make_source

pytestmark = pytest.mark.asyncio

SettingsFactory = Callable[..., Settings]

#: A DSN that is never dialled. ``persistence_enabled`` is derived, not stored:
#: it is ``persist_sessions and database_url is not None``. Storage here is the
#: in-memory double, so the DSN's only job is to be present.
_DSN = "postgresql://researcher:researcher@localhost:5432/researcher"


def _request(
    question: str = "what is photosynthesis",
    *sources: SourceName,
    use_cache: bool = True,
    max_results: int = 3,
) -> ResearchRequest:
    return ResearchRequest(
        question=question,
        sources=tuple(sources) or (SourceName.WIKIPEDIA, SourceName.ARXIV),
        use_cache=use_cache,
        max_results=max_results,
    )


def _service(
    settings: Settings,
    ai: Any,
    *,
    storage: Any = None,
    cache: Any = None,
) -> ResearchService:
    """Wire the real orchestrator and cache around a stubbed ``AIService``.

    The service's collaborators are real rather than stubbed because the
    interesting behaviour here lives in how they are sequenced; a stub
    orchestrator would let a sequencing bug pass by simply agreeing with it.
    """
    orchestrator = Orchestrator(
        ai, CacheService(cache or InMemorySourceCache(), settings), settings
    )
    return ResearchService(ai=ai, orchestrator=orchestrator, settings=settings, storage=storage)


def _failed(source: SourceName, code: str = "upstream_error") -> SourceOutcome:
    return SourceOutcome(
        source=source,
        status=SourceStatus.ERROR,
        failure=FailureDetail(code=code, message="the provider refused the connection"),
    )


def _empty(source: SourceName) -> SourceOutcome:
    return SourceOutcome(source=source, status=SourceStatus.EMPTY)


class BrokenSessions:
    """A session store whose every method fails, as PostgreSQL's would."""

    def __init__(self) -> None:
        self.calls = 0

    async def save(self, session: ResearchSession) -> None:
        """Fail."""
        self.calls += 1
        raise StorageError("the database is unreachable", source="storage")

    async def get(self, session_id: Any) -> Any:
        """Fail."""
        raise StorageError("the database is unreachable", source="storage")

    async def list_recent(self, *, limit: int = 20) -> Any:
        """Fail."""
        raise StorageError("the database is unreachable", source="storage")


class BrokenStorage:
    """A :class:`Storage` whose cache works and whose session writes do not."""

    def __init__(self) -> None:
        self._cache = InMemorySourceCache()
        self._sessions = BrokenSessions()

    @property
    def cache(self) -> InMemorySourceCache:
        """A working cache, so only the session write under test can fail."""
        return self._cache

    @property
    def sessions(self) -> BrokenSessions:
        """The failing session store."""
        return self._sessions

    async def aclose(self) -> None:
        """Release nothing."""


# ---------------------------------------------------------------------------
# A run that works
# ---------------------------------------------------------------------------


async def test_a_clean_run_succeeds(make_settings: SettingsFactory) -> None:
    service = _service(make_settings(), StubAIService())

    result = await service.research(_request())

    assert result.status is ResultStatus.SUCCESS
    assert result.answer is not None
    assert result.warnings == ()
    assert [outcome.status for outcome in result.outcomes] == [
        SourceStatus.SUCCESS,
        SourceStatus.SUCCESS,
    ]


async def test_the_answer_is_synthesised_from_the_retrieved_sources(
    make_settings: SettingsFactory,
) -> None:
    """The synthesizer sees exactly what the citations will point at."""
    ai = StubAIService()
    service = _service(make_settings(), ai)

    result = await service.research(_request())

    assert len(ai.synthesized) == 1
    question, sources = ai.synthesized[0]
    assert question == "what is photosynthesis"
    assert [source.origin for source in sources] == ["wikipedia", "arxiv"]
    # Every retrieved source is cited, and the citation numbers line up with
    # the positions the sources occupied in the list the synthesizer was given.
    assert result.answer is not None
    assert [citation.source for citation in result.answer.citations] == list(sources)
    assert [citation.index for citation in result.answer.citations] == [1, 2]


async def test_the_original_question_is_never_replaced_by_its_canonical_form(
    make_settings: SettingsFactory,
) -> None:
    """Canonicalisation is cache-key material, and must not leak into the run.

    The question the user typed is what gets searched, what gets answered, and
    what gets reported back; only the cache is allowed to see the normalised
    form. Passed to the provider in lower case, a question loses its proper
    nouns — which is a silently worse answer, not a failed one.
    """
    ai = StubAIService()
    service = _service(make_settings(), ai)
    asked = "  What IS Photosynthesis?  "

    result = await service.research(_request(asked))

    assert ai.fetched[0][1] == asked
    assert ai.synthesized[0][0] == asked
    assert result.question == asked


async def test_questions_differing_only_in_case_share_a_cache_entry(
    make_settings: SettingsFactory,
) -> None:
    """The one place the canonical form is observable from outside."""
    ai = StubAIService()
    service = _service(make_settings(), ai)

    first = await service.research(_request("what is photosynthesis"))
    second = await service.research(_request("  What IS Photosynthesis? "))

    # Two sources, one run's worth: the second run never contacted a provider.
    assert len(ai.fetched) == 2
    assert second.status is ResultStatus.SUCCESS
    assert first.answer is not None
    assert second.answer is not None
    assert second.answer.citations == first.answer.citations
    # Both runs synthesised; only the second ran on cached sources.
    assert [question for question, _ in ai.synthesized] == [
        "what is photosynthesis",
        "  What IS Photosynthesis? ",
    ]


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


async def test_a_failed_source_makes_the_run_partial_and_says_which(
    make_settings: SettingsFactory,
) -> None:
    ai = StubAIService(outcomes={SourceName.ARXIV: _failed(SourceName.ARXIV)})
    service = _service(make_settings(), ai)

    result = await service.research(_request())

    assert result.status is ResultStatus.PARTIAL
    assert result.answer is not None
    assert any("arxiv was unavailable (upstream_error)" in note for note in result.warnings)


async def test_a_source_that_returned_nothing_makes_the_run_partial(
    make_settings: SettingsFactory,
) -> None:
    """Empty is not the same as failed, and the reader is told which happened.

    A source that answered with nothing has not degraded the *answer*, but it
    has limited it, and the difference between "arxiv was down" and "arxiv had
    nothing" changes what a reader should do next.
    """
    ai = StubAIService(outcomes={SourceName.ARXIV: _empty(SourceName.ARXIV)})
    service = _service(make_settings(), ai)

    result = await service.research(_request())

    assert result.status is ResultStatus.PARTIAL
    assert any("arxiv returned no results" in note for note in result.warnings)
    assert not any("unavailable" in note for note in result.warnings)


async def test_a_run_that_retrieved_nothing_is_reported_without_calling_synthesis(
    make_settings: SettingsFactory,
) -> None:
    """Synthesis is skipped, not called with an empty list.

    The supplied synthesizer raises for an empty source list, and a request
    that cannot be answered is not worth paying for twice.
    """
    ai = StubAIService(
        outcomes={source: _failed(source) for source in (SourceName.WIKIPEDIA, SourceName.ARXIV)}
    )
    service = _service(make_settings(), ai)

    result = await service.research(_request())

    assert result.status is ResultStatus.NO_SOURCES
    assert result.answer is None
    assert ai.synthesized == []
    assert any("no usable sources" in note for note in result.warnings)
    # Both failures are still reported, so the user can see why.
    assert any("wikipedia was unavailable" in note for note in result.warnings)


async def test_a_synthesis_failure_is_reported_as_a_failed_run(
    make_settings: SettingsFactory,
) -> None:
    """The retrieval is not discarded: the sources were fetched and paid for."""
    ai = StubAIService(error=UpstreamError("synthesis request failed", source="synthesis"))
    service = _service(make_settings(), ai)

    result = await service.research(_request())

    assert result.status is ResultStatus.FAILED
    assert result.answer is None
    assert any("synthesis failed" in note for note in result.warnings)
    # The retrieval survives the failure that ended the run.
    assert len(result.outcomes) == 2
    assert all(outcome.status is SourceStatus.SUCCESS for outcome in result.outcomes)


async def test_a_rejected_credential_propagates(make_settings: SettingsFactory) -> None:
    """Exit status 2 even this late: it is the environment, not this run.

    Reported as a failed run it would be retried forever, unchanged; raised it
    tells the user their key is wrong, which is the only thing that fixes it.
    """
    ai = StubAIService(error=ConfigurationError("no usable google_api_key is configured"))
    storage = InMemoryStorage()
    service = _service(make_settings(database_url=_DSN), ai, storage=storage)

    with pytest.raises(ConfigurationError):
        await service.research(_request())

    assert await storage.sessions.list_recent() == ()


# ---------------------------------------------------------------------------
# Bad input costs nothing
# ---------------------------------------------------------------------------


async def test_an_overlong_question_fails_before_any_provider_is_contacted(
    make_settings: SettingsFactory,
) -> None:
    ai = StubAIService()
    storage = InMemoryStorage()
    service = _service(
        make_settings(max_question_length=20, database_url=_DSN), ai, storage=storage
    )

    with pytest.raises(InvalidRequestError) as caught:
        await service.research(_request("a question that is comfortably longer than twenty"))

    assert "the limit is 20" in str(caught.value)
    assert ai.fetched == []
    assert ai.synthesized == []
    assert await storage.sessions.list_recent() == ()


async def test_a_punctuation_only_question_fails_before_any_provider_is_contacted(
    make_settings: SettingsFactory,
) -> None:
    """It passes the structural check, so only canonicalisation can catch it.

    Left to the fan-out, this would surface as an internal error mid-gather —
    after quota had been spent, and reported as a source failure rather than as
    something the user can fix.
    """
    ai = StubAIService()
    service = _service(make_settings(), ai)

    with pytest.raises(InvalidRequestError) as caught:
        await service.research(_request("???"))

    assert "more than punctuation" in str(caught.value)
    assert ai.fetched == []


async def test_a_result_limit_above_the_configured_maximum_fails_before_any_fetch(
    make_settings: SettingsFactory,
) -> None:
    ai = StubAIService()
    service = _service(make_settings(max_results_per_source=2), ai)

    with pytest.raises(InvalidRequestError) as caught:
        await service.research(_request(max_results=5))

    assert "--max-results 5 exceeds the configured limit of 2" in str(caught.value)
    assert ai.fetched == []


async def test_a_web_request_without_a_credential_fails_before_any_fetch(
    make_settings: SettingsFactory,
) -> None:
    """A missing key is exit 2, not a source failure the user reads as an outage."""
    ai = StubAIService()
    service = _service(make_settings(web_search_provider="tavily", tavily_api_key=None), ai)

    with pytest.raises(ConfigurationError):
        await service.research(_request("what is photosynthesis", SourceName.WEB))

    assert ai.fetched == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def test_a_completed_session_is_saved_and_reported_as_such(
    make_settings: SettingsFactory,
) -> None:
    storage = InMemoryStorage()
    service = _service(make_settings(database_url=_DSN), StubAIService(), storage=storage)

    result = await service.research(_request())

    assert result.persistence is PersistenceStatus.SAVED
    stored = await storage.sessions.get(result.request_id)
    assert stored is not None
    assert stored.id == result.request_id
    assert stored.status is ResultStatus.SUCCESS
    assert stored.answer == result.answer


async def test_the_stored_session_records_the_provider_identity(
    make_settings: SettingsFactory,
) -> None:
    """A run is only reproducible if the record says what produced it."""
    storage = InMemoryStorage()
    service = _service(make_settings(database_url=_DSN), StubAIService(), storage=storage)

    result = await service.research(_request())
    stored = await storage.sessions.get(result.request_id)

    assert stored is not None
    assert stored.provider.llm_model == "gemini-3.8-flash"
    assert stored.provider.web_search_provider == "tavily"


async def test_a_failed_run_is_still_saved(make_settings: SettingsFactory) -> None:
    """The point of the record is to explain the failure afterwards."""
    storage = InMemoryStorage()
    ai = StubAIService(error=UpstreamError("synthesis request failed", source="synthesis"))
    service = _service(make_settings(database_url=_DSN), ai, storage=storage)

    result = await service.research(_request())

    stored = await storage.sessions.get(result.request_id)
    assert stored is not None
    assert stored.status is ResultStatus.FAILED
    assert stored.retrieval.outcomes == result.outcomes
    assert stored.warnings == result.warnings


async def test_persistence_is_skipped_when_no_storage_was_configured(
    make_settings: SettingsFactory,
) -> None:
    """An unconfigured database is a choice, not a fault, and not a warning."""
    service = _service(make_settings(database_url=_DSN), StubAIService())

    result = await service.research(_request())

    assert result.persistence is PersistenceStatus.SKIPPED
    assert not any("not saved" in note for note in result.warnings)


async def test_persistence_is_skipped_when_there_is_no_database_url(
    make_settings: SettingsFactory,
) -> None:
    """``persist_sessions`` alone is not enough; the DSN is what makes it real.

    Storage is injected here, so a service that checked only ``persist_sessions``
    would attempt a write against a database it was never told about.
    """
    storage = InMemoryStorage()
    settings = make_settings(database_url=None)
    service = _service(settings, StubAIService(), storage=storage)

    result = await service.research(_request())

    assert settings.persist_sessions is True  # the only other input says "write"
    assert result.persistence is PersistenceStatus.SKIPPED
    assert await storage.sessions.list_recent() == ()


async def test_persistence_is_skipped_when_it_is_switched_off(
    make_settings: SettingsFactory,
) -> None:
    """A DSN is set and storage is wired, and the run still does not write."""
    storage = InMemoryStorage()
    service = _service(
        make_settings(database_url=_DSN, persist_sessions=False),
        StubAIService(),
        storage=storage,
    )

    result = await service.research(_request())

    assert result.persistence is PersistenceStatus.SKIPPED
    assert await storage.sessions.list_recent() == ()


async def test_a_failed_write_does_not_discard_the_answer(
    make_settings: SettingsFactory,
) -> None:
    """The user already paid for the answer; losing it to a disk fault is worse.

    But it must not pass silently either, or "nothing was recorded" is found
    later as a gap in the history rather than now, when it can be said out loud.
    """
    storage = BrokenStorage()
    service = _service(make_settings(database_url=_DSN), StubAIService(), storage=storage)

    result = await service.research(_request())

    assert result.persistence is PersistenceStatus.FAILED
    assert result.status is ResultStatus.SUCCESS
    assert result.answer is not None
    assert any("the session was not saved (storage_error)" in note for note in result.warnings)
    assert storage.sessions.calls == 1


# ---------------------------------------------------------------------------
# Dangling citations
# ---------------------------------------------------------------------------


async def test_a_citation_with_no_matching_reference_is_reported(
    make_settings: SettingsFactory,
) -> None:
    """The supplied synthesizer drops out-of-range indices without editing prose.

    The answer text is not rewritten — that would be putting words in the
    model's mouth — so the marker is reported instead of being rendered as
    though it resolved to something.
    """
    answer = AnswerWithCitations(
        question="what is photosynthesis",
        answer="Plants convert light into sugar [1]. The stroma is involved [7].",
        citations=[Citation(index=1, source=make_source("wikipedia"))],
    )
    service = _service(make_settings(), StubAIService(answer=answer))

    result = await service.research(_request())

    assert result.status is ResultStatus.SUCCESS
    assert any("[7]" in note and "no matching reference" in note for note in result.warnings)
    assert not any("[1]" in note for note in result.warnings)


async def test_an_answer_whose_citations_all_resolve_produces_no_warning(
    make_settings: SettingsFactory,
) -> None:
    service = _service(make_settings(), StubAIService())

    result = await service.research(_request())

    assert not any("no matching reference" in note for note in result.warnings)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


async def test_timings_are_recorded_for_the_run(make_settings: SettingsFactory) -> None:
    service = _service(make_settings(), StubAIService())

    result = await service.research(_request())

    timing = result.timing
    assert timing.finished_at >= timing.started_at
    assert timing.retrieval_seconds >= 0.0
    assert timing.synthesis_seconds >= 0.0


async def test_synthesis_time_is_not_charged_when_synthesis_was_skipped(
    make_settings: SettingsFactory,
) -> None:
    """The number a benchmark reads, so an unattempted step must read as zero."""
    ai = StubAIService(outcomes={source: _empty(source) for source in SourceName})
    service = _service(make_settings(), ai)

    result = await service.research(_request())

    assert result.status is ResultStatus.NO_SOURCES
    assert result.timing.synthesis_seconds == 0.0


async def test_the_run_is_identified_by_a_fresh_id_each_time(
    make_settings: SettingsFactory,
) -> None:
    service = _service(make_settings(), StubAIService())

    first = await service.research(_request())
    second = await service.research(_request())

    assert first.request_id != second.request_id


async def test_a_run_does_not_leave_a_pending_task_behind(
    make_settings: SettingsFactory,
) -> None:
    """Nothing is detached: the service awaits everything it starts.

    A leaked task would outlive its caller, write to a closed pool, and surface
    only as an unrelated error in a later test.
    """
    service = _service(make_settings(), StubAIService())

    before = asyncio.all_tasks()
    await service.research(_request())
    assert asyncio.all_tasks() - before == set()
