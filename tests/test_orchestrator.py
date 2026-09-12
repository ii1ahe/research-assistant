"""Fan-out, degradation and ordering — the claims ADR-005 makes.

Three of these are load-bearing and easy to get subtly wrong:

*Order.* The position of a source in the result fixes its citation number, so
results must be reassembled in the order the user selected, never in the order
they happened to finish. The test for this deliberately makes the *first*
source the slowest.

*Bounded concurrency.* Unbounded fan-out is how a working application earns
HTTP 429s. The bound is measured here, not read back from configuration.

*Survival.* One broken source must not cost the others, and a cache fault must
not cost anything at all.

Cache behaviour is tested through the public surface: a test warms the cache by
running a retrieval, or seeds it with a key built by hand, and then observes
whether the next run fetches. That way the tests also pin the key convention —
a key that stopped matching would show up as a failed fetch count, not as a
private method returning something different.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from researcher.config import Settings
from researcher.models import (
    CacheKey,
    FailureDetail,
    ResearchRequest,
    SourceName,
    SourceOutcome,
    SourceStatus,
)
from researcher.services.cache import CacheService
from researcher.services.orchestrator import Orchestrator
from researcher.storage.memory import InMemorySourceCache
from tests.conftest import BrokenCache, StubAIService, make_source

pytestmark = pytest.mark.asyncio

SettingsFactory = Callable[..., Settings]


def _request(*sources: SourceName, use_cache: bool = True, max_results: int = 3) -> ResearchRequest:
    return ResearchRequest(
        question="what is photosynthesis",
        sources=tuple(sources) or tuple(SourceName),
        use_cache=use_cache,
        max_results=max_results,
    )


def _key(
    source: SourceName,
    provider: str,
    *,
    query: str = "q",
    max_results: int = 3,
) -> CacheKey:
    """Build a cache key by hand, to seed the cache before a run."""
    return CacheKey(source=source, query=query, provider=provider, max_results=max_results)


def _failed(source: SourceName, code: str = "upstream_error") -> SourceOutcome:
    return SourceOutcome(
        source=source,
        status=SourceStatus.ERROR,
        failure=FailureDetail(code=code, message="the provider refused the connection"),
    )


class Probe:
    """A fetcher that records concurrency and can be made deliberately slow."""

    def __init__(self, *, delays: Mapping[SourceName, float] | None = None) -> None:
        self.delays = dict(delays or {})
        self.live = 0
        self.peak = 0
        self.calls: list[SourceName] = []

    async def fetch_source(
        self, source: SourceName, query: str, *, max_results: int | None = None
    ) -> SourceOutcome:
        """Sleep for this source's delay, tracking how many overlap."""
        self.calls.append(source)
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(self.delays.get(source, 0.0))
        finally:
            self.live -= 1
        return SourceOutcome(
            source=source,
            status=SourceStatus.SUCCESS,
            sources=(make_source(source.value),),
        )


def _build(settings: Settings, ai: Any, cache: Any = None) -> Orchestrator:
    return Orchestrator(ai, CacheService(cache or InMemorySourceCache(), settings), settings)


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------


async def test_every_selected_source_is_fetched_once(make_settings: SettingsFactory) -> None:
    ai = StubAIService()
    orchestrator = _build(make_settings(), ai)

    result = await orchestrator.retrieve(
        _request(SourceName.WIKIPEDIA, SourceName.ARXIV), canonical_query="q"
    )

    assert [outcome.source for outcome in result.outcomes] == [
        SourceName.WIKIPEDIA,
        SourceName.ARXIV,
    ]
    assert all(outcome.status is SourceStatus.SUCCESS for outcome in result.outcomes)
    assert [call[0] for call in ai.fetched] == [SourceName.WIKIPEDIA, SourceName.ARXIV]


async def test_sources_are_concatenated_in_selection_order(make_settings: SettingsFactory) -> None:
    """The order of ``result.sources`` is what fixes citation numbering."""
    orchestrator = _build(make_settings(), StubAIService())

    result = await orchestrator.retrieve(
        _request(SourceName.WEB, SourceName.WIKIPEDIA), canonical_query="q"
    )

    assert [source.origin for source in result.sources] == ["web", "wikipedia"]


