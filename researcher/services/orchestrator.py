"""Fetches the selected sources under a concurrency limit and combines results even when a source
fails.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ai.schemas import Source
from researcher.config import Settings
from researcher.models import (
    CacheKey,
    FailureDetail,
    ResearchRequest,
    RetrievalResult,
    SourceName,
    SourceOutcome,
    SourceStatus,
)
from researcher.services.ai_service import AIService
from researcher.services.cache import CacheService
from researcher.validation import deduplicate_sources

__all__ = ["Orchestrator"]

logger = logging.getLogger(__name__)

# Separate unexpected internal faults from known provider errors.
INTERNAL_ERROR_CODE = "internal_error"


class Orchestrator:
    """Retrieves from every selected source, concurrently and independently."""

    def __init__(self, ai: AIService, cache: CacheService, settings: Settings) -> None:
        """Initialize the orchestrator.

        Args:
            ai: The boundary that talks to the supplied package.
            cache: Retrieval-result cache. Consulted per source, never required.
            settings: Supplies ``max_parallel_sources``.
        """
        self._ai = ai
        self._cache = cache
        self._settings = settings

    async def retrieve(
        self,
        request: ResearchRequest,
        *,
        canonical_query: str,
    ) -> RetrievalResult:
        """Retrieve from every source in ``request``.

        Args:
            request: The validated request.
            canonical_query: The cache-normal form of the question, derived once
                by the caller. Passed in rather than recomputed per source so
                that every source is keyed by exactly the same string.

        Returns:
            Every source's outcome, plus the surviving sources in citation
            order and any notes the run produced.
        """
        limit = asyncio.Semaphore(self._settings.max_parallel_sources)
        notes: list[str] = []
        seen: set[str] = set()

        def note(message: str) -> None:
            """Record a run-wide note once, however many sources produced it."""
            if message not in seen:
                seen.add(message)
                notes.append(message)

        async def bounded(source: SourceName) -> SourceOutcome:
            async with limit:
                return await self._retrieve_one(source, request, canonical_query, note)

        results = await asyncio.gather(
            *(bounded(source) for source in request.sources), return_exceptions=True
        )
        outcomes = tuple(
            _as_outcome(source, result)
            for source, result in zip(request.sources, results, strict=True)
        )
        # Remove duplicate URLs across all sources while keeping the first result.
        sources = deduplicate_sources(src for outcome in outcomes for src in outcome.sources)
        logger.info(
            "retrieved %d source(s) from %d of %d selected",
            len(sources),
            sum(1 for outcome in outcomes if outcome.status is SourceStatus.SUCCESS),
            len(outcomes),
        )
        return RetrievalResult(outcomes=outcomes, sources=sources, warnings=tuple(notes))

    async def _retrieve_one(
        self,
        source: SourceName,
        request: ResearchRequest,
        canonical_query: str,
        note: Callable[[str], None],
    ) -> SourceOutcome:
        """Retrieve one source, preferring a fresh cache entry."""
        key = (
            self._cache_key(source, canonical_query, request.max_results)
            if request.use_cache
            else None
        )

        if key is not None:
            lookup = await self._cache.lookup(key)
            if lookup.failure is not None:
                # A cache failure is reported, then retrieval continues with a live fetch.
                note(f"cache read failed ({lookup.failure.code}); sources were fetched live")
            elif lookup.entry is not None:
                return _from_cache(source, lookup.entry.sources)

        outcome = await self._ai.fetch_source(
            source, request.question, max_results=request.max_results
        )

        if key is not None and outcome.status is SourceStatus.SUCCESS:
            write = await self._cache.store(key, outcome.sources)
            if write.failure is not None:
                note(f"cache write failed ({write.failure.code}); results were not stored")

        return outcome

    def _cache_key(self, source: SourceName, canonical_query: str, max_results: int) -> CacheKey:
        """Build the identity of one cacheable result."""
        return CacheKey(
            source=source,
            query=canonical_query,
            provider=self._provider_for(source),
            max_results=max_results,
        )

    def _provider_for(self, source: SourceName) -> str:
        """Name the backend a cached payload came from."""
        if source is SourceName.WEB:
            return self._settings.web_search_provider
        return source.value


def _from_cache(source: SourceName, sources: tuple[Source, ...]) -> SourceOutcome:
    """Describe a cache hit, including a cached *empty* result."""
    if not sources:
        return SourceOutcome(source=source, status=SourceStatus.EMPTY, cache_hit=True, attempts=0)
    return SourceOutcome(
        source=source,
        status=SourceStatus.SUCCESS,
        sources=sources,
        cache_hit=True,
        attempts=0,
    )


def _as_outcome(source: SourceName, result: SourceOutcome | BaseException) -> SourceOutcome:
    """Turn a gather result into an outcome, whatever it is.

    Raises:
        asyncio.CancelledError: Deliberately. ``gather(return_exceptions=True)``
            captures a child's own cancellation as an ordinary result, and
            turning that into an error outcome would swallow a shutdown. The
            same reasoning keeps ``resilience.execute`` from catching
            ``BaseException``.
    """
    if isinstance(result, asyncio.CancelledError):
        raise result

    if isinstance(result, BaseException):
        # Log unexpected exception details, but keep provider payloads out of user output.
        logger.error("%s retrieval raised unexpectedly", source.value, exc_info=result)
        return SourceOutcome(
            source=source,
            status=SourceStatus.ERROR,
            failure=FailureDetail(
                code=INTERNAL_ERROR_CODE,
                message="an unexpected internal error occurred while retrieving this source",
                source=source.value,
            ),
        )

    return result
