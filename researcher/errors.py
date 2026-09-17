"""Application failure categories.

Every error this application raises deliberately derives from
:class:`ResearcherError`. That gives the CLI one class to translate into an exit
status, and gives the resilience layer one place to decide whether a failure is
worth retrying.

The supplied ``ai`` package raises its own ``ai.providers.base.ProviderError``,
which subclasses ``RuntimeError`` and is deliberately coarse: it covers missing
credentials, missing packages, network faults and provider errors alike. That
error is a *dependency* failure and is translated into an :class:`UpstreamError`
at the ``ai_service`` boundary rather than being allowed to escape into the
application.

This module depends only on the standard library, so every other module can
import it without risking a cycle.
"""

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
    """Base class for every error this application raises deliberately.

    Attributes:
        code: Stable identifier for the failure category. Safe to log, print
            and match on — it never contains user data or provider detail.
        retryable: Whether the *same* operation could plausibly succeed if
            attempted again. A conservative class-level default; the resilience
            layer may narrow or widen it per operation.
        source: Name of the affected source or operation, when the failure
            belongs to exactly one.
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
    """Settings are missing, malformed or mutually inconsistent.

    Not retryable: retrying cannot repair a bad configuration, and retrying an
    authentication failure only burns quota (see ``docs/COMMON_PITFALLS.md``).
    """

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
    """A source fetcher or the LLM provider failed.

    Retryable by default, but that default is deliberately loose: the resilience
    layer classifies the underlying cause before scheduling an attempt, because
    a missing API key surfaces through the same ``ai.ProviderError`` as a
    genuine network fault.
    """

    code: ClassVar[str] = "upstream_error"
    retryable: ClassVar[bool] = True


class UpstreamRateLimitError(UpstreamError):
    """The provider throttled us, possibly naming how long to wait.

    Distinct from a plain :class:`UpstreamError` because the provider's answer
    to a throttle is *actionable*: it names a delay after which the same call
    would be accepted. That delay is parsed out of the provider's payload at
    the classification boundary — the only place the payload still exists —
    and carried here so the retry policy can honour it. The message itself is
    composed from safe parts; the raw payload stays in ``__cause__``.
    """

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
    """An upstream operation exceeded its deadline.

    A distinct type because a timeout is the one failure that leaves work
    running: the SDK call behind ``ai.synthesize`` is synchronous and cannot be
    forcibly cancelled, so the operation may still be in flight after this is
    raised.
    """

    code: ClassVar[str] = "upstream_timeout"


class InvalidAnswerError(ResearcherError):
    """The synthesised answer failed integrity checks.

    Raised when citation indices are non-positive, reference a source that does
    not exist, or duplicate one another. Structural checks cannot establish
    factual support, only internal consistency.
    """

    code: ClassVar[str] = "invalid_answer"
    retryable: ClassVar[bool] = False


class StorageError(ResearcherError):
    """A persistence operation failed.

    Retryability is decided by the caller rather than by this class. A cache
    read may safely degrade to a live fetch and a cache write may degrade to a
    warning, but a failure to persist a research session must be reported as
    unsaved rather than presented as a clean success. Defaults to not
    retryable so a partially applied write is never repeated without the caller
    consciously deciding that it is idempotent.
    """

    code: ClassVar[str] = "storage_error"
    retryable: ClassVar[bool] = False
