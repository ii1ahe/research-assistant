"""Wraps the supplied AI calls with checked settings, deadlines, retries, and error handling."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Sequence

import httpx

from ai.providers.base import LLMProvider, ProviderError
from ai.schemas import AnswerWithCitations, Source
from ai.sources import fetch_arxiv, fetch_web, fetch_wikipedia
from researcher.config import Settings
from researcher.errors import (
    ConfigurationError,
    InvalidRequestError,
    ResearcherError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)
from researcher.models import (
    FailureDetail,
    ProviderIdentity,
    SourceName,
    SourceOutcome,
    SourceStatus,
)
from researcher.services.http_client import build_client
from researcher.services.resilience import Attempts, RetryPolicy, deadline, execute
from researcher.services.wikipedia import fetch_wikipedia_fulltext
from researcher.validation import validate_answer

__all__ = ["AIService"]

logger = logging.getLogger(__name__)

# The supplied package uses one error type for setup and request failures.
# Only known setup messages stop retries; unknown failures remain retryable.
_MISCONFIGURATION_MARKERS = (
    "is not set",
    "is required",
    "unknown llm_provider",
    "unknown web_search_provider",
    "install with",
    "expected anthropic",
)

# Recognise rate limits even when the provider gives no retry delay.
_THROTTLING_MARKERS = (
    "429",
    "resource_exhausted",
    "rate limit",
    "rate-limited",
    "quota",
    "throttl",
)

# Parse retry delays expressed in seconds. HTTP headers are checked separately.
_RETRY_AFTER_PATTERNS = (
    re.compile(
        r"retry in\s+(?P<delay>\d+(?:\.\d+)?)\s*(?:s\b|secs?\b|seconds?\b)",
        re.IGNORECASE,
    ),
    re.compile(r"retry-after:?\s+(?P<delay>\d+(?:\.\d+)?)\s*s?\b", re.IGNORECASE),
)


def _retry_after_hint(text: str) -> float | None:
    """Parse the delay a provider asks us to wait, when it names one."""
    for pattern in _RETRY_AFTER_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        delay = float(match.group("delay"))
        if math.isfinite(delay):
            return delay
    return None


def _delay_from_response(exc: BaseException) -> float | None:
    """Read a ``Retry-After`` delay off the response an exception carries."""
    try:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            # Some exceptions expose headers directly rather than through a response.
            headers = getattr(exc, "headers", None)
        if headers is None:
            return None
        # Plain mappings may use either header-name spelling.
        raw = headers.get("retry-after")
        if raw is None:
            raw = headers.get("Retry-After")
        if raw is None:
            return None
        delay = float(str(raw).strip())
    except Exception:
        # A malformed retry hint must not hide the original provider failure.
        return None
    if not math.isfinite(delay) or delay < 0:
        return None
    return delay


def _header_retry_after(exc: BaseException) -> float | None:
    """Walk the exception chain for a response carrying a ``Retry-After``."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        delay = _delay_from_response(current)
        if delay is not None:
            return delay
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return None


def _translate(exc: ProviderError, *, source: str) -> ResearcherError:
    """Classify a supplied-package failure into this application's taxonomy."""
    text = str(exc)
    lowered = text.casefold()
    if any(marker in lowered for marker in _MISCONFIGURATION_MARKERS):
        return ConfigurationError(text, source=source)
    hint = _retry_after_hint(text)
    if hint is None:
        hint = _header_retry_after(exc)
    if hint is not None or any(marker in lowered for marker in _THROTTLING_MARKERS):
        message = f"{source} is rate-limited"
        if hint is not None:
            message += f"; the provider asks to retry in {hint:g}s"
        return UpstreamRateLimitError(message, source=source, retry_after_seconds=hint)
    return UpstreamError(f"{source} request failed", source=source)