async def test_outcomes_follow_selection_order_not_completion_order(
    make_settings: SettingsFactory,
) -> None:
    """The first source is the slowest, so completion order is the reverse.

    A gather that reassembled results as they arrived would renumber every
    citation in the answer — silently, and consistently enough to look fine.
    """
    probe = Probe(delays={SourceName.WIKIPEDIA: 0.05, SourceName.ARXIV: 0.0})
    orchestrator = _build(make_settings(), probe)

    result = await orchestrator.retrieve(
        _request(SourceName.WIKIPEDIA, SourceName.ARXIV), canonical_query="q"
    )

    assert probe.calls == [SourceName.WIKIPEDIA, SourceName.ARXIV]
    assert [outcome.source for outcome in result.outcomes] == [
        SourceName.WIKIPEDIA,
        SourceName.ARXIV,
    ]
    assert [source.origin for source in result.sources] == ["wikipedia", "arxiv"]


async def test_one_failing_source_does_not_cost_the_others(
    make_settings: SettingsFactory,
) -> None:
    ai = StubAIService(outcomes={SourceName.ARXIV: _failed(SourceName.ARXIV)})
    orchestrator = _build(make_settings(), ai)

    result = await orchestrator.retrieve(
        _request(SourceName.WIKIPEDIA, SourceName.ARXIV, SourceName.WEB), canonical_query="q"
    )

    assert {outcome.source: outcome.status for outcome in result.outcomes} == {
        SourceName.WIKIPEDIA: SourceStatus.SUCCESS,
        SourceName.ARXIV: SourceStatus.ERROR,
        SourceName.WEB: SourceStatus.SUCCESS,
    }
    # Only the two that answered contribute sources.
    assert len(result.sources) == 2


async def test_sources_returned_by_two_providers_are_collapsed(
    make_settings: SettingsFactory,
) -> None:
    """One page can legitimately be the top hit for two sources."""
    shared = make_source("wikipedia", 1)
    ai = StubAIService(
        outcomes={
            source: SourceOutcome(source=source, status=SourceStatus.SUCCESS, sources=(shared,))
            for source in (SourceName.WIKIPEDIA, SourceName.ARXIV)
        }
    )
    orchestrator = _build(make_settings(), ai)

    result = await orchestrator.retrieve(
        _request(SourceName.WIKIPEDIA, SourceName.ARXIV), canonical_query="q"
    )

    assert result.sources == (shared,)
    # Both sources still report their own outcome; only the citation list is
    # deduplicated.
    assert len(result.outcomes) == 2


async def test_an_unexpected_exception_becomes_an_error_outcome(
    make_settings: SettingsFactory,
) -> None:
    """A bug in the fetch layer must not abort a run that already spent quota."""

    class Exploding:
        async def fetch_source(self, source: SourceName, query: str, **kwargs: Any) -> Any:
            raise ValueError("a bug, not a source failure")

    orchestrator = _build(make_settings(), Exploding())

    result = await orchestrator.retrieve(_request(SourceName.ARXIV), canonical_query="q")

    outcome = result.outcomes[0]
    assert outcome.status is SourceStatus.ERROR
    assert outcome.failure is not None
    assert outcome.failure.code == "internal_error"
    # The exception text is logged, not surfaced: an unanticipated message is
    # exactly the kind that can carry a provider payload.
    assert "a bug" not in outcome.failure.message
    assert result.sources == ()


async def test_cancellation_is_not_swallowed(make_settings: SettingsFactory) -> None:
    """A cancelled run stays cancelled rather than reporting a failed source.

    ``gather(return_exceptions=True)`` captures a child's cancellation as an
    ordinary result, so without an explicit re-raise a shutdown would look
    exactly like three dead sources.
    """

    class Cancelling:
        async def fetch_source(self, source: SourceName, query: str, **kwargs: Any) -> Any:
            raise asyncio.CancelledError

    orchestrator = _build(make_settings(), Cancelling())

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.retrieve(_request(SourceName.ARXIV), canonical_query="q")


# ---------------------------------------------------------------------------
# Bounded concurrency
# ---------------------------------------------------------------------------


async def test_concurrency_is_capped_at_the_configured_bound(
    make_settings: SettingsFactory,
) -> None:
    """Measured, not inferred: the peak is observed rather than asserted."""
    probe = Probe(delays=dict.fromkeys(SourceName, 0.02))
    orchestrator = _build(make_settings(max_parallel_sources=2), probe)

    await orchestrator.retrieve(_request(*SourceName), canonical_query="q")

    assert probe.peak == 2


async def test_a_bound_of_one_serialises_the_fetches(make_settings: SettingsFactory) -> None:
    probe = Probe(delays=dict.fromkeys(SourceName, 0.01))
    orchestrator = _build(make_settings(max_parallel_sources=1), probe)

    await orchestrator.retrieve(_request(*SourceName), canonical_query="q")

    assert probe.peak == 1
    assert len(probe.calls) == 3


