"""Provides in-memory cache and session stores for tests and runs that do not need PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from researcher.models import CacheEntry, CacheKey, ResearchSession, utc_now

__all__ = [
    "InMemorySessionRepository",
    "InMemorySourceCache",
    "InMemoryStorage",
]


class InMemorySourceCache:
    """A :class:`~researcher.storage.interfaces.SourceCache` held in a dict."""

    def __init__(self) -> None:
        """Initialize an empty cache."""
        self._entries: dict[CacheKey, CacheEntry] = {}

    async def get(self, key: CacheKey, *, now: datetime | None = None) -> CacheEntry | None:
        """Return the fresh cached entry for ``key``, or ``None``."""
        entry = self._entries.get(key)
        if entry is None or not entry.is_fresh(now=now):
            return None
        return entry

    async def put(self, entry: CacheEntry) -> None:
        """Store ``entry``, replacing any entry already stored under its key."""
        self._entries[entry.key] = entry

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete every entry that expired at or before ``now``."""
        moment = now if now is not None else utc_now()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= moment]
        for key in expired:
            del self._entries[key]
        return len(expired)


class InMemorySessionRepository:
    """A :class:`~researcher.storage.interfaces.SessionRepository` held in a dict."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._sessions: dict[uuid.UUID, ResearchSession] = {}

    async def save(self, session: ResearchSession) -> None:
        """Persist ``session``, replacing any session already stored under its id."""
        self._sessions[session.id] = session

    async def get(self, session_id: uuid.UUID) -> ResearchSession | None:
        """Return the stored session with this id, or ``None``."""
        return self._sessions.get(session_id)

    async def list_recent(self, *, limit: int = 20) -> Sequence[ResearchSession]:
        """Return up to ``limit`` most recently created sessions, newest first.

        Raises:
            ValueError: ``limit`` is less than one. Raised for the same reason
                the PostgreSQL implementation raises: so the two behave
                identically at the boundary rather than one returning nothing
                where the other would return everything.
        """
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        # UUID ordering matches the PostgreSQL created_at DESC, id DESC tiebreaker.
        ordered = sorted(
            self._sessions.values(),
            key=lambda session: (session.created_at, session.id),
            reverse=True,
        )
        return tuple(ordered[:limit])


class InMemoryStorage:
    """An in-memory :class:`~researcher.storage.interfaces.Storage` for tests."""

    def __init__(self) -> None:
        """Initialize empty repositories."""
        self._cache = InMemorySourceCache()
        self._sessions = InMemorySessionRepository()

    @property
    def cache(self) -> InMemorySourceCache:
        """Retrieval-result cache."""
        return self._cache

    @property
    def sessions(self) -> InMemorySessionRepository:
        """Completed-session store."""
        return self._sessions

    async def aclose(self) -> None:
        """Release nothing.

        Present so the type satisfies the protocol and callers need no special
        case for tests, and safe to call repeatedly.
        """
