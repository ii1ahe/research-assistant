"""PostgreSQL implementation of the research-session store.

A session is stored as one JSON document reconstructed into
:class:`~researcher.models.ResearchSession` on read. The three scalar columns
alongside it are denormalised copies used by :meth:`list_recent` so it can order
and limit without parsing every document; see the migration for why they are
not constrained to the ``ResultStatus`` enumeration.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

import asyncpg

from researcher.models import ResearchSession
from researcher.storage._driver import DRIVER_ERRORS, READ_ERRORS, translate

__all__ = ["PostgresSessionRepository"]

logger = logging.getLogger(__name__)

_UPSERT = """
INSERT INTO research_sessions (id, created_at, status, question, payload)
VALUES ($1, $2, $3, $4, $5::jsonb)
ON CONFLICT (id) DO UPDATE SET
    created_at = EXCLUDED.created_at,
    status     = EXCLUDED.status,
    question   = EXCLUDED.question,
    payload    = EXCLUDED.payload
"""

_SELECT_ONE = "SELECT payload FROM research_sessions WHERE id = $1"

# `id` breaks ties so the order is total. Two sessions can share created_at:
# the column has microsecond resolution, and a fast test can create several
# within it. Without the tiebreaker, "newest first" would be arbitrary between
# runs, and a test asserting order would pass or fail by luck.
_SELECT_RECENT = """
SELECT payload
FROM research_sessions
ORDER BY created_at DESC, id DESC
LIMIT $1
"""


class PostgresSessionRepository:
    """A :class:`~researcher.storage.interfaces.SessionRepository` over an asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize the repository.

        Args:
            pool: An open pool. Not owned — closing it is
                :class:`~researcher.storage.postgres.PostgresStorage`'s job.
        """
        self._pool = pool

    async def save(self, session: ResearchSession) -> None:
        """Persist ``session``, replacing any session already stored under its id.

        Raises:
            StorageError: The write failed.
        """
        params = (
            session.id,
            session.created_at,
            session.status.value,
            session.request.question,
            session.model_dump_json(),
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_UPSERT, *params)
        except DRIVER_ERRORS as exc:
            raise translate(exc, "saving a research session") from exc

    async def get(self, session_id: uuid.UUID) -> ResearchSession | None:
        """Return the stored session with this id, or ``None``.

        Raises:
            StorageError: The lookup failed, or the stored document does not
                satisfy the model.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(_SELECT_ONE, session_id)
            if row is None:
                return None
            return ResearchSession.model_validate_json(row["payload"])
        except READ_ERRORS as exc:
            raise translate(exc, "loading a research session") from exc

    async def list_recent(self, *, limit: int = 20) -> Sequence[ResearchSession]:
        """Return up to ``limit`` most recently created sessions, newest first.

        Args:
            limit: Maximum number of sessions to return.

        Returns:
            The sessions, newest first.

        Raises:
            ValueError: ``limit`` is less than one.
            StorageError: The lookup failed, or a stored document does not
                satisfy the model.

        Note:
            ``limit`` is checked here rather than passed through, because
            PostgreSQL reads a negative ``LIMIT`` as *unlimited*. Without this
            guard, ``list_recent(limit=-1)`` — an off-by-one with an otherwise
            harmless-looking call — would return the entire table instead of
            nothing.
        """
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(_SELECT_RECENT, limit)
            return tuple(ResearchSession.model_validate_json(row["payload"]) for row in rows)
        except READ_ERRORS as exc:
            raise translate(exc, "listing research sessions") from exc
