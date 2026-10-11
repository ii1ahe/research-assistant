"""Reads and writes cached sources, reports cache failures, and removes expired entries."""

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
    """A cache read result. A storage failure is recorded and treated as a miss."""

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
    """Describe a storage failure without holding the live exception."""
    return FailureDetail(
        code=exc.code,
        message=exc.message,
        source=exc.source,
        retryable=exc.retryable,
    )


class CacheService:
    """Read and write cached sources. Storage failures are returned as details so retrieval can
    continue.
    """

    def __init__(self, cache: SourceCache | None, settings: Settings) -> None:
        """Initialize the service.

        Args:
            cache: The backing store, from ``Storage.cache``. ``None`` means
                there is none — the application is running without a database —
                and every operation becomes the no-op that the corresponding
                failure would have produced anyway. It is not an error state:
                ``DATABASE_URL`` is optional by design.
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
        if self._cache is None:
            return CacheLookup()
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

        Args:
            key: Identity of the result being stored.
            sources: The sources to cache, in citation order.
            now: Override for the current time, so expiry is testable.

        Returns:
            Whether the write succeeded, and why not if it did not.
        """
        if self._cache is None:
            return CacheWrite()
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
        """Delete expired entries and return their count. Log a storage failure and return zero."""
        if self._cache is None:
            return 0
        try:
            return await self._cache.purge_expired(now=now)
        except StorageError as exc:
            logger.warning("purging expired cache entries failed: %s", exc)
            return 0

    async def purge_expired_strict(self, *, now: datetime | None = None) -> int:
        """Delete expired entries for the CLI command. Let storage failures reach the caller."""
        if self._cache is None:
            return 0
        return await self._cache.purge_expired(now=now)
