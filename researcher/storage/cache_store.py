"""Stores source results in PostgreSQL and returns entries whose cache keys match and have not
expired.
"""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg

from researcher.errors import StorageError
from researcher.models import CacheEntry, CacheKey, utc_now
from researcher.storage._driver import DRIVER_ERRORS, READ_ERRORS, translate

__all__ = ["PostgresSourceCache"]

logger = logging.getLogger(__name__)

# These key columns must match the primary key in 001_initial_schema.sql.
_KEY_PREDICATE = (
    "source = $1 AND query = $2 AND provider = $3 AND max_results = $4 AND schema_version = $5"
)

_SELECT_FRESH = f"SELECT payload FROM source_cache WHERE {_KEY_PREDICATE} AND expires_at > $6"

_UPSERT = """
INSERT INTO source_cache
    (source, query, provider, max_results, schema_version,
     payload, fetched_at, expires_at)
VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
ON CONFLICT (source, query, provider, max_results, schema_version)
DO UPDATE SET
    payload    = EXCLUDED.payload,
    fetched_at = EXCLUDED.fetched_at,
    expires_at = EXCLUDED.expires_at
"""

_PURGE = "DELETE FROM source_cache WHERE expires_at <= $1"


def _key_params(key: CacheKey) -> tuple[str, str, str, int, int]:
    """Flatten a key into the five positional parameters the statements expect."""
    return (
        key.source.value,
        key.query,
        key.provider,
        key.max_results,
        key.schema_version,
    )


class PostgresSourceCache:
    """A :class:`~researcher.storage.interfaces.SourceCache` over an asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize the cache.

        Args:
            pool: An open pool. Not owned — closing it is
                :class:`~researcher.storage.postgres.PostgresStorage`'s job, so a
                cache and a session repository can share one pool without either
                closing it out from under the other.
        """
        self._pool = pool

    async def get(self, key: CacheKey, *, now: datetime | None = None) -> CacheEntry | None:
        """Return the fresh cached entry for ``key``, or ``None``.

        Raises:
            StorageError: The lookup failed, or the stored payload does not
                match the key it was filed under.
        """
        moment = now if now is not None else utc_now()
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(_SELECT_FRESH, *_key_params(key), moment)
            if row is None:
                return None

            entry = CacheEntry.model_validate_json(row["payload"])
            if entry.key != key:
                # Reject a payload whose cache key differs from the lookup key.
                # StorageError is not in READ_ERRORS, so it passes through unchanged.
                raise StorageError(
                    "cached entry does not match the key it was stored under",
                    source="storage",
                )
            return entry
        except READ_ERRORS as exc:
            raise translate(exc, "reading a cached result") from exc

    async def put(self, entry: CacheEntry) -> None:
        """Store ``entry``, replacing any entry already stored under its key.

        Raises:
            StorageError: The write failed.
        """
        params = (
            *_key_params(entry.key),
            entry.model_dump_json(),
            entry.fetched_at,
            entry.expires_at,
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_UPSERT, *params)
        except DRIVER_ERRORS as exc:
            raise translate(exc, "writing a cached result") from exc

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete every entry that expired at or before ``now``.

        Returns:
            The number of rows removed.

        Raises:
            StorageError: The deletion failed.
        """
        moment = now if now is not None else utc_now()
        try:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(_PURGE, moment)
        except DRIVER_ERRORS as exc:
            raise translate(exc, "purging expired cache entries") from exc

        # asyncpg returns a command tag such as DELETE 3; read the count from it.
        return int(tag.rsplit(" ", 1)[-1])
