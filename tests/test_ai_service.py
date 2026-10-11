"""Checks retrieval and answer generation, including empty results, failures, timeouts, and
cleanup.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

import httpx
import pytest

from ai.providers.base import ProviderError
from ai.schemas import AnswerWithCitations, Citation, Source
from researcher.config import Settings
from researcher.errors import (
    ConfigurationError,
    InvalidAnswerError,
    InvalidRequestError,
    UpstreamError,
)
from researcher.models import SourceName, SourceStatus
from researcher.services import ai_service as ai_service_module
from researcher.services.ai_service import AIService
from researcher.services.resilience import RetryPolicy

pytestmark = pytest.mark.asyncio

#: The ``make_settings`` fixture's signature, spelled out so each test's
#: arguments are annotated.
SettingsFactory = Callable[..., Settings]

#: A policy that retries immediately, so tests never wait on a real backoff.
FAST = RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0)


def _source(n: int) -> Source:
    return Source(
        title=f"Title {n}",
        url=f"https://example.com/{n}",
        snippet=f"snippet-{n}",
        origin="wikipedia",
    )


def _answer(question: str, sources: list[Source]) -> AnswerWithCitations:
    return AnswerWithCitations(
        question=question,
        answer="An answer [1].",
        citations=[Citation(index=1, source=sources[0])],
    )


# Retrieval outcomes


def patch_wikipedia(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., object]) -> None:
    """Patch both Wikipedia fetchers with a single fake."""
    monkeypatch.setattr(ai_service_module, "fetch_wikipedia", fake)
    monkeypatch.setattr(ai_service_module, "fetch_wikipedia_fulltext", fake)


async def test_a_successful_fetch_reports_success(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(query: str, **kwargs: object) -> list[Source]:
        return [_source(1), _source(2)]

    patch_wikipedia(monkeypatch, fake)

    outcome = await AIService(make_settings(), retrieval_policy=FAST).fetch_source(
        SourceName.WIKIPEDIA, "a question"
    )

    assert outcome.status is SourceStatus.SUCCESS
    assert outcome.source is SourceName.WIKIPEDIA
    assert len(outcome.sources) == 2
    assert outcome.attempts == 1
    assert outcome.failure is None


async def test_no_results_is_empty_not_an_error(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider answered; it just had nothing. That is not an outage."""

    async def fake(query: str, **kwargs: object) -> list[Source]:
        return []

    monkeypatch.setattr(ai_service_module, "fetch_arxiv", fake)

    outcome = await AIService(make_settings(), retrieval_policy=FAST).fetch_source(
        SourceName.ARXIV, "a question"
    )

    assert outcome.status is SourceStatus.EMPTY
    assert outcome.sources == ()
    # An empty result is not a failure, so there is nothing to describe.
    assert outcome.failure is None


