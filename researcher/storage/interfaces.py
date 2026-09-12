"""Storage contracts.

The application depends on the protocols in this module and never on a driver,
a pool or a SQL string. ADR-004 records why: the project requires real
PostgreSQL persistence *and* a test suite that runs offline with no database, so
the seam has to be structural rather than a concrete class. Two implementations
satisfy these protocols — the PostgreSQL one used by the application
(``postgres.py`` driving ``cache_store.py`` and ``session_repository.py``) and an
in-memory one used by the tests.

Contract for implementers
-------------------------
- **Asynchronous.** The application's retrieval path is asynchronous and the
  driver is ``asyncpg``, so every method is a coroutine. An implementation that
  is synchronous underneath (the in-memory one) still exposes ``async def``.
- **Raise :class:`~researcher.errors.StorageError`.** Driver exceptions must be
  translated at this boundary. Nothing above storage may need to know that
  ``asyncpg`` exists, so an escaping ``asyncpg.PostgresError`` is a bug.
- **Never silently swallow.** A failed cache *read* may reasonably be reported
  to the caller as a miss, but a failed session *write* must be reported, because
  the run then has to be described to the user as unsaved rather than as a clean
  success. Implementations raise; deciding what a failure means is the caller's
  job, since only the caller knows whether the operation was optional.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from researcher.models import CacheEntry, CacheKey, ResearchSession

__all__ = [
    "SessionRepository",
    "SourceCache",
    "Storage",
]


class SourceCache(Protocol):
    """Storage for retrieval results, keyed by :class:`~researcher.models.CacheKey`."""

    async def get(self, key: CacheKey, *, now: datetime | None = None) -> CacheEntry | None:
        """Return the cached entry for ``key``, or ``None``.

        Entries whose expiry has passed are reported as ``None``. Filtering
        happens here rather than at the call site so that no caller can forget
        to check and serve a stale result.

        Args:
            key: Identity of the requested result.
            now: Override for the current time. Tests use this to exercise
                expiry without sleeping.

        Returns:
            A fresh :class:`~researcher.models.CacheEntry`, or ``None`` if there
            is no entry for ``key`` or the entry is stale.

        Raises:
            StorageError: The lookup could not be performed.
        """
        ...

    async def put(self, entry: CacheEntry) -> None:
        """Store ``entry``, replacing any entry already stored under its key.

        Writing is an upsert: fetching the same query again must refresh the
        entry rather than fail on a duplicate key.

        Raises:
            StorageError: The write could not be performed.
        """
        ...

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete every entry that expired at or before ``now``.

        Returns:
            The number of entries removed.

        Raises:
            StorageError: The deletion could not be performed.
        """
        ...


class SessionRepository(Protocol):
    """Durable storage for completed research sessions."""

    async def save(self, session: ResearchSession) -> None:
        """Persist ``session``, replacing any session already stored under its id.

        The snapshot is stored whole, including each source's snippet. The
        supplied ``AnswerWithCitations.to_dict()`` drops snippets when it
        flattens references, so it is a presentation format and must not be the
        archival record.

        Raises:
            StorageError: The session could not be written.
        """
        ...

    async def get(self, session_id: uuid.UUID) -> ResearchSession | None:
        """Return the stored session with this id, or ``None`` if there is none.

        Raises:
            StorageError: The lookup could not be performed.
        """
        ...

    async def list_recent(self, *, limit: int = 20) -> Sequence[ResearchSession]:
        """Return up to ``limit`` most recently created sessions, newest first.

        Raises:
            StorageError: The lookup could not be performed.
        """
        ...


class Storage(Protocol):
    """The application's persistence surface, and its lifecycle.

    Bundling the two repositories with ``aclose`` gives the bootstrap one object
    to construct, one object to inject, and one object to shut down, so a
    connection pool cannot be opened and then leaked on an error path.
    """

    @property
    def cache(self) -> SourceCache:
        """Retrieval-result cache."""
        ...

    @property
    def sessions(self) -> SessionRepository:
        """Completed-session store."""
        ...

    async def aclose(self) -> None:
        """Release every resource held by this storage.

        Must be safe to call more than once, so that a cleanup ``finally`` block
        cannot itself raise and mask the original failure.
        """
        ...