# ---------------------------------------------------------------------------
# Cache interaction
# ---------------------------------------------------------------------------


async def test_a_second_run_is_served_from_the_cache(make_settings: SettingsFactory) -> None:
    """Proves the store happened, without reaching into the cache's internals."""
    ai = StubAIService()
    orchestrator = _build(make_settings(), ai)

    first = await orchestrator.retrieve(_request(SourceName.WIKIPEDIA), canonical_query="q")
    second = await orchestrator.retrieve(_request(SourceName.WIKIPEDIA), canonical_query="q")

    assert not first.outcomes[0].cache_hit
    assert second.outcomes[0].cache_hit
    assert second.outcomes[0].attempts == 0
    assert second.sources == first.sources
    # The provider was called exactly once across both runs.
    assert len(ai.fetched) == 1


async def test_a_cached_empty_result_is_empty_not_success(
    make_settings: SettingsFactory,
) -> None:
    """A successful outcome must carry a source, and there is none to carry.

    Reported as EMPTY because that is what it is: the answer to "is there
    anything here" does not depend on when it was asked.
    """
    settings = make_settings()
    cache = InMemorySourceCache()
    ai = StubAIService()
    await CacheService(cache, settings).store(_key(SourceName.WIKIPEDIA, "wikipedia"), ())

    result = await _build(settings, ai, cache).retrieve(
        _request(SourceName.WIKIPEDIA), canonical_query="q"
    )

    assert result.outcomes[0].status is SourceStatus.EMPTY
    assert result.outcomes[0].cache_hit
    assert result.sources == ()
    assert ai.fetched == []


async def test_no_cache_flag_touches_no_cache(make_settings: SettingsFactory) -> None:
    """``--no-cache`` means neither read nor write, in either direction."""
    broken = BrokenCache()
    orchestrator = _build(make_settings(), StubAIService(), broken)

    result = await orchestrator.retrieve(
        _request(SourceName.WIKIPEDIA, use_cache=False), canonical_query="q"
    )

    assert result.outcomes[0].status is SourceStatus.SUCCESS
    assert result.warnings == ()
    assert broken.reads == 0
    assert broken.writes == 0


async def test_a_failed_cache_read_still_fetches_and_is_reported(
    make_settings: SettingsFactory,
) -> None:
    """The fallback is the work that would have happened without a cache."""
    broken = BrokenCache()
    ai = StubAIService()
    orchestrator = _build(make_settings(), ai, broken)

    result = await orchestrator.retrieve(_request(SourceName.WIKIPEDIA), canonical_query="q")

    assert len(ai.fetched) == 1
    assert result.outcomes[0].status is SourceStatus.SUCCESS
    assert broken.reads == 1
    assert any("cache read failed" in note for note in result.warnings)


async def test_a_failed_cache_write_is_reported_once(make_settings: SettingsFactory) -> None:
    """Run-wide, so three sources sharing one broken cache produce one note each."""
    orchestrator = _build(make_settings(), StubAIService(), BrokenCache())

    result = await orchestrator.retrieve(_request(*SourceName), canonical_query="q")

    assert sum("cache read failed" in note for note in result.warnings) == 1
    assert sum("cache write failed" in note for note in result.warnings) == 1


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


async def test_the_key_names_the_backend_for_web(make_settings: SettingsFactory) -> None:
    """Switching WEB_SEARCH_PROVIDER must not serve the previous provider's hits.

    Two entries are seeded for the same query under different provider names. A
    key that omitted the provider would return whichever was written last.
    """
    settings = make_settings(web_search_provider="tavily")
    cache = InMemorySourceCache()
    service = CacheService(cache, settings)
    await service.store(_key(SourceName.WEB, "tavily"), (make_source("web", 1),))
    await service.store(_key(SourceName.WEB, "duckduckgo"), (make_source("web", 2),))

    result = await _build(settings, StubAIService(), cache).retrieve(
        _request(SourceName.WEB), canonical_query="q"
    )

    assert result.outcomes[0].cache_hit
    assert result.sources[0].title == "web result 1"


async def test_the_key_separates_result_limits(make_settings: SettingsFactory) -> None:
    """Asking for three must not be served the entry that holds five."""
    settings = make_settings()
    cache = InMemorySourceCache()
    ai = StubAIService()
    await CacheService(cache, settings).store(
        _key(SourceName.ARXIV, "arxiv", max_results=5), (make_source("arxiv", 5),)
    )

    result = await _build(settings, ai, cache).retrieve(
        _request(SourceName.ARXIV, max_results=3), canonical_query="q"
    )

    assert not result.outcomes[0].cache_hit
    assert len(ai.fetched) == 1
