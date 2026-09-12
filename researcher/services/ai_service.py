"""The application's only door into the supplied ``ai`` package.

Everything outside this module talks to :class:`AIService`; nothing else imports
``ai``. That rule buys three things at once.

**Credentials reach the providers.** The supplied providers read ``os.getenv``
directly, and nothing in this application loads ``.env`` into the process
environment — ``pydantic-settings`` reads that file into :class:`~researcher.
config.Settings` and stops there. Constructing each provider with an explicit
``api_key`` and ``model`` is therefore the only path by which a configured
credential arrives. Without this module the providers consult an empty
environment and report "not set" no matter what ``.env`` says. The supplied
factories (``ai.providers.factory.get_llm``, ``ai.sources.
get_web_search_provider``) are exactly that path and are deliberately bypassed.

**Failures arrive translated.** ``ai`` raises one coarse ``ProviderError`` for
missing credentials, missing packages, network faults and provider errors alike,
so it cannot be caught usefully as it stands. It is classified here, once,
into the taxonomy in :mod:`researcher.errors` — which is also the only place
that *can* classify it, since it is the only place that sees the original
exception.

**Blocking work stops blocking.** ``ai.synthesize`` is synchronous: it calls
``LLMProvider.complete``, a blocking SDK call. Left on the event loop it would
stall every concurrent source fetch for the length of the LLM round trip, which
for this application is the whole point of the async design.
"""

from __future__ import annotations

import asyncio
import logging
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

#: Substrings in a ``ProviderError`` message that mean "this was never
#: configured", as opposed to "this failed just now". The supplied package uses
#: one exception type for both, so the distinction has to be read off the text.
#:
#: Matched case-insensitively, and deliberately narrow. Anything unrecognised is
#: treated as transient, because a genuine network fault is the common case and
#: being wrong costs one wasted attempt — whereas wrongly calling a transient
#: fault permanent would abandon a source that would have answered.
_MISCONFIGURATION_MARKERS = (
    "is not set",
    "is required",
    "unknown llm_provider",
    "unknown web_search_provider",
    "install with",
    "expected anthropic",
)


def _translate(exc: ProviderError, *, source: str) -> ResearcherError:
    """Classify a supplied-package failure into this application's taxonomy.

    A misconfiguration carries an actionable message that names the missing
    variable and holds nothing sensitive, so it is passed through verbatim.
    Anything else may embed a provider payload or a URL, and is replaced with a
    generic message — the original is kept as ``__cause__`` for the traceback,
    which is where diagnostics belong. The same split is used by
    :func:`researcher.storage._driver.translate`.
    """
    text = str(exc)
    lowered = text.casefold()
    if any(marker in lowered for marker in _MISCONFIGURATION_MARKERS):
        return ConfigurationError(text, source=source)
    return UpstreamError(f"{source} request failed", source=source)


