"""Searches Wikipedia using full-text search and turns article summaries into source records."""

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

# Identify this client on each Wikipedia request.
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
                # Search returns titles; the next request gets their summaries.
                "srprop": "",
                "format": "json",
            },
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        # Keep JSON parsing inside try so an HTML error page becomes a ProviderError.
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
    """Fetch one summary per title, skipping the ones that have none."""
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
    """Search Wikipedia and return article summaries in ranking order.

    Args:
        query: The original question, sent without changes.
        max_results: Number of articles to request.
        client: Shared HTTP client. If omitted, a local client is opened and closed.

    Returns:
        Up to max_results sources. No matches returns an empty list.

    Raises:
        ProviderError: The search request fails. Failed individual articles are skipped.
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
