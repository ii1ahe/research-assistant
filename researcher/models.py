"""Defines the request, source result, cache, timing, and saved-session models and checks their
fields.
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
    """Base class for every application data contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# Enumerations


class SourceName(StrEnum):
    """Canonical source identifiers."""

    WIKIPEDIA = "wikipedia"
    ARXIV = "arxiv"
    WEB = "web"


class SourceStatus(StrEnum):
    """How one source's retrieval attempt ended."""

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


# Failures


class FailureDetail(AppModel):
    """A safe, structured description of one failure."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    source: str | None = None
    retryable: bool = False


# Request


class ResearchRequest(AppModel):
    """A validated request to research one question."""

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


# Retrieval


class SourceOutcome(AppModel):
    """The result of retrieving from exactly one source."""

    source: SourceName
    status: SourceStatus
    sources: tuple[Source, ...] = ()
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    # A cache hit makes no provider requests, so attempts can be zero.
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
    """Everything one retrieval pass produced."""

    outcomes: tuple[SourceOutcome, ...]
    sources: tuple[Source, ...] = ()
    # Run-wide notes, such as cache failures, are recorded once here.
    warnings: tuple[str, ...] = ()


# Caching


class CacheKey(AppModel):
    """Identity of one cacheable retrieval result."""

    source: SourceName
    query: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    max_results: int = Field(ge=1)
    schema_version: int = Field(default=1, ge=1)


class CacheEntry(AppModel):
    """A cached retrieval result and its freshness window."""

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


# Identity of the answering stack


class ProviderIdentity(AppModel):
    """Which providers produced a result.

    Persisted with every session so a stored answer can be attributed to the
    exact model and search backend that generated it.
    """

    llm_provider: str = Field(min_length=1)
    llm_model: str = Field(min_length=1)
    web_search_provider: str = Field(min_length=1)


# Timings


class TimingInfo(AppModel):
    """Wall-clock timings for one research operation."""

    started_at: datetime
    finished_at: datetime
    retrieval_seconds: float = Field(default=0.0, ge=0.0)
    synthesis_seconds: float = Field(default=0.0, ge=0.0)

    @property
    def total_seconds(self) -> float:
        """Total wall-clock duration, including persistence-free overhead."""
        return (self.finished_at - self.started_at).total_seconds()


# Results


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
    """A durable snapshot of one complete research operation."""

    id: uuid.UUID
    created_at: datetime
    request: ResearchRequest
    provider: ProviderIdentity
    retrieval: RetrievalResult
    status: ResultStatus
    answer: AnswerWithCitations | None = None
    warnings: tuple[str, ...] = ()
    timing: TimingInfo
