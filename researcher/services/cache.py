"""Caching retrieval results, without ever letting the cache break a run.

The cache is an optimisation, so every failure it can produce has a correct
answer that is merely slower — fetch the source, or store nothing. The one
thing a cache failure must not do is end the request. Both operations here
therefore catch :class:`~researcher.errors.StorageError` and hand it back as a
value, leaving the caller to decide whether it is worth mentioning.

The two directions are treated differently on purpose. A failed *read* is
indistinguishable from a miss and is reported as one, because the fallback is
exactly the work that would have happened anyway. A failed *write* is never
silently discarded: a cache that has quietly stopped storing looks identical to
a working one until the bill for re-fetching everything arrives.

Useful caching also depends on getting the key right, which is why
:class:`~researcher.models.CacheKey` carries the provider and the result limit
and not just the query — see :meth:`Orchestrator._cache_key`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ai.schemas import Source
from researcher.config import Settings
from researcher.errors import StorageError
from researcher.models import CacheEntry, CacheKey, FailureDetail, utc_now
from researcher.storage.interfaces import SourceCache

__all__ = ["CacheLookup", "CacheService", "CacheWrite"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """What a cache read produced.

    ``failure`` is set when the read could not be performed at all, which the
    caller should report — but only after having treated the lookup as a miss
    and fetched the source regardless.
    """

    entry: CacheEntry | None = None
    failure: FailureDetail | None = None

    @property
    def hit(self) -> bool:
        """Whether a fresh entry was found."""
        return self.entry is not None


@dataclass(frozen=True, slots=True)
class CacheWrite:
    """What a cache write produced.

    ``stored`` is False both when the write failed and when there was nothing
    worth writing; ``failure`` distinguishes the two.
    """

    stored: bool = False
    failure: FailureDetail | None = None


def _detail(exc: StorageError) -> FailureDetail:
    """Describe a storage failure without holding the live exception.

    ``message`` is already safe — the storage boundary keeps driver text and the
    DSN in ``__cause__`` rather than in the message a user would see.
    """
    return FailureDetail(
        code=exc.code,
        message=exc.message,
        source=exc.source,
        retryable=exc.retryable,
    )


class CacheService:
    """Reads and writes cached retrieval results, and reports what went wrong.

    Raises nothing for a storage failure. The caller here is a retrieval path
    that can always continue without the cache, and an exception there would
    turn an optimisation into a dependency.
    """

    def __init__(self, cache: SourceCache, settings: Settings) -> None:
        """Initialize the service.

        Args:
            cache: The backing store, from ``Storage.cache``.
            settings: Supplies ``cache_ttl_seconds``.
        """
        self._cache = cache
        self._settings = settings

    async def lookup(self, key: CacheKey, *, now: datetime | None = None) -> CacheLookup:
        """Return the cached entry for ``key``, or the reason there is none.

        Args:
            key: Identity of the wanted result.
            now: Override for the current time, so expiry is testable without
                sleeping. Staleness itself is decided by the store, not here.

        Returns:
            A hit, an ordinary miss, or a miss caused by a storage failure.
        """
        try:
            entry = await self._cache.get(key, now=now)
        except StorageError as exc:
            logger.warning("cache read failed for %s/%s: %s", key.source.value, key.query, exc)
            return CacheLookup(failure=_detail(exc))
        if entry is None:
            logger.debug("cache miss for %s/%s", key.source.value, key.query)
            return CacheLookup()
        logger.debug("cache hit for %s/%s", key.source.value, key.query)
        return CacheLookup(entry=entry)

    async def store(
        self,
        key: CacheKey,
        sources: Sequence[Source],
        *,
        now: datetime | None = None,
    ) -> CacheWrite:
        """Cache ``sources`` under ``key``.

        An empty result is cached rather than skipped. "This query has no
        results" is worth remembering: without it, every run repeats the lookup
        that already came back empty, which is precisely the traffic a cache
        exists to avoid. The TTL bounds how long that judgement stands.

        Args:
            key: Identity of the result being stored.
            sources: The sources to cache, in citation order.
            now: Override for the current time, so expiry is testable.

        Returns:
            Whether the write succeeded, and why not if it did not.
        """
        moment = now or utc_now()
        entry = CacheEntry(
            key=key,
            sources=tuple(sources),
            fetched_at=moment,
            expires_at=moment + timedelta(seconds=self._settings.cache_ttl_seconds),
        )
        try:
            await self._cache.put(entry)
        except StorageError as exc:
            logger.warning("cache write failed for %s/%s: %s", key.source.value, key.query, exc)
            return CacheWrite(failure=_detail(exc))
        logger.debug("cached %d source(s) for %s/%s", len(sources), key.source.value, key.query)
        return CacheWrite(stored=True)

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete entries that have expired. Returns how many were removed.

        Housekeeping rather than part of a request path, so a failure here is
        logged and reported as zero removals instead of propagating.
        """
        try:
            return await self._cache.purge_expired(now=now)
        except StorageError as exc:
            logger.warning("purging expired cache entries failed: %s", exc)
            return 0
