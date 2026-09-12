"""Construction of the shared outbound HTTP client.

All three source fetchers accept an ``httpx.AsyncClient``, and the topic
contract is explicit that they should be given the *same* one: connection reuse
roughly doubles throughput at the query sizes this application produces. A
single client also gives one place to set the timeouts, the connection ceiling
and the ``User-Agent``, instead of three call sites that can disagree.
"""

from __future__ import annotations

import httpx

from researcher.config import Settings

__all__ = ["USER_AGENT", "build_client"]

#: Wikipedia and arXiv are both key-free and rate-limited, and both ask callers
#: to identify themselves. An anonymous client is a poor citizen and is the
#: first thing throttled, so the contact URL is part of the string.
USER_AGENT = "researcher/0.1 (SWENG final project; +https://github.com/ii1ahe/research-assistant)"


def build_client(settings: Settings) -> httpx.AsyncClient:
    """Return a client configured for this application's outbound traffic.

    The connection ceiling is derived from ``max_parallel_sources`` rather than
    set to a round number. That setting is the true upper bound on concurrent
    fetches, so sizing the pool to it guarantees the pool is never the thing
    serialising work the orchestrator's semaphore already permitted — a
    discrepancy that would show up only as unexplained latency under load.

    The per-request timeout is set from ``per_source_timeout_seconds``, the same
    value the application enforces with :func:`researcher.services.resilience.
    deadline`. Duplicating it is intentional: the enforced deadline is what
    bounds the request, and this is the fallback for any call that somehow
    escapes it — for instance a redirect chain that outlives the original
    request's timeout.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.per_source_timeout_seconds),
        limits=httpx.Limits(
            max_connections=settings.max_parallel_sources,
            max_keepalive_connections=settings.max_parallel_sources,
        ),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
