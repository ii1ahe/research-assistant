"""PostgreSQL storage: connection pool, migrations and lifecycle.

This is the only module in the application that imports a database driver. The
repositories in :mod:`researcher.storage.cache_store` and
:mod:`researcher.storage.session_repository` receive the pool from here, and
everything above :class:`PostgresStorage` sees only the protocols in
:mod:`researcher.storage.interfaces`.

Two responsibilities live here rather than in the repositories:

**Migrations.** They are a property of the database, not of either repository,
and applying them must happen exactly once before either repository is used.
Running them from ``connect`` means a caller cannot obtain a usable storage
object without the schema being current.

**Error translation.** Every driver exception is converted to
:class:`~researcher.errors.StorageError` at this boundary. The original is
attached as ``__cause__`` because suppressing it would make a production
failure undiagnosable, but it is never interpolated into the message: a DSN
may carry a password, and a driver message may quote a row.
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

#: Repository root, derived from this file's location rather than the current
#: working directory, so migrations are found regardless of where the CLI is
#: invoked from.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Default migration directory. A top-level ``migrations/`` is what the README
#: advertises and what a reviewer will look for.
_MIGRATIONS_DIRNAME = "migrations"

#: Bookkeeping for applied migrations. The checksum is what makes migrations
#: append-only: re-running an edited file is refused rather than skipped.
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

    Ordering is lexical, which is why migrations are numbered. A zero-padded
    or otherwise consistently sorted prefix is required; ``10_x.sql`` sorting
    before ``2_x.sql`` is the caller's problem to avoid, not something this
    function silently corrects.

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

    Each migration runs inside its own transaction together with the row that
    records it, so a failure leaves neither a half-applied schema nor a
    bookkeeping row claiming otherwise.

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
    """The application's :class:`~researcher.storage.interfaces.Storage` implementation.

    Construct through :meth:`connect` rather than directly: it opens the pool
    and brings the schema up to date, so an instance that exists is an instance
    that is ready to use.
    """

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
        migrate: bool = True,
        migrations_path: Path | None = None,
    ) -> PostgresStorage:
        """Open a pool against ``dsn`` and bring the schema up to date.

        Args:
            dsn: PostgreSQL connection string.
            min_size: Connections opened eagerly. At least one so that an
                unreachable database fails here rather than on first use.
            max_size: Hard ceiling on concurrent connections.
            timeout: Seconds a connection attempt may take before the pool
                open fails. The driver's default is 60 s, which turns an
                unreachable database into a minute of silence per attempt —
                the clean-clone reproduction
                (``artefacts/reproduction-codespaces-bfec78.txt``) measured
                seven skipped tests at sixty seconds each. Fail fast instead,
                so bootstrap reports the unusable database rather than
                hanging.
            migrate: Set false only when the caller manages migrations itself.
            migrations_path: Override for the migration directory.

        Returns:
            A ready :class:`PostgresStorage`.

        Raises:
            StorageError: The pool could not be opened, or a migration failed.
                The pool is closed before this is raised, so a failed connect
                does not leak connections.
        """
        try:
            pool = await asyncpg.create_pool(
                dsn, min_size=min_size, max_size=max_size, timeout=timeout
            )
        except DRIVER_ERRORS as exc:
            raise StorageError("could not connect to the database", source="storage") from exc

        if pool is None:  # pragma: no cover - defensive; asyncpg does not do this
            raise StorageError("could not connect to the database", source="storage")

        if migrate:
            try:
                await run_migrations(pool, directory=migrations_path)
            except BaseException:
                # Covers StorageError and cancellation alike: a pool that was
                # opened must not outlive a failed setup.
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
        """Close the pool.

        Safe to call more than once, and deliberately does **not** raise. It is
        called from cleanup blocks, where an exception would replace whatever
        failure was already propagating and hide the real cause. A close that
        fails is logged instead.
        """
        if self._closed:
            return
        self._closed = True
        try:
            await self._pool.close()
        except DRIVER_ERRORS:
            logger.warning("closing the database pool failed", exc_info=True)
