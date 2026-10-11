"""Defines the error types used by the app, with short codes and flags that control retries."""

from __future__ import annotations

from typing import ClassVar

__all__ = [
    "ConfigurationError",
    "InvalidAnswerError",
    "InvalidRequestError",
    "ResearcherError",
    "StorageError",
    "UpstreamError",
    "UpstreamRateLimitError",
    "UpstreamTimeoutError",
]


class ResearcherError(Exception):
    """Base error type for the application.

    Attributes:
        code: Error category, without user data or provider details.
        retryable: Whether the operation may succeed on another attempt.
        source: The source or operation that failed, when known.
    """

    code: ClassVar[str] = "researcher_error"
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, source: str | None = None) -> None:
        """Initialize the error.

        Args:
            message: Human-readable text that is safe to show the user. It must
                not embed credentials, full provider payloads or raw response
                bodies.
            source: The affected source or operation when the failure belongs to
                one — ``"wikipedia"``, ``"arxiv"``, ``"web"``, ``"synthesis"``
                or ``"storage"``.
        """
        super().__init__(message)
        self.message = message
        self.source = source

    def __str__(self) -> str:
        if self.source is not None:
            return f"[{self.source}] {self.message}"
        return self.message


class ConfigurationError(ResearcherError):
    """Settings are missing, malformed or mutually inconsistent."""

    code: ClassVar[str] = "configuration_error"
    retryable: ClassVar[bool] = False


class InvalidRequestError(ResearcherError):
    """Caller-supplied input failed validation.

    Raised for an empty or oversized question, an unknown or empty source
    selection, or a result limit outside the accepted range.
    """

    code: ClassVar[str] = "invalid_request"
    retryable: ClassVar[bool] = False


class UpstreamError(ResearcherError):
    """A source fetcher or the LLM provider failed."""

    code: ClassVar[str] = "upstream_error"
    retryable: ClassVar[bool] = True


class UpstreamRateLimitError(UpstreamError):
    """The provider throttled us, possibly naming how long to wait."""

    code: ClassVar[str] = "upstream_rate_limit"

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Initialize the error.

        Args:
            message: Safe, human-readable text.
            source: The affected source or operation.
            retry_after_seconds: The delay the provider asked for, parsed from
                its payload, or ``None`` when it throttled without naming one.
        """
        super().__init__(message, source=source)
        self.retry_after_seconds = retry_after_seconds


class UpstreamTimeoutError(UpstreamError):
    """An upstream operation exceeded its deadline."""

    code: ClassVar[str] = "upstream_timeout"


class InvalidAnswerError(ResearcherError):
    """The synthesised answer failed integrity checks."""

    code: ClassVar[str] = "invalid_answer"
    retryable: ClassVar[bool] = False


class StorageError(ResearcherError):
    """A persistence operation failed."""

    code: ClassVar[str] = "storage_error"
    retryable: ClassVar[bool] = False
