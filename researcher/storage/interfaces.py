"""Defines the async methods that cache and session stores must provide."""

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
    """The application's persistence surface, and its lifecycle."""

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
