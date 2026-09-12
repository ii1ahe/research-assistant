"""Wikipedia retrieval, against a mocked API.

This module exists because the supplied fetcher cannot answer a question: it
searches with ``opensearch``, which prefix-matches the whole query against
article titles, so a natural-language question returns nothing. The tests here
are therefore about two things at once — that the full-text search is what gets
called, and that everything downstream of it behaves the way the supplied
fetcher's callers already expect.

The API is mocked with ``respx`` rather than by replacing the fetcher, because
what is being tested *is* the request that goes out: which endpoint, which
parameters, and whether the question survives the trip unedited.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from ai.providers.base import ProviderError
from researcher.services import wikipedia as wikipedia_module
from researcher.services.wikipedia import fetch_wikipedia_fulltext

pytestmark = pytest.mark.asyncio

SEARCH_URL = "https://en.wikipedia.org/w/api.php"
SUMMARY_PATTERN = r"https://en\.wikipedia\.org/api/rest_v1/page/summary/.*"

#: The kind of question the supplied demo set is made of, and the kind the
#: supplied fetcher returns nothing for.
QUESTION = "What is photosynthesis and what are its main stages?"


def _search(*titles: str) -> dict[str, object]:
    """A MediaWiki search response carrying the given titles."""
    return {"query": {"search": [{"title": title} for title in titles]}}


def _summaries(payloads: dict[str, dict[str, object]]):
    """Build a side effect that answers each summary request by title."""

    def respond(request: httpx.Request) -> Response:
        title = str(request.url).rsplit("/", 1)[-1].replace("_", " ")
        if title not in payloads:
            return Response(404)
        return Response(200, json=payloads[title])

    return respond


def _article(title: str, extract: str = "An extract.", url: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {"title": title, "extract": extract}
    if url is not None:
        body["content_urls"] = {"desktop": {"page": url}}
    return body


# ---------------------------------------------------------------------------
# The request that goes out
# ---------------------------------------------------------------------------


async def test_the_question_is_sent_to_the_full_text_search(respx_mock: respx.MockRouter) -> None:
    """The whole fix in one assertion: ``list=search``, and the question verbatim.

    Not a keyword, not a rewritten query. Full-text ranking is what handles the
    natural-language form, so the query has nothing to be stripped down to.
    """
    route = respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search()))
    respx_mock.get(url__regex=SUMMARY_PATTERN).mock(return_value=Response(404))

    async with httpx.AsyncClient() as client:
        await fetch_wikipedia_fulltext(QUESTION, client=client)

    params = route.calls[0].request.url.params
    assert params["action"] == "query"
    assert params["list"] == "search"
    assert params["srsearch"] == QUESTION
    assert params["format"] == "json"


async def test_the_search_is_not_the_supplied_opensearch(
    respx_mock: respx.MockRouter,
) -> None:
    """Guards the regression directly, since the symptom is silence.

    Using the wrong API here does not raise — it returns an empty list, which
    the application honestly reports as "no results". Nothing would look broken
    except that Wikipedia never contributes anything.
    """
    route = respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search()))
    respx_mock.get(url__regex=SUMMARY_PATTERN).mock(return_value=Response(404))

    async with httpx.AsyncClient() as client:
        await fetch_wikipedia_fulltext(QUESTION, client=client)

    params = route.calls[0].request.url.params
    assert params.get("action") != "opensearch"
    assert "search" not in params, "the opensearch API takes 'search', not 'srsearch'"


async def test_the_client_identifies_itself(respx_mock: respx.MockRouter) -> None:
    """Wikipedia blocks automated clients that do not, so this is not cosmetic."""
    route = respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search()))

    async with httpx.AsyncClient() as client:
        await fetch_wikipedia_fulltext(QUESTION, client=client)

    assert "researcher" in route.calls[0].request.headers.get("user-agent", "")


async def test_a_blank_question_asks_the_api_nothing(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search()))

    async with httpx.AsyncClient() as client:
        assert await fetch_wikipedia_fulltext("   ", client=client) == []

    assert not route.called


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------


async def test_the_ranked_hits_become_sources(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(SEARCH_URL).mock(
        return_value=Response(200, json=_search("Photosynthesis", "Calvin cycle"))
    )
    respx_mock.get(url__regex=SUMMARY_PATTERN).mock(
        side_effect=_summaries(
            {
                "Photosynthesis": _article(
                    "Photosynthesis",
                    "The process used by plants to convert light into energy.",
                    url="https://en.wikipedia.org/wiki/Photosynthesis",
                ),
                "Calvin cycle": _article("Calvin cycle", "A series of redox reactions.", url=None),
            }
        )
    )

    async with httpx.AsyncClient() as client:
        sources = await fetch_wikipedia_fulltext(QUESTION, max_results=2, client=client)

    assert [source.title for source in sources] == ["Photosynthesis", "Calvin cycle"]
    assert sources[0].snippet.startswith("The process used by plants")
    assert sources[0].url == "https://en.wikipedia.org/wiki/Photosynthesis"
    # The nested title is underscored for the URL, and the fallback is used
    # when the API omits `content_urls`.
    assert sources[1].url == "https://en.wikipedia.org/wiki/Calvin_cycle"
    assert {source.origin for source in sources} == {"wikipedia"}


async def test_the_ranking_order_is_preserved(respx_mock: respx.MockRouter) -> None:
    """Search order is the API's ranking, and it is carried through untouched.

    ``max_results`` is applied by the API, so a source that asked for two gets
    the *best* two rather than the first two to come back.
    """
    respx_mock.get(SEARCH_URL).mock(
        return_value=Response(200, json=_search("First", "Second", "Third"))
    )
    respx_mock.get(url__regex=SUMMARY_PATTERN).mock(
        side_effect=_summaries({t: _article(t) for t in ("First", "Second", "Third")})
    )

    async with httpx.AsyncClient() as client:
        sources = await fetch_wikipedia_fulltext(QUESTION, client=client)

    assert [source.title for source in sources] == ["First", "Second", "Third"]


async def test_the_result_limit_reaches_the_api(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search()))

    async with httpx.AsyncClient() as client:
        await fetch_wikipedia_fulltext(QUESTION, max_results=7, client=client)

    assert route.calls[0].request.url.params["srlimit"] == "7"


async def test_no_matches_is_empty_and_not_an_error(respx_mock: respx.MockRouter) -> None:
    """The search worked; there was simply nothing. That is not an outage."""
    respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json={"query": {"search": []}}))

    async with httpx.AsyncClient() as client:
        assert await fetch_wikipedia_fulltext(QUESTION, client=client) == []


async def test_an_article_with_no_extract_is_skipped(respx_mock: respx.MockRouter) -> None:
    """An empty summary is not a source — it would cite nothing."""
    respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search("Empty", "Useful")))
    respx_mock.get(url__regex=SUMMARY_PATTERN).mock(
        side_effect=_summaries(
            {"Empty": _article("Empty", extract="   "), "Useful": _article("Useful")}
        )
    )

    async with httpx.AsyncClient() as client:
        sources = await fetch_wikipedia_fulltext(QUESTION, client=client)

    assert [source.title for source in sources] == ["Useful"]


async def test_one_failing_article_does_not_lose_the_others(
    respx_mock: respx.MockRouter,
) -> None:
    """The supplied fetcher's tolerance, kept: a dead link is not a dead source."""
    respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search("Gone", "Fine")))
    respx_mock.get(url__regex=SUMMARY_PATTERN).mock(
        side_effect=_summaries({"Fine": _article("Fine")})
    )

    async with httpx.AsyncClient() as client:
        sources = await fetch_wikipedia_fulltext(QUESTION, client=client)

    assert [source.title for source in sources] == ["Fine"]


