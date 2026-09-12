"""Wikipedia retrieval that can answer a question rather than a title.

The supplied ``ai.sources.fetch_wikipedia`` searches with the MediaWiki
``opensearch`` API, which prefix-matches the *entire* query against article
titles. That is the wrong tool for this application, and the difference is not
subtle — measured against the live API:

===============================  ====================================
Query                            Titles returned
===============================  ====================================
``What is photosynthesis and``   0
``what are its main stages?``
``photosynthesis``                3
``photosynthesis main stages``    0
``photosynthesis stages``         0
``What is photosynthesis``        0
===============================  ====================================

The pattern is the point: only a bare word that happens to begin a title
matches. No transformation of a natural-language question fixes this, because
the failure is in the API's semantics rather than in the phrasing — every
multi-word query above is a reasonable search and every one returns nothing.
Each of the five supplied demo questions is natural-language, and three of them
declare Wikipedia as an expected source, so without this module Wikipedia
contributes nothing to any of them.

So the *search step* is replaced and nothing else. Candidate titles come from
the MediaWiki search API (``list=search``), which does real full-text ranking
and answers the untouched question correctly — the same query that returns
nothing above returns ``Photosynthesis`` first when asked this way. Everything
after that is the supplied behaviour, unchanged: the same article-summary
endpoint, the same ``Source`` shape, the same tolerance of one article failing
without failing the fetch.

``ai/`` is immutable, so this lives on our side of the boundary. It is selected
by :attr:`~researcher.config.Settings.wikipedia_search`, which defaults to
``fulltext``; setting it to ``opensearch`` restores the supplied fetcher
exactly, so nothing supplied has been removed or made unreachable.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ai.providers.base import ProviderError
from ai.schemas import Source

__all__ = ["fetch_wikipedia_fulltext"]

logger = logging.getLogger(__name__)

#: The MediaWiki action API, same host the supplied fetcher uses.
_API_URL = "https://en.wikipedia.org/w/api.php"

#: Article summaries, same endpoint and same shape the supplied fetcher reads.
_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

#: Wikipedia asks that automated clients identify themselves. Sent with every
#: request, because a client that does not is liable to be blocked outright.
_USER_AGENT = (
    "researcher-final-project/0.1 (coursework; https://github.com/ii1ahe/research-assistant)"
)


async def _search_titles(query: str, limit: int, client: httpx.AsyncClient) -> list[str]:
    """Return the titles the full-text search ranks highest for ``query``.

    Raises:
        ProviderError: The search request itself failed. Not caught here — the
            caller translates it, so every failure reaches the application
            through the same door the supplied fetchers use.
    """
    try:
        response = await client.get(
            _API_URL,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                # The ranking is all this call is for; the summary endpoint
                # below supplies the text, so asking for snippets here would
                # only be discarded.
                "srprop": "",
                "format": "json",
            },
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        # Inside the ``try`` on purpose. A proxy error page or a captive portal
        # answers 200 with HTML, and a ``JSONDecodeError`` escaping from here
        # would not be a :class:`ProviderError`, so ``AIService`` could not
        # classify it and it would leave ``fetch_source`` as an exception —
        # breaking the rule that a source failure is an outcome.
        payload = response.json()
    except Exception as exc:
        raise ProviderError(f"Wikipedia search failed: {exc}") from exc

    if not isinstance(payload, dict):
        return []
    hits = payload.get("query", {}).get("search", [])
    return [
        hit["title"] for hit in hits if isinstance(hit, dict) and isinstance(hit.get("title"), str)
    ]


async def _summaries(titles: list[str], client: httpx.AsyncClient) -> list[Source]:
    """Fetch one summary per title, skipping the ones that have none.

    A single article that is missing, renamed or has no extract must not cost
    the whole fetch — the same tolerance the supplied fetcher shows, and the
    reason a source can return two good results instead of an error.
    """
    sources: list[Source] = []
    for title in titles:
        try:
            response = await client.get(
                _SUMMARY_URL.format(title=title.replace(" ", "_")),
                headers={"User-Agent": _USER_AGENT},
            )
            response.raise_for_status()
        except Exception as exc:
            logger.debug("no summary for %r: %s", title, exc)
            continue

        body = response.json()
        extract = (body.get("extract") or "").strip()
        if not extract:
            continue

        sources.append(
            Source(
                title=body.get("title", title),
                url=body.get("content_urls", {}).get("desktop", {}).get("page")
                or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                snippet=extract,
                origin="wikipedia",
            )
        )
    return sources


async def fetch_wikipedia_fulltext(
    query: str,
    *,
    max_results: int = 3,
    client: Any = None,
) -> list[Source]:
    """Search Wikipedia's full text and return the top-N article summaries.

    Mirrors the signature of ``ai.sources.fetch_wikipedia`` so the two are
    interchangeable at the call site in
    :meth:`~researcher.services.ai_service.AIService._fetch`.

    Args:
        query: The question, exactly as the user typed it. It is passed to the
            search API untouched: full-text ranking is what makes this work, so
            there is nothing to strip, shorten or rewrite.
        max_results: Articles to request.
        client: An HTTP client to reuse. One is built and closed locally when
            omitted, matching the supplied fetchers' behaviour so this is a
            drop-in for them.

    Returns:
        Up to ``max_results`` sources, in the search engine's ranking order.
        Empty when the search matched nothing — the application reports that as
        an empty source, not as a failure.

    Raises:
        ProviderError: The search request failed. An individual article failing
            is not this; that is skipped.
    """
    if not query.strip():
        return []

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=10.0)

    try:
        titles = await _search_titles(query, max_results, client)
        if not titles:
            return []
        return await _summaries(titles, client)
    finally:
        if own_client:
            await client.aclose()
