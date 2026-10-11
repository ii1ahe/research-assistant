"""Runs one question through validation, retrieval, answer generation, and optional session
storage.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable

from ai.schemas import AnswerWithCitations
from researcher.config import Settings
from researcher.errors import ConfigurationError, ResearcherError, StorageError
from researcher.models import (
    PersistenceStatus,
    ResearchRequest,
    ResearchResult,
    ResearchSession,
    ResultStatus,
    SourceOutcome,
    SourceStatus,
    TimingInfo,
    utc_now,
)
from researcher.services.ai_service import AIService
from researcher.services.orchestrator import Orchestrator
from researcher.storage.interfaces import Storage
from researcher.validation import (
    canonicalize_query,
    unreferenced_citation_indices,
    validate_request,
)

__all__ = ["ResearchService"]

logger = logging.getLogger(__name__)


class ResearchService:
    """Sequences one research request and reports everything it learned."""

    def __init__(
        self,
        *,
        ai: AIService,
        orchestrator: Orchestrator,
        settings: Settings,
        storage: Storage | None = None,
    ) -> None:
        """Set up the research workflow.

        Args:
            ai: Service used to generate answers.
            orchestrator: Fetches sources under a concurrency limit.
            settings: Controls session storage.
            storage: Saves completed sessions. None skips storage without a failure.
        """
        self._ai = ai
        self._orchestrator = orchestrator
        self._settings = settings
        self._storage = storage

    async def research(self, request: ResearchRequest) -> ResearchResult:
        """Answer ``request``, and describe how it went.

        Args:
            request: The request, already built from user input.

        Returns:
            The result, carrying the answer when one was produced, every
            source's outcome, the warnings raised along the way, the timings
            and whether the session reached storage.

        Raises:
            InvalidRequestError: The request is unacceptable in itself.
            ConfigurationError: The configuration cannot serve this request, or
                the provider rejected our credentials at synthesis time.
        """
        # Check the request and its cache key before making network calls.
        validate_request(request, self._settings)
        canonical_query = canonicalize_query(request.question)

        request_id = uuid.uuid4()
        started_at = utc_now()
        warnings: list[str] = []

        retrieval_started = time.perf_counter()
        retrieval = await self._orchestrator.retrieve(request, canonical_query=canonical_query)
        retrieval_seconds = time.perf_counter() - retrieval_started

        warnings.extend(retrieval.warnings)
        warnings.extend(_describe_outcomes(retrieval.outcomes))

        answer = None
        status = ResultStatus.NO_SOURCES
        synthesis_seconds = 0.0

        if not retrieval.sources:
            # Do not request an answer when retrieval returned no sources.
            warnings.append("no usable sources were retrieved, so no answer was produced")
            logger.warning("request %s produced no usable sources", request_id)
        else:
            synthesis_started = time.perf_counter()
            try:
                answer = await self._ai.synthesize(request.question, retrieval.sources)
            except ConfigurationError:
                # A configuration problem needs a user change, not another run.
                raise
            except ResearcherError as exc:
                status = ResultStatus.FAILED
                warnings.append(f"synthesis failed ({exc.code}): {exc.message}")
                logger.warning("synthesis failed for request %s: %s", request_id, exc)
            else:
                status = _status_for(retrieval.outcomes)
                warnings.extend(_describe_dangling_citations(answer))
            finally:
                synthesis_seconds = time.perf_counter() - synthesis_started

        timing = TimingInfo(
            started_at=started_at,
            finished_at=utc_now(),
            retrieval_seconds=retrieval_seconds,
            synthesis_seconds=synthesis_seconds,
        )

        session = ResearchSession(
            id=request_id,
            created_at=started_at,
            request=request,
            provider=self._ai.provider_identity,
            retrieval=retrieval,
            status=status,
            answer=answer,
            warnings=tuple(warnings),
            timing=timing,
        )

        persistence, note = await self._persist(session)
        if note is not None:
            # Only a failed write adds a warning after the session snapshot is built.
            warnings.append(note)

        return ResearchResult(
            request_id=request_id,
            question=request.question,
            status=status,
            answer=answer,
            outcomes=retrieval.outcomes,
            warnings=tuple(warnings),
            timing=timing,
            persistence=persistence,
        )

    async def _persist(self, session: ResearchSession) -> tuple[PersistenceStatus, str | None]:
        """Write ``session``, and describe rather than raise a write failure."""
        if self._storage is None or not self._settings.persistence_enabled:
            return PersistenceStatus.SKIPPED, None
        try:
            await self._storage.sessions.save(session)
        except StorageError as exc:
            logger.error("session %s was not saved: %s", session.id, exc, exc_info=exc)
            return (
                PersistenceStatus.FAILED,
                f"the session was not saved ({exc.code}): {exc.message}",
            )
        logger.info("saved session %s", session.id)
        return PersistenceStatus.SAVED, None


def _status_for(outcomes: Iterable[SourceOutcome]) -> ResultStatus:
    """Describe a run that produced an answer: complete, or degraded."""
    if all(outcome.status is SourceStatus.SUCCESS for outcome in outcomes):
        return ResultStatus.SUCCESS
    return ResultStatus.PARTIAL


def _describe_outcomes(outcomes: Iterable[SourceOutcome]) -> list[str]:
    """Turn source outcomes into notes a user can act on.

    Says nothing about sources that succeeded, so the warnings stay readable:
    on a clean run this produces nothing at all.
    """
    notes: list[str] = []
    for outcome in outcomes:
        name = outcome.source.value
        if outcome.failure is not None:
            notes.append(
                f"{name} was unavailable ({outcome.failure.code}): {outcome.failure.message}"
            )
        elif outcome.status is SourceStatus.EMPTY:
            notes.append(f"{name} returned no results")
    return notes


def _describe_dangling_citations(answer: AnswerWithCitations) -> list[str]:
    """Warn about reference numbers the prose uses but the answer does not hold."""
    dangling = unreferenced_citation_indices(answer)
    if not dangling:
        return []
    markers = ", ".join(f"[{index}]" for index in dangling)
    return [f"the answer cites {markers} but the answer carries no matching reference"]
