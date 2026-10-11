"""Opens the PostgreSQL pool, applies SQL migrations, checks migration hashes, and closes the
pool.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import asyncpg

from researcher.errors import StorageError
from researcher.storage._driver import DRIVER_ERRORS
from researcher.storage.cache_store import PostgresSourceCache
from researcher.storage.interfaces import SessionRepository, SourceCache
from researcher.storage.session_repository import PostgresSessionRepository

__all__ = [
    "PostgresStorage",
    "discover_migrations",
    "migrations_dir",
    "run_migrations",
]

logger = logging.getLogger(__name__)

# Find migrations from the repo path rather than the working directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# SQL migrations live in the top-level migrations folder.
_MIGRATIONS_DIRNAME = "migrations"

# Bound each SQL statement because cache reads and writes run outside fetch deadlines.
# bootstrap passes the active source timeout; this default is for direct callers.
_DEFAULT_COMMAND_TIMEOUT = 10.0

# Store migration hashes so an edited, already-applied file is rejected.
_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text        PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def migrations_dir() -> Path:
    """Return the default migration directory."""
    return _REPO_ROOT / _MIGRATIONS_DIRNAME


def discover_migrations(directory: Path | None = None) -> list[Path]:
    """Return the ``.sql`` migrations in ``directory``, ordered by filename.

    Raises:
        StorageError: The directory is missing or holds no migrations. Both
            mean the deployment is incomplete, and continuing would leave the
            application running against a schema nobody created.
    """
    target = directory if directory is not None else migrations_dir()
    if not target.is_dir():
        raise StorageError(
            f"migration directory not found: {target.name}",
            source="storage",
        )
    found = sorted(target.glob("*.sql"))
    if not found:
        raise StorageError(
            f"no migrations found in {target.name}",
            source="storage",
        )
    return found


async def run_migrations(
    pool: asyncpg.Pool,
    *,
    directory: Path | None = None,
) -> tuple[str, ...]:
    """Apply every migration that has not been applied yet.

    Args:
        pool: An open pool.
        directory: Override for the migration directory.

    Returns:
        The filenames applied by this call, in order. Empty when the schema is
        already current, which is the normal case after the first start.

    Raises:
        StorageError: The directory is unusable, a migration failed, or an
            already-applied migration's contents have changed.
    """
    files = discover_migrations(directory)

    try:
        async with pool.acquire() as conn:
            await conn.execute(_MIGRATIONS_DDL)
            rows = await conn.fetch("SELECT filename, checksum FROM schema_migrations")
            applied_before = {row["filename"]: row["checksum"] for row in rows}

            applied_now: list[str] = []
            for path in files:
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

                recorded = applied_before.get(path.name)
                if recorded is not None:
                    if recorded != checksum:
                        raise StorageError(
                            f"migration {path.name} has changed since it was applied; "
                            "migrations are append-only, so add a new file instead",
                            source="storage",
                        )
                    continue

                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)",
                        path.name,
                        checksum,
                    )
                applied_now.append(path.name)
                logger.info("applied migration %s", path.name)
    except StorageError:
        raise
    except DRIVER_ERRORS as exc:
        raise StorageError("could not apply migrations", source="storage") from exc

    return tuple(applied_now)


class PostgresStorage:
    """The application's :class:`~researcher.storage.interfaces.Storage` implementation."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Wrap an already-open pool.

        Args:
            pool: The pool to use. Ownership transfers to this object, which
                closes it in :meth:`aclose`.
        """
        self._pool = pool
        self._cache = PostgresSourceCache(pool)
        self._sessions = PostgresSessionRepository(pool)
        self._closed = False

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        timeout: float = 10.0,
        command_timeout: float = _DEFAULT_COMMAND_TIMEOUT,
        migrate: bool = True,
        migrations_path: Path | None = None,
    ) -> PostgresStorage:
        """Open a database pool and apply pending migrations.

        Args:
            dsn: PostgreSQL connection string.
            min_size: Connections opened at startup; at least one checks the database now.
            max_size: Maximum number of connections.
            timeout: Connection timeout in seconds.
            command_timeout: Timeout for each SQL statement, separate from connecting.
            migrate: Whether to apply migrations when opening the pool.
            migrations_path: Optional migration folder override.

        Returns:
            Storage ready for cache and session queries.

        Raises:
            StorageError: Connection or migration setup fails. The pool is closed first.
        """
        try:
            pool = await asyncpg.create_pool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                timeout=timeout,
                command_timeout=command_timeout,
            )
        except DRIVER_ERRORS as exc:
            raise StorageError("could not connect to the database", source="storage") from exc

        if pool is None:  # pragma: no cover - defensive; asyncpg does not do this
            raise StorageError("could not connect to the database", source="storage")

        if migrate:
            try:
                await run_migrations(pool, directory=migrations_path)
            except BaseException:
                # Close the pool if migration setup fails or is cancelled.
                await pool.close()
                raise

        return cls(pool)

    @property
    def cache(self) -> SourceCache:
        """Retrieval-result cache."""
        return self._cache

    @property
    def sessions(self) -> SessionRepository:
        """Completed-session store."""
        return self._sessions

    async def aclose(self) -> None:
        """Close the pool."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._pool.close()
        except DRIVER_ERRORS:
            logger.warning("closing the database pool failed", exc_info=True)
