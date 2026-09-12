"""In-memory storage, the offline test double required by ADR-004.

The brief asks for real PostgreSQL persistence *and* a suite that passes with
the network down. Those are only compatible if storage sits behind an
interface, which is why these classes exist alongside the PostgreSQL ones and
not instead of them.

Sharing the protocols means business logic is exercised against the same shape
in tests as in production, so the double can mask a SQL mistake but not a logic
one. The SQL itself is covered by integration tests against a real database,
which is the point ADR-004 makes about mocks alone being insufficient.

Freshness and ordering deliberately mirror the SQL rather than approximating
it: both compare ``expires_at <= now`` for staleness and both order by
``(created_at, id)`` descending. Two implementations that disagreed about an
edge would make the suite pass while production served something else.
"""

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
    """A :class:`~researcher.storage.interfaces.SourceCache` held in a dict.

    Keyed by :class:`~researcher.models.CacheKey` directly, which works because
    the model is frozen and therefore hashable.
    """

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
        # uuid.UUID orders by its big-endian integer value, which is the same
        # order PostgreSQL applies to the uuid type, so this tiebreaker and the
        # SQL `ORDER BY created_at DESC, id DESC` agree.
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
