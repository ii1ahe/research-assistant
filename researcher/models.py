"""Typed data contracts shared across application boundaries.

Every value that crosses a module boundary in this application is a Pydantic
model defined here. No module hands another a bare ``dict``
(see ``docs/COMMON_PITFALLS.md`` #12).

Design rules
------------
- **Frozen.** Contracts are immutable once built. This is the same discipline
  the supplied ``ai.schemas.Source`` uses, and it structurally prevents the
  renumbering hazard the architecture record calls out: retrieval order fixes
  citation numbering, so nothing downstream may reorder a source list. Updates
  are made with ``model_copy(update=...)``.
- **Intrinsic invariants live here; policy lives in ``validation``.** A model
  rejects a structurally impossible value — an empty question, a successful
  outcome carrying no sources. Bounds that depend on configuration, such as the
  maximum question length, belong to :mod:`researcher.validation`.
- **The original question is never overwritten by its canonical form.** A
  request keeps exactly what the user typed. The canonical form used as a cache
  key is derived on demand and stored on :class:`CacheKey`, never written back.

This module depends only on Pydantic and the immutable supplied schemas, so it
can be imported from anywhere without creating a cycle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai.schemas import AnswerWithCitations, Source

__all__ = [
    "AppModel",
    "CacheEntry",
    "CacheKey",
    "FailureDetail",
    "PersistenceStatus",
    "ProviderIdentity",
    "ResearchRequest",
    "ResearchResult",
    "ResearchSession",
    "ResultStatus",
    "RetrievalResult",
    "SourceName",
    "SourceOutcome",
    "SourceStatus",
    "TimingInfo",
    "utc_now",
]


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class AppModel(BaseModel):
    """Base class for every application data contract.

    Freezes the instance and forbids unknown fields, so a typo in a keyword
    argument fails loudly at construction instead of silently producing a
    default-valued field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SourceName(StrEnum):
    """Canonical source identifiers.

    Mirrors the ``origin`` values accepted by ``ai.schemas.Source``. The
    ``wiki`` alias is normalised to :attr:`WIKIPEDIA` during validation and is
    deliberately not a member here.
    """

    WIKIPEDIA = "wikipedia"
    ARXIV = "arxiv"
    WEB = "web"


class SourceStatus(StrEnum):
    """How one source's retrieval attempt ended.

    ``EMPTY`` is distinct from ``ERROR`` on purpose: a provider that answers
    successfully with no usable results is a content problem, not an outage,
    and the two warrant different diagnostics.
    """

    SUCCESS = "success"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    ERROR = "error"


class ResultStatus(StrEnum):
    """The outcome of one complete research operation."""

    #: An answer was produced and every selected source contributed.
    SUCCESS = "success"
    #: An answer was produced, but at least one source degraded.
    PARTIAL = "partial"
    #: No usable sources; synthesis was skipped rather than called with none.
    NO_SOURCES = "no_sources"
    #: No answer could be produced.
    FAILED = "failed"


class PersistenceStatus(StrEnum):
    """Whether the completed session reached durable storage."""

    SAVED = "saved"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


class FailureDetail(AppModel):
    """A safe, structured description of one failure.

    Carries the stable error code and retryability of a
    :class:`researcher.errors.ResearcherError` without holding a live exception,
    so it can be logged and persisted. ``message`` must already be safe to show
    a user: no credentials, no raw provider payloads.
    """

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    source: str | None = None
    retryable: bool = False


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ResearchRequest(AppModel):
    """A validated request to research one question.

    ``question`` holds the user's text verbatim, including its original casing
    and surrounding whitespace. The canonical form used for cache lookups is
    derived separately and never written back here.

    Bounds that depend on configuration — the maximum question length, the
    maximum number of results — are enforced by :func:`researcher.validation.
    validate_request`, not by this model.
    """

    question: str
    sources: tuple[SourceName, ...]
    use_cache: bool = True
    max_results: int = Field(default=3, ge=1)

    @field_validator("question")
    @classmethod
    def _question_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must be non-empty")
        return value

    @field_validator("sources")
    @classmethod
    def _sources_nonempty_and_unique(cls, value: tuple[SourceName, ...]) -> tuple[SourceName, ...]:
        if not value:
            raise ValueError("at least one source must be selected")
        if len(set(value)) != len(value):
            raise ValueError("sources must not contain duplicates")
        return value


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class SourceOutcome(AppModel):
    """The result of retrieving from exactly one source.

    Success, emptiness, timeout and error are recorded separately so a partial
    result can be described precisely instead of collapsing into "it failed".
    """

    source: SourceName
    status: SourceStatus
    sources: tuple[Source, ...] = ()
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    #: Requests made to the provider. Zero is legitimate: a cache hit performs
    #: no request, and ``cache_hit`` is what says so.
    attempts: int = Field(default=1, ge=0)
    cache_hit: bool = False
    failure: FailureDetail | None = None

    @model_validator(mode="after")
    def _status_matches_payload(self) -> SourceOutcome:
        if self.status is SourceStatus.SUCCESS and not self.sources:
            raise ValueError("a successful outcome must carry at least one source")
        if self.status is not SourceStatus.SUCCESS and self.sources:
            raise ValueError(f"a '{self.status.value}' outcome must not carry sources")
        if self.status in (SourceStatus.TIMEOUT, SourceStatus.ERROR) and self.failure is None:
            raise ValueError(f"a '{self.status.value}' outcome must describe the failure")
        return self