class AIService:
    """Credentials, retries, deadlines and translation around ``ai.*``."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        retrieval_policy: RetryPolicy | None = None,
        synthesis_policy: RetryPolicy | None = None,
    ) -> None:
        """Set up provider settings and retry policies.

        Args:
            settings: Checked application settings.
            client: Shared HTTP client. The caller closes it; a client made here is
                closed by aclose.
            retrieval_policy: Retry policy for source requests.
            synthesis_policy: Retry policy for answers; timeouts are not retried by default.

        Raises:
            ConfigurationError: The selected LLM has no API key. Web credentials are
                checked when that source is requested.
        """
        self._settings = settings
        self._client = client
        self._owns_client = False
        # Fetching uses GET, so retrying a timed-out request is acceptable.
        self._retrieval_policy = retrieval_policy or RetryPolicy()
        # A timed-out answer request may still run and be billed, so do not repeat it.
        self._synthesis_policy = synthesis_policy or RetryPolicy(retry_timeouts=False)
        self._llm: LLMProvider | None = None
        self._web: object | None = None

        if settings.resolved_llm_api_key is None:
            raise ConfigurationError(
                f"no API key configured for LLM_PROVIDER={settings.llm_provider!r}. "
                "Set the matching key in .env — see .env.example for which variable "
                "each provider reads.",
                source="synthesis",
            )

    # Wiring

    @property
    def provider_identity(self) -> ProviderIdentity:
        """Which stack will produce a result, for the session snapshot."""
        return ProviderIdentity(
            llm_provider=self._settings.llm_provider,
            llm_model=self._settings.effective_llm_model,
            web_search_provider=self._settings.web_search_provider,
        )

    def _http(self) -> httpx.AsyncClient:
        """Return the HTTP client, building and owning one if none was given."""
        if self._client is None:
            self._client = build_client(self._settings)
            self._owns_client = True
        return self._client

    def _llm_provider(self) -> LLMProvider:
        """Build the configured LLM provider on first use, with explicit credentials."""
        if self._llm is not None:
            return self._llm

        provider = self._settings.llm_provider
        model = self._settings.effective_llm_model
        key = self._settings.resolved_llm_api_key
        if provider == "anthropic":
            from ai.providers.anthropic import AnthropicLLM

            self._llm = AnthropicLLM(model, api_key=key)
        elif provider == "openai":
            from ai.providers.openai import OpenAILLM

            self._llm = OpenAILLM(model, api_key=key)
        else:
            from ai.providers.google import GeminiLLM

            self._llm = GeminiLLM(model, api_key=key)
        logger.debug("synthesis will use %s/%s", provider, model)
        return self._llm

    def _web_provider(self) -> object:
        """Build the configured search provider on first use, with an explicit key."""
        if self._web is not None:
            return self._web

        name = self._settings.web_search_provider
        key = self._settings.web_search_api_key
        timeout = self._settings.per_source_timeout_seconds
        if name == "tavily":
            from ai.sources import TavilyProvider

            self._web = TavilyProvider(key, timeout=timeout)
        elif name == "serper":
            from ai.sources import SerperProvider

            self._web = SerperProvider(key, timeout=timeout)
        else:
            from ai.sources import DuckDuckGoProvider

            self._web = DuckDuckGoProvider()
        return self._web

    # Retrieval

    async def fetch_source(
        self,
        source: SourceName,
        query: str,
        *,
        max_results: int | None = None,
    ) -> SourceOutcome:
        """Retrieve from one source, and describe how it went.

        Args:
            source: Which source to query.
            query: The question text, as the user typed it.
            max_results: Results to request. Defaults to the configured limit.

        Returns:
            A success, empty, timeout or error outcome.
        """
        limit = max_results if max_results is not None else self._settings.max_results_per_source
        attempts = Attempts()
        started = time.perf_counter()
        try:
            async with deadline(self._settings.per_source_timeout_seconds, source=source.value):
                sources = await execute(
                    lambda: self._fetch(source, query, limit),
                    policy=self._retrieval_policy,
                    description=f"{source.value} fetch",
                    attempts=attempts,
                )
        except UpstreamTimeoutError as exc:
            return self._failure(source, SourceStatus.TIMEOUT, exc, attempts, started)
        except ResearcherError as exc:
            return self._failure(source, SourceStatus.ERROR, exc, attempts, started)

        elapsed = time.perf_counter() - started
        if not sources:
            # An empty response is different from a failed request.
            logger.info("%s returned no usable results", source.value)
            return SourceOutcome(
                source=source,
                status=SourceStatus.EMPTY,
                elapsed_seconds=elapsed,
                attempts=attempts.count,
            )

        logger.info(
            "%s returned %d result(s) in %.2fs (%d attempt(s))",
            source.value,
            len(sources),
            elapsed,
            attempts.count,
        )
        return SourceOutcome(
            source=source,
            status=SourceStatus.SUCCESS,
            sources=tuple(sources),
            elapsed_seconds=elapsed,
            attempts=attempts.count,
        )

    async def _fetch(self, source: SourceName, query: str, max_results: int) -> list[Source]:
        """Make one attempt at one source.

        Raises:
            ResearcherError: Translated from the supplied package's
                ``ProviderError``. Never lets the original escape.
        """
        client = self._http()
        try:
            if source is SourceName.WIKIPEDIA:
                if self._settings.wikipedia_search == "opensearch":
                    return await fetch_wikipedia(query, max_results=max_results, client=client)
                return await fetch_wikipedia_fulltext(query, max_results=max_results, client=client)
            if source is SourceName.ARXIV:
                return await fetch_arxiv(query, max_results=max_results, client=client)
            return await fetch_web(
                query,
                max_results=max_results,
                provider=self._web_provider(),  # type: ignore[arg-type]
                client=client,
            )
        except ProviderError as exc:
            raise _translate(exc, source=source.value) from exc

    @staticmethod
    def _failure(
        source: SourceName,
        status: SourceStatus,
        exc: ResearcherError,
        attempts: Attempts,
        started: float,
    ) -> SourceOutcome:
        """Describe a failed retrieval without letting the exception escape."""
        logger.warning("%s retrieval ended as %s (%s)", source.value, status.value, exc.code)
        return SourceOutcome(
            source=source,
            status=status,
            elapsed_seconds=time.perf_counter() - started,
            attempts=max(attempts.count, 1),
            failure=FailureDetail(
                code=exc.code,
                message=exc.message,
                source=exc.source,
                retryable=exc.retryable,
            ),
        )

    # Synthesis

    async def synthesize(self, question: str, sources: Sequence[Source]) -> AnswerWithCitations:
        """Produce a cited answer from ``sources``, off the event loop.

        Args:
            question: The question, as the user typed it.
            sources: The ordered sources. Their order fixes citation numbering
                and must not be changed afterwards.

        Returns:
            The validated answer.

        Raises:
            InvalidRequestError: ``sources`` is empty, or the synthesizer
                rejected the inputs.
            InvalidAnswerError: The answer's references are internally
                inconsistent — see :func:`researcher.validation.validate_answer`.
            ConfigurationError: The provider is not usable.
            UpstreamError: The provider failed.
            UpstreamTimeoutError: The provider exceeded the deadline.
        """
        materialised = tuple(sources)
        if not materialised:
            # Reject an empty source list before calling the supplied answer generator.
            raise InvalidRequestError(
                "cannot synthesise an answer without sources", source="synthesis"
            )

        try:
            llm = self._llm_provider()
        except ProviderError as exc:
            raise _translate(exc, source="synthesis") from exc

        async def attempt() -> AnswerWithCitations:
            """Run synthesis once, translating the provider's failure."""
            try:
                return await asyncio.to_thread(_call_synthesize, question, list(materialised), llm)
            except ProviderError as exc:
                # Translate inside the retry loop so the policy sees a ResearcherError.
                raise _translate(exc, source="synthesis") from exc

        try:
            async with deadline(self._settings.synthesis_timeout_seconds, source="synthesis"):
                answer = await execute(
                    attempt, policy=self._synthesis_policy, description="synthesis"
                )
        except ValueError as exc:
            raise InvalidRequestError(str(exc), source="synthesis") from exc

        return validate_answer(answer, materialised)

    # Lifecycle

    async def aclose(self) -> None:
        """Close the HTTP client, if this service built it."""
        client, self._client = self._client, None
        if client is None or not self._owns_client:
            return
        self._owns_client = False
        try:
            await client.aclose()
        except Exception:
            logger.warning("closing the HTTP client failed", exc_info=True)


def _call_synthesize(question: str, sources: list[Source], llm: LLMProvider) -> AnswerWithCitations:
    """Call ``ai.synthesize`` with an explicitly constructed provider."""
    from ai.synthesizer import synthesize

    return synthesize(question, sources, llm=llm)
