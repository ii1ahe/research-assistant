"""PostgreSQL implementation of the source-result cache.

The table's primary key is the whole :class:`~researcher.models.CacheKey`, so
``put`` is a single ``INSERT ... ON CONFLICT`` and freshness is filtered in SQL
rather than in Python. Both choices push decisions into the database that would
otherwise be re-implemented — and eventually mis-implemented — per call site.

The ``payload`` column holds the entire
:class:`~researcher.models.CacheEntry` as JSON, while the key is *also* spread
across columns. That is deliberate redundancy: the columns are the index the
lookup runs against, and the payload is the record. Because the two are written
from one object they cannot disagree at rest, and a read checks that they did
not — see :meth:`PostgresSourceCache.get`.
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

#: Matches the primary key declared in ``migrations/001_initial_schema.sql``.
#: The two must agree or ``ON CONFLICT`` fails at runtime, so this string and
#: the migration are a pair.
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

        Staleness is decided by the database (``expires_at > now``), using the
        same strict comparison as
        :meth:`~researcher.models.CacheEntry.is_fresh`, so an entry that is
        exactly at its expiry instant counts as stale in both places.

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
                # Only reachable if a row were written by something other than
                # put, or if the payload column were edited by hand. Returning
                # it would serve one query's results for another, so it is
                # refused. StorageError is not in READ_ERRORS, so it propagates
                # rather than being re-translated below.
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

        # asyncpg reports the affected count in the command tag ("DELETE 3")
        # rather than returning it, which is why this parses the tag.
        return int(tag.rsplit(" ", 1)[-1])