async def test_a_transient_provider_failure_is_recorded_not_raised(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(query: str, **kwargs: object) -> list[Source]:
        raise ProviderError("Tavily search failed: connection reset by peer")

    monkeypatch.setattr(ai_service_module, "fetch_web", fake)

    outcome = await AIService(make_settings(), retrieval_policy=FAST).fetch_source(
        SourceName.WEB, "a question"
    )

    assert outcome.status is SourceStatus.ERROR
    assert outcome.failure is not None
    assert outcome.failure.code == "upstream_error"
    assert outcome.failure.retryable


async def test_a_missing_credential_is_a_configuration_failure(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same exception type as a network fault, classified differently."""

    async def fake(query: str, **kwargs: object) -> list[Source]:
        raise ProviderError("TAVILY_API_KEY is not set.")

    monkeypatch.setattr(ai_service_module, "fetch_web", fake)

    outcome = await AIService(make_settings(), retrieval_policy=FAST).fetch_source(
        SourceName.WEB, "a question"
    )

    assert outcome.status is SourceStatus.ERROR
    assert outcome.failure is not None
    assert outcome.failure.code == "configuration_error"
    assert not outcome.failure.retryable


async def test_a_missing_package_is_a_configuration_failure(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(query: str, **kwargs: object) -> list[Source]:
        raise ProviderError(
            "The `duckduckgo-search` package is required. Install with `pip install`."
        )

    monkeypatch.setattr(ai_service_module, "fetch_web", fake)

    outcome = await AIService(make_settings(), retrieval_policy=FAST).fetch_source(
        SourceName.WEB, "a question"
    )

    assert outcome.failure is not None
    assert outcome.failure.code == "configuration_error"


async def test_an_upstream_message_is_not_copied_into_the_failure(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider text can carry payload; the cause keeps it for the traceback."""
    secret = "https://api.example.com/v1/search?token=abcd1234"

    async def fake(query: str, **kwargs: object) -> list[Source]:
        raise ProviderError(f"request to {secret} failed")

    monkeypatch.setattr(ai_service_module, "fetch_web", fake)

    outcome = await AIService(make_settings(), retrieval_policy=FAST).fetch_source(
        SourceName.WEB, "a question"
    )

    assert outcome.failure is not None
    assert "abcd1234" not in outcome.failure.message


async def test_a_slow_source_times_out(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(query: str, **kwargs: object) -> list[Source]:
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(ai_service_module, "fetch_arxiv", fake)

    service = AIService(make_settings(per_source_timeout_seconds=0.02), retrieval_policy=FAST)
    outcome = await service.fetch_source(SourceName.ARXIV, "a question")

    assert outcome.status is SourceStatus.TIMEOUT
    assert outcome.failure is not None
    assert outcome.failure.code == "upstream_timeout"


async def test_a_source_is_retried_and_can_recover(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def fake(query: str, **kwargs: object) -> list[Source]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderError("Wikipedia search failed: read timeout")
        return [_source(1)]

    patch_wikipedia(monkeypatch, fake)

    outcome = await AIService(make_settings(), retrieval_policy=FAST).fetch_source(
        SourceName.WIKIPEDIA, "a question"
    )

    assert outcome.status is SourceStatus.SUCCESS
    # The attempt count is what tells an operator a source only just answered.
    assert outcome.attempts == 2


async def test_the_deadline_bounds_the_source_including_its_retries(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source gets one budget, not one per attempt.

    Otherwise a retrying source could take ``attempts * timeout`` seconds and
    the configured per-source timeout would mean nothing.
    """
    calls = 0

    async def fake(query: str, **kwargs: object) -> list[Source]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        raise ProviderError("arXiv search failed: read timeout")

    monkeypatch.setattr(ai_service_module, "fetch_arxiv", fake)

    service = AIService(
        make_settings(per_source_timeout_seconds=0.12),
        retrieval_policy=RetryPolicy(max_attempts=10, initial_backoff=0.0, max_backoff=0.0),
    )
    started = time.perf_counter()
    outcome = await service.fetch_source(SourceName.ARXIV, "a question")
    elapsed = time.perf_counter() - started

    assert outcome.status is SourceStatus.TIMEOUT
    # Retries really did run: this is not a first-attempt timeout.
    assert calls >= 2
    # ...and the loop was cut short. Ten attempts of 0.05s would have taken 0.5s.
    assert calls < 10
    assert elapsed < 0.3, f"the deadline did not bound the whole source: {elapsed:.2f}s"


@pytest.mark.parametrize(
    ("setting", "expected"),
    [("fulltext", "fulltext"), ("opensearch", "supplied")],
)
async def test_each_source_kind_reaches_its_own_fetcher(
    make_settings: SettingsFactory,
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    expected: str,
) -> None:
    """Dispatch is by canonical name, and the sources must not be crossed."""
    seen: list[str] = []

    def make(name: str) -> Callable[..., object]:
        async def fake(query: str, **kwargs: object) -> list[Source]:
            seen.append(name)
            return [_source(1)]

        return fake

    monkeypatch.setattr(ai_service_module, "fetch_wikipedia", make("supplied"))
    monkeypatch.setattr(ai_service_module, "fetch_wikipedia_fulltext", make("fulltext"))
    monkeypatch.setattr(ai_service_module, "fetch_arxiv", make("arxiv"))
    monkeypatch.setattr(ai_service_module, "fetch_web", make("web"))

    service = AIService(make_settings(wikipedia_search=setting), retrieval_policy=FAST)
    for source in (SourceName.WIKIPEDIA, SourceName.ARXIV, SourceName.WEB):
        await service.fetch_source(source, "a question")

    assert seen == [expected, "arxiv", "web"]


async def test_wikipedia_uses_the_fulltext_fetcher_unless_told_otherwise(
    make_settings: SettingsFactory,
) -> None:
    """The default is the one that can answer a question, not the supplied one."""
    assert make_settings().wikipedia_search == "fulltext"


async def test_the_fetchers_share_one_http_client(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connection reuse is why the client is injected at all."""
    clients: list[object] = []

    async def fake(query: str, **kwargs: object) -> list[Source]:
        clients.append(kwargs.get("client"))
        return [_source(1)]

    patch_wikipedia(monkeypatch, fake)
    monkeypatch.setattr(ai_service_module, "fetch_arxiv", fake)

    service = AIService(make_settings(), retrieval_policy=FAST)
    await service.fetch_source(SourceName.WIKIPEDIA, "q")
    await service.fetch_source(SourceName.ARXIV, "q")

    assert clients[0] is clients[1]
    assert isinstance(clients[0], httpx.AsyncClient)
    await service.aclose()


async def test_an_injected_client_is_used(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []

    async def fake(query: str, **kwargs: object) -> list[Source]:
        seen.append(kwargs.get("client"))
        return [_source(1)]

    patch_wikipedia(monkeypatch, fake)

    injected = httpx.AsyncClient()
    service = AIService(make_settings(), client=injected, retrieval_policy=FAST)
    await service.fetch_source(SourceName.WIKIPEDIA, "q")

    assert seen == [injected]
    await service.aclose()
    # The caller owns it, so aclose must leave it usable.
    assert not injected.is_closed
    await injected.aclose()


# Synthesis


async def test_synthesis_returns_a_validated_answer(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        return _answer(question, sources)

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    answer = await AIService(make_settings()).synthesize("a question", [_source(1)])

    assert answer.question == "a question"
    assert [c.index for c in answer.citations] == [1]


async def test_synthesis_does_not_block_the_event_loop(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ai.synthesize`` is synchronous, so it must not run on the loop."""

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        time.sleep(0.1)  # stands in for the blocking SDK call
        return _answer(question, sources)

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await AIService(make_settings()).synthesize("a question", [_source(1)])
    finally:
        task.cancel()

    assert ticks > 0, "synthesis blocked the event loop"


async def test_synthesis_without_sources_is_rejected(make_settings: SettingsFactory) -> None:
    """Guarded here rather than left to ``ai.synthesize``'s ``ValueError``."""
    with pytest.raises(InvalidRequestError, match="without sources"):
        await AIService(make_settings()).synthesize("a question", [])


async def test_a_reordered_source_list_is_caught(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validation is wired in, not merely available."""

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        return _answer(question, list(reversed(sources)))

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    with pytest.raises(InvalidAnswerError, match="reordered"):
        await AIService(make_settings()).synthesize("a question", [_source(1), _source(2)])


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
async def test_a_blank_answer_is_rejected(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """An empty answer is not an answer, however well-formed its citations."""

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        return AnswerWithCitations(question=question, answer=blank, citations=[])

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    with pytest.raises(InvalidAnswerError, match="empty"):
        await AIService(make_settings()).synthesize("a question", [_source(1)])


async def test_an_answer_without_citations_is_still_accepted(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The companion to the test above: the check must not overshoot."""

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        return AnswerWithCitations(
            question=question, answer="The sources do not settle this.", citations=[]
        )

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    answer = await AIService(make_settings()).synthesize("a question", [_source(1)])

    assert answer.answer == "The sources do not settle this."


async def test_a_provider_error_during_synthesis_is_translated(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        raise ProviderError("Gemini call failed: 503 Service Unavailable")

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    with pytest.raises(UpstreamError) as caught:
        await AIService(make_settings()).synthesize("a question", [_source(1)])

    assert caught.value.source == "synthesis"
    assert caught.value.retryable


async def test_a_missing_synthesis_credential_is_a_configuration_error(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        raise ProviderError("GOOGLE_API_KEY (or LLM_API_KEY) is not set.")

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    with pytest.raises(ConfigurationError):
        await AIService(make_settings()).synthesize("a question", [_source(1)])


async def test_building_the_provider_is_a_failure_point_too(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing SDK surfaces at construction, as the same coarse error."""

    class Exploding:
        def __init__(self, model: str | None = None, *, api_key: str | None = None) -> None:
            raise ProviderError(
                "The `google-genai` package is required. Install with `pip install`."
            )

    monkeypatch.setattr("ai.providers.google.GeminiLLM", Exploding)

    with pytest.raises(ConfigurationError, match="Install with"):
        await AIService(make_settings()).synthesize("a question", [_source(1)])


async def test_a_synthesizer_value_error_becomes_an_invalid_request(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supplied synthesizer signals bad input with a bare ``ValueError``."""

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        raise ValueError("question must be non-empty")

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    with pytest.raises(InvalidRequestError, match="non-empty"):
        await AIService(make_settings()).synthesize("a question", [_source(1)])


async def test_a_transient_synthesis_failure_is_retried(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry only works if translation happens *inside* the retried operation."""
    calls = 0

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderError("Gemini call failed: 503 Service Unavailable")
        return _answer(question, sources)

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    service = AIService(
        make_settings(),
        synthesis_policy=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0),
    )
    answer = await service.synthesize("a question", [_source(1)])

    assert calls == 2
    assert answer.answer


async def test_a_rate_limited_synthesis_waits_the_provider_hint_then_recovers(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole chain: payload → parsed hint → wait → recovered attempt."""
    calls = 0

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderError("Gemini call failed: 429 RESOURCE_EXHAUSTED. Please retry in 0.15s")
        return _answer(question, sources)

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    service = AIService(
        make_settings(),
        synthesis_policy=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0),
    )
    started = time.perf_counter()
    answer = await service.synthesize("a question", [_source(1)])
    elapsed = time.perf_counter() - started

    assert calls == 2
    assert answer.answer
    assert elapsed >= 0.1


async def test_a_non_retryable_synthesis_failure_is_not_repeated(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misconfiguration is translated *and* declined by the same loop."""
    calls = 0

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        nonlocal calls
        calls += 1
        raise ProviderError("GOOGLE_API_KEY (or LLM_API_KEY) is not set.")

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    service = AIService(
        make_settings(),
        synthesis_policy=RetryPolicy(max_attempts=5, initial_backoff=0.0, max_backoff=0.0),
    )
    with pytest.raises(ConfigurationError):
        await service.synthesize("a question", [_source(1)])

    assert calls == 1


async def test_a_synthesis_timeout_is_abandoned_rather_than_repeated(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One provider call, then stop — even with three attempts available."""
    calls = 0

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        nonlocal calls
        calls += 1
        time.sleep(5)
        return _answer(question, sources)

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    service = AIService(
        make_settings(synthesis_timeout_seconds=0.05),
        synthesis_policy=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0),
    )
    with pytest.raises(UpstreamError, match="deadline"):
        await service.synthesize("a question", [_source(1)])

    assert calls == 1


async def test_the_deadline_abandons_the_await_without_stopping_the_thread(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit on the deadline, demonstrated rather than asserted."""
    completion: list[float] = []
    finished = threading.Event()

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        time.sleep(0.3)
        completion.append(time.perf_counter())
        finished.set()
        return _answer(question, sources)

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    service = AIService(make_settings(synthesis_timeout_seconds=0.05))
    with pytest.raises(UpstreamError, match="deadline"):
        await service.synthesize("a question", [_source(1)])
    abandoned_at = time.perf_counter()

    # The caller has given up...
    assert completion == []
    # ...and the call it gave up on still finishes, afterwards.
    assert await asyncio.to_thread(finished.wait, 5.0), "the worker thread never finished"
    assert completion[0] > abandoned_at


async def test_synthesis_is_governed_by_its_own_deadline_not_the_fetch_one(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two budgets are separate settings, and this is the proof."""
    calls = 0

    def fake(question: str, sources: list[Source], *, llm: object = None) -> AnswerWithCitations:
        nonlocal calls
        calls += 1
        time.sleep(5)
        return _answer(question, sources)

    monkeypatch.setattr("ai.synthesizer.synthesize", fake)

    service = AIService(
        make_settings(per_source_timeout_seconds=30.0, synthesis_timeout_seconds=0.05),
        synthesis_policy=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0),
    )
    with pytest.raises(UpstreamError, match="deadline"):
        await service.synthesize("a question", [_source(1)])

    assert calls == 1


# Lifecycle


async def test_aclose_is_idempotent(make_settings: SettingsFactory) -> None:
    """Cleanup runs from ``finally`` blocks, so it must tolerate repeats."""
    service = AIService(make_settings())
    await service.aclose()
    await service.aclose()


async def test_aclose_closes_a_client_it_built(make_settings: SettingsFactory) -> None:
    service = AIService(make_settings())
    client = service._http()
    assert not client.is_closed

    await service.aclose()

    assert client.is_closed


async def test_aclose_survives_a_client_that_fails_to_close(
    make_settings: SettingsFactory,
) -> None:
    """It runs from a ``finally`` block, where raising would replace the real failure."""

    class Stubborn(httpx.AsyncClient):
        async def aclose(self) -> None:
            raise RuntimeError("connection already gone")

    service = AIService(make_settings())
    service._client = Stubborn()
    service._owns_client = True

    await service.aclose()

    # Not left half-closed: the reference is dropped either way.
    assert service._client is None


async def test_a_client_is_not_built_until_it_is_needed(make_settings: SettingsFactory) -> None:
    """Construction must not open sockets, so a bad config fails before any do."""
    service = AIService(make_settings())

    assert service._client is None