class RetrievalResult(AppModel):
    """Everything one retrieval pass produced.

    ``sources`` is the validated, de-duplicated, final ordering. Because
    ``ai.synthesize`` numbers citations by list position, this ordering is what
    fixes the reference numbers, and it must not be reordered afterwards.
    """

    outcomes: tuple[SourceOutcome, ...]
    sources: tuple[Source, ...] = ()
    #: Notes about the retrieval pass itself rather than about any one source —
    #: currently cache degradation, which is run-wide and would otherwise be
    #: repeated identically for every source that touched it.
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class CacheKey(AppModel):
    """Identity of one cacheable retrieval result.

    Scoped by provider *and* result limit *and* format version, not just by
    source and query: switching from Tavily to DuckDuckGo, or from three results
    to five, must not return the other's payload.

    ``query`` is the canonical form produced by
    :func:`researcher.validation.canonicalize_query`.
    """

    source: SourceName
    query: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    max_results: int = Field(ge=1)
    schema_version: int = Field(default=1, ge=1)


class CacheEntry(AppModel):
    """A cached retrieval result and its freshness window.

    The format version is not duplicated as a field; it is part of the key
    identity, so :attr:`schema_version` reads it from there. Two copies could
    disagree, and the key is what lookups are performed against.
    """

    key: CacheKey
    sources: tuple[Source, ...]
    fetched_at: datetime
    expires_at: datetime

    @property
    def schema_version(self) -> int:
        """The cache format version this entry was written under."""
        return self.key.schema_version

    def is_fresh(self, *, now: datetime | None = None) -> bool:
        """Return whether this entry is still within its freshness window."""
        return (now or utc_now()) < self.expires_at


# ---------------------------------------------------------------------------
# Identity of the answering stack
# ---------------------------------------------------------------------------


class ProviderIdentity(AppModel):
    """Which providers produced a result.

    Persisted with every session so a stored answer can be attributed to the
    exact model and search backend that generated it.
    """

    llm_provider: str = Field(min_length=1)
    llm_model: str = Field(min_length=1)
    web_search_provider: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------


class TimingInfo(AppModel):
    """Wall-clock timings for one research operation.

    Durations are stored as plain seconds rather than ``timedelta`` so the
    benchmark artefacts and log records can carry them without conversion.
    """

    started_at: datetime
    finished_at: datetime
    retrieval_seconds: float = Field(default=0.0, ge=0.0)
    synthesis_seconds: float = Field(default=0.0, ge=0.0)

    @property
    def total_seconds(self) -> float:
        """Total wall-clock duration, including persistence-free overhead."""
        return (self.finished_at - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class ResearchResult(AppModel):
    """What one research operation returns to its caller.

    An answer is present exactly when the status says one was produced, which
    :meth:`_answer_matches_status` enforces.
    """

    request_id: uuid.UUID
    question: str
    status: ResultStatus
    answer: AnswerWithCitations | None = None
    outcomes: tuple[SourceOutcome, ...] = ()
    warnings: tuple[str, ...] = ()
    timing: TimingInfo
    persistence: PersistenceStatus = PersistenceStatus.SKIPPED

    @model_validator(mode="after")
    def _answer_matches_status(self) -> ResearchResult:
        produced = self.status in (ResultStatus.SUCCESS, ResultStatus.PARTIAL)
        if produced and self.answer is None:
            raise ValueError(f"status {self.status.value} requires an answer")
        if not produced and self.answer is not None:
            raise ValueError(f"status {self.status.value} must not carry an answer")
        return self


class ResearchSession(AppModel):
    """A durable snapshot of one complete research operation.

    Self-contained by design: it holds the original request, the resolved source
    ordering, the full source payloads with their snippets, the answer and its
    references, and the outcomes for every source. The supplied
    ``AnswerWithCitations.to_dict()`` flattens references and drops snippets, so
    it is a presentation format and not suitable as the archival record.

    ``id`` is the same identifier as the corresponding
    :attr:`ResearchResult.request_id`.
    """

    id: uuid.UUID
    created_at: datetime
    request: ResearchRequest
    provider: ProviderIdentity
    retrieval: RetrievalResult
    status: ResultStatus
    answer: AnswerWithCitations | None = None
    warnings: tuple[str, ...] = ()
    timing: TimingInfo
