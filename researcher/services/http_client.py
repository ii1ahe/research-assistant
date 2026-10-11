"""Creates the shared async HTTP client with timeouts, connection limits, and a User-Agent
header.
"""

from __future__ import annotations

import httpx

from researcher.config import Settings

__all__ = ["USER_AGENT", "build_client"]

# Wikipedia and arXiv ask automated clients to identify themselves.
USER_AGENT = "researcher/0.1 (SWENG final project; +https://github.com/ii1ahe/research-assistant)"


def build_client(settings: Settings) -> httpx.AsyncClient:
    """Return a client configured for this application's outbound traffic."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.per_source_timeout_seconds),
        limits=httpx.Limits(
            max_connections=settings.max_parallel_sources,
            max_keepalive_connections=settings.max_parallel_sources,
        ),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
