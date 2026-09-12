"""One research operation, from question to durable record.

This is the application layer: the only place that knows the *order* of the
work and what each step's failure means. Everything it uses is injected, so the
whole sequence can be exercised against in-memory storage and a stubbed
``AIService`` without a network or a database.

Two decisions shape the class, and both are about what a failure should cost.

**Bad input raises; a bad run is returned.** Anything wrong with the request or
the configuration raises before any work is attempted, because those are exit
status 2 — the user must change something and retrying is pointless. Everything
after that point is a *result*: a run where all three sources failed is still a
run that happened, and describing it inside a
:class:`~researcher.models.ResearchResult` keeps the CLI free of control flow
and gives the session store something complete to hold.

**A failed run is still recorded.** Synthesis failing does not discard the
retrieval: the sources were fetched, the provider was paid, and the outcomes
explain what went wrong. The session is written either way, which is what makes
a failure diagnosable after the fact rather than only in a log line.

The one exception to both is :class:`~researcher.errors.ConfigurationError`
raised *by synthesis*. A missing or rejected credential is not a property of
this run, it is a property of the environment, and it will fail identically on
every future run — so it is an exit status 2 even at that late stage, and it is
allowed to propagate.
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
        """Initialize the service.

        Args:
            ai: The ``ai`` boundary. Used for synthesis; retrieval is delegated
                to ``orchestrator``, which holds its own reference to the same
                object.
            orchestrator: Performs the bounded, per-source fan-out.
            settings: Supplies the persistence policy.
            storage: Where completed sessions are written. ``None`` means the
                run is not persisted and is reported as ``skipped`` rather than
                as a failure — an unconfigured database is a choice, not a
                fault.
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
        # Raises before any network call. The canonical form is derived here so
        # that every source is keyed by the same string, and so that a question
        # consisting only of punctuation is rejected as bad input rather than
        # surfacing as an unexpected error from inside the fan-out.
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
            # Synthesis is skipped rather than called with an empty list: the
            # supplied synthesizer raises ValueError for that, and paying for a
            # request we know is unanswerable would be worse.
            warnings.append("no usable sources were retrieved, so no answer was produced")
            logger.warning("request %s produced no usable sources", request_id)
        else:
            synthesis_started = time.perf_counter()
            try:
                answer = await self._ai.synthesize(request.question, retrieval.sources)
            except ConfigurationError:
                # Not this run's fault and not fixable by running again.
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
            # Only ever set when the write failed, in which case there is no
            # stored copy whose warnings would have to match this one.
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
        """Write ``session``, and describe rather than raise a write failure.

        A failed session write is not allowed to discard an answer the user has
        already paid for, but it must not pass silently either: the run is
        reported as unsaved so the CLI can exit non-zero, which is what makes
        "the answer is right there but nothing was recorded" visible instead of
        discovered later as a gap in the history.
        """
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
    """Describe a run that produced an answer: complete, or degraded.

    Degraded means any selected source did not fully contribute — including one
    that answered with nothing, since a reader deciding how much to trust the
    answer needs to know a source came back empty, not just that none errored.
    """
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
    """Warn about reference numbers the prose uses but the answer does not hold.

    The supplied synthesizer drops out-of-range indices from the citation list
    without editing the answer text, so prose citing ``[7]`` can ship with three
    references. Those markers are reported rather than rendered as though they
    resolved.
    """
    dangling = unreferenced_citation_indices(answer)
    if not dangling:
        return []
    markers = ", ".join(f"[{index}]" for index in dangling)
    return [f"the answer cites {markers} but the answer carries no matching reference"]