async def test_a_title_the_api_omits_is_ignored(respx_mock: respx.MockRouter) -> None:
    """A malformed hit must not become a request for a title of ``None``."""
    respx_mock.get(SEARCH_URL).mock(
        return_value=Response(
            200, json={"query": {"search": [{"title": "Real"}, {"nos": "title"}]}}
        )
    )
    respx_mock.get(url__regex=SUMMARY_PATTERN).mock(
        side_effect=_summaries({"Real": _article("Real")})
    )

    async with httpx.AsyncClient() as client:
        sources = await fetch_wikipedia_fulltext(QUESTION, client=client)

    assert [source.title for source in sources] == ["Real"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_a_search_failure_raises_so_the_caller_can_classify_it(
    respx_mock: respx.MockRouter,
) -> None:
    """``ProviderError`` is the type ``AIService`` translates.

    Raising the application's own error here would skip that classification and
    leave the retry decision to a layer that cannot make it.
    """
    respx_mock.get(SEARCH_URL).mock(return_value=Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderError, match="Wikipedia search failed"):
            await fetch_wikipedia_fulltext(QUESTION, client=client)


async def test_a_search_response_that_is_not_json_is_a_provider_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(SEARCH_URL).mock(return_value=Response(200, text="<html>nope</html>"))

    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderError):
            await fetch_wikipedia_fulltext(QUESTION, client=client)


# ---------------------------------------------------------------------------
# Client ownership
# ---------------------------------------------------------------------------


async def test_an_injected_client_is_left_open(respx_mock: respx.MockRouter) -> None:
    """Whoever built the client closes it — the same rule the rest of the app follows."""
    respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search()))

    async with httpx.AsyncClient() as client:
        await fetch_wikipedia_fulltext(QUESTION, client=client)

        assert not client.is_closed


async def test_a_client_it_built_itself_is_closed(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No client is leaked when the fetcher is called without one."""
    built: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def recording(*args: object, **kwargs: object) -> httpx.AsyncClient:
        client = real(*args, **kwargs)  # type: ignore[arg-type]
        built.append(client)
        return client

    respx_mock.get(SEARCH_URL).mock(return_value=Response(200, json=_search()))
    monkeypatch.setattr(wikipedia_module.httpx, "AsyncClient", recording)

    await fetch_wikipedia_fulltext(QUESTION)

    assert len(built) == 1
    assert built[0].is_closed