class AIService:
    """Credentials, retries, deadlines and translation around ``ai.*``.

    Construct with :class:`~researcher.config.Settings`; everything else is
    injected so tests can supply a fake client and a policy that does not sleep.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        retrieval_policy: RetryPolicy | None = None,
        synthesis_policy: RetryPolicy | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            settings: Validated application settings.
            client: An HTTP client to use. When omitted one is built on first
                use and closed by :meth:`aclose`. Pass one in to share it with
                the rest of the application, which then owns closing it.
            retrieval_policy: Retry policy for source fetches.
            synthesis_policy: Retry policy for synthesis. Defaults to a policy
                that does **not** retry timeouts.

        Raises:
            ConfigurationError: No credential is configured for the selected
                LLM provider. Checked here rather than at first use because
                every run ends in synthesis, so a missing key means retrieval
                would spend the user's time on an answer that can never be
                produced. The web-search credential is deliberately *not*
                checked here — see :meth:`fetch_source`.
        """
        self._settings = settings
        self._client = client
        self._owns_client = False
        #: A retried fetch is an idempotent GET, so a timeout is worth another
        #: attempt.
        self._retrieval_policy = retrieval_policy or RetryPolicy()
        #: A timed-out synthesis may still be running upstream, and billing,
        #: after the caller has given up — so it is abandoned, not repeated.
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

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

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
        """Build the configured LLM provider on first use, with explicit credentials.

        Bypasses ``ai.providers.factory.get_llm``, which reads the provider, the
        model and the key from ``os.environ`` — the environment this application
        does not populate.
        """
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
        """Build the configured search provider on first use, with an explicit key.

        Bypasses ``ai.sources.get_web_search_provider`` for the same reason as
        :meth:`_llm_provider`. DuckDuckGo needs no credential, so it is built
        with none and is the one provider that is always available.
        """
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

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def fetch_source(
        self,
        source: SourceName,
        query: str,
        *,
        max_results: int | None = None,
    ) -> SourceOutcome:
        """Retrieve from one source, and describe how it went.

        Never raises for a source-level failure. A source that times out, is
        unreachable, or is not configured produces an outcome recording that.
        Raising instead would push the same decision — "carry on without this
        one" — onto every call site, and would make partial success harder to
        express than total failure.

        A missing web-search credential is one of those source-level failures,
        not a startup check like the LLM key. Someone running ``--sources
        wiki,arxiv`` has a perfectly good reason not to hold a Tavily key, and
        should not be blocked by one.

        The deadline bounds the whole source, retries included, rather than
        each attempt. A source gets ``per_source_timeout_seconds`` of wall clock
        and no more, whatever it does with it. Retries therefore help the
        failures that come back quickly — a refused connection, a 502 — and a
        genuinely hung provider is abandoned at the deadline instead of being
        retried into a multiple of it.

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
            # Distinct from an error on purpose: the provider answered, it just
            # had nothing. That is a content problem, not an outage.
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

        The query reaches every fetcher untouched. Wikipedia's is the one place
        that could have been an exception — the supplied search cannot answer a
        question — and it is handled by choosing a different *fetcher* rather
        than by rewriting the query, so a query that has been through this
        method is still the query the user typed. See
        ``researcher/services/wikipedia.py``.

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

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def synthesize(self, question: str, sources: Sequence[Source]) -> AnswerWithCitations:
        """Produce a cited answer from ``sources``, off the event loop.

        ``ai.synthesize`` is synchronous, so it is dispatched to a worker
        thread. The consequence is that the deadline cannot stop it: the thread
        runs to completion and the provider may still bill for it. The
        alternative — calling it inline — would block every concurrent fetch,
        defeating the concurrency the rest of the design is built around.

        Because the deadline wraps the whole call rather than each attempt, a
        timeout abandons synthesis outright and no retry decision is ever
        reached; :attr:`RetryPolicy.retry_timeouts` is the second guard behind
        that, for the case where an attempt reports a timeout itself.

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
            # ai.synthesize raises ValueError for this, but an empty list here
            # means the caller skipped the no-sources check, which is a caller
            # bug worth naming precisely.
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
                # Translated *inside* the retried operation, so ``execute`` sees
                # a retryable ResearcherError. Translating out here instead — as
                # this method first did — leaves the policy inert: the loop only
                # retries ResearcherError, so a foreign ProviderError sails
                # straight through it and nothing is ever retried.
                raise _translate(exc, source="synthesis") from exc

        try:
            async with deadline(self._settings.per_source_timeout_seconds, source="synthesis"):
                answer = await execute(
                    attempt, policy=self._synthesis_policy, description="synthesis"
                )
        except ValueError as exc:
            raise InvalidRequestError(str(exc), source="synthesis") from exc

        return validate_answer(answer, materialised)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the HTTP client, if this service built it.

        Idempotent, and it logs rather than raises: it is called from ``finally``
        blocks, where a raise would replace the failure that got us there. An
        injected client is left open, because the caller that supplied it owns
        its lifetime.
        """
        client, self._client = self._client, None
        if client is None or not self._owns_client:
            return
        self._owns_client = False
        try:
            await client.aclose()
        except Exception:
            logger.warning("closing the HTTP client failed", exc_info=True)


def _call_synthesize(question: str, sources: list[Source], llm: LLMProvider) -> AnswerWithCitations:
    """Call ``ai.synthesize`` with an explicitly constructed provider.

    A module-level function rather than a closure so the thread it runs on is
    easy to point at in a traceback. Imported lazily so that importing this
    module does not pull the synthesizer in.
    """
    from ai.synthesizer import synthesize

    return synthesize(question, sources, llm=llm)
