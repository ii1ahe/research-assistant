"""Composing the application, and taking it apart again.

This is the one module allowed to know the whole object graph (ADR-003). It is
the only place that reads settings, configures logging, opens a connection or
constructs a provider, which is what lets every other module be exercised by
handing it something else instead.

The lifecycle is why this is a context manager rather than a handful of lines in
``cli.py``. One run opens an HTTP connection pool and possibly a database pool,
and both must be released on every path out — including the paths that raise and
the ones a user interrupts. :class:`Application` owns all of it behind one
``aclose``, so the CLI holds a single thing in an ``async with`` and has no
opportunity to leak a pool on an error path.

A note on the ordering inside :func:`bootstrap`: the HTTP client is built before
the database is dialled, so if the database is unreachable the client that was
already created is closed rather than leaked. Nothing is left half-open, which
is the same property :meth:`PostgresStorage.connect` guarantees for its own pool.
Setup is arranged in the order it can fail — client, then database, then the
providers that validate their own credentials — so every resource is open before
whatever might reject the configuration, and every failure has something to
release.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from types import TracebackType

import httpx

from researcher.config import Settings, get_settings
from researcher.core.researcher import ResearchService
from researcher.errors import ConfigurationError, StorageError
from researcher.services.ai_service import AIService
from researcher.services.cache import CacheService
from researcher.services.http_client import build_client
from researcher.services.orchestrator import Orchestrator
from researcher.storage.interfaces import Storage
from researcher.storage.postgres import PostgresStorage

__all__ = ["Application", "bootstrap", "configure_logging", "open_storage"]

logger = logging.getLogger(__name__)

#: Everything configured here is namespaced to our own code. The supplied ``ai``
#: package logs through the root logger, and inheriting its chatter at whatever
#: verbosity it chose would bury the application's own output.
_LOGGER_NAMESPACE = "researcher"


def configure_logging(settings: Settings) -> None:
    """Send the application's own logs to stderr at the configured level.

    Scoped to the ``researcher`` namespace rather than installed on the root
    logger, so enabling DEBUG for this application does not also enable it for
    ``httpx``, ``asyncpg`` and the provider SDKs.

    Stderr, always. stdout carries the answer and nothing else, so that
    ``researcher ask "…" > answer.md`` produces a file worth keeping.

    Idempotent: a second call replaces the handler the first installed rather
    than adding to it, which is what a process that bootstraps more than once —
    a test, or a future ``demo`` re-entry — needs. ``propagate`` is disabled for
    the same reason, so a log record is emitted exactly once.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    namespace = logging.getLogger(_LOGGER_NAMESPACE)
    for existing in list(namespace.handlers):
        namespace.removeHandler(existing)
        existing.close()
    namespace.addHandler(handler)
    namespace.setLevel(settings.log_level)
    namespace.propagate = False


async def open_storage(settings: Settings) -> Storage | None:
    """Open the database, or return ``None`` when none is configured.

    Args:
        settings: Supplies ``database_url``.

    Returns:
        A ready :class:`~researcher.storage.interfaces.Storage`, or ``None`` if
        no DSN is set — in which case nothing is cached and sessions are not
        stored, which the README documents as a supported way to run.

    Raises:
        ConfigurationError: A DSN is set and the database cannot be used.

    The failure is deliberately fatal rather than a quiet downgrade to running
    without persistence. A user who set ``DATABASE_URL`` asked for their sessions
    to be recorded, and finding out days later — from an empty history — that
    they never were is worse than being told at startup, before any work has been
    paid for. Running without a database remains fully supported; it is spelled
    by leaving the variable unset.
    """
    if settings.database_url is None:
        logger.debug("no database configured; running without persistence")
        return None

    try:
        storage = await PostgresStorage.connect(settings.database_url)
    except StorageError as exc:
        raise ConfigurationError(
            f"the configured database is not usable ({exc.message}). "
            "Unset DATABASE_URL to run without persistence.",
            source="storage",
        ) from exc

    logger.debug("connected to the database and applied migrations")
    return storage


async def _quietly(release: Callable[[], Awaitable[None]], what: str) -> None:
    """Release a resource, logging rather than raising a failure to release it."""
    try:
        await release()
    except Exception:
        logger.warning("releasing %s failed", what, exc_info=True)


class Application:
    """Everything one run owns, released through a single ``aclose``.

    Holds the constructed service alongside the resources behind it, because
    whoever built the resource is who must close it: ``AIService`` deliberately
    leaves an injected client open, so the ownership lands here.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        service: ResearchService,
        storage: Storage | None,
        client: httpx.AsyncClient,
        ai: AIService,
    ) -> None:
        """Initialize the application. Construct via :func:`bootstrap`."""
        self._settings = settings
        self._service = service
        self._storage = storage
        self._client = client
        self._ai = ai
        self._closed = False

    @property
    def settings(self) -> Settings:
        """The settings this application was built from."""
        return self._settings

    @property
    def service(self) -> ResearchService:
        """The application service the CLI drives."""
        return self._service

    @property
    def storage(self) -> Storage | None:
        """The open storage, or ``None`` when running without a database."""
        return self._storage

    async def aclose(self) -> None:
        """Release the HTTP pool and the database, once, in that order.

        Idempotent, and it never raises: it is called from ``finally`` blocks,
        where an exception would replace the failure that got us there. Each
        resource is released independently, so a failure closing one still
        releases the other — the connection pool matters more than the report of
        why it could not be closed.
        """
        if self._closed:
            return
        self._closed = True

        # A no-op while the client below is still open, and kept for the case
        # where the service built its own. Calling it first means the service
        # has stopped referring to a client before that client is closed.
        await self._ai.aclose()
        await _quietly(self._client.aclose, "the HTTP client")
        if self._storage is not None:
            await _quietly(self._storage.aclose, "the database pool")

    async def __aenter__(self) -> Application:
        """Return this application."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release everything, whatever happened inside the block."""
        await self.aclose()


@contextlib.asynccontextmanager
async def bootstrap(settings: Settings | None = None) -> AsyncIterator[Application]:
    """Build the application graph and tear it down on the way out.

    Args:
        settings: Settings to build from. Loaded from the environment and
            ``.env`` when omitted, which is what the CLI does.

    Yields:
        A ready :class:`Application`.

    Raises:
        ConfigurationError: The environment is invalid, or a configured database
            cannot be used.

    A context manager rather than a factory function because of the paths that
    never reach a ``return``. The one that matters is a user pressing Ctrl-C:
    the interrupt arrives as a ``CancelledError`` raised inside the ``async
    with``, and the pool is still closed on the way out.
    """
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved)

    client = build_client(resolved)
    try:
        storage = await open_storage(resolved)
    except BaseException:
        # Covers ConfigurationError and cancellation alike: the client exists
        # already, and a failure to reach the database must not leak it.
        await _quietly(client.aclose, "the HTTP client")
        raise

    try:
        # One client, shared by every fetcher: the topic contract is explicit
        # that connection reuse is where this application's throughput comes
        # from.
        ai = AIService(resolved, client=client)
        cache = CacheService(storage.cache if storage is not None else None, resolved)
        orchestrator = Orchestrator(ai, cache, resolved)
        service = ResearchService(
            ai=ai, orchestrator=orchestrator, settings=resolved, storage=storage
        )
    except BaseException:
        # A credential that is absent or refused is discovered by ``AIService``
        # here — at startup, before a single request is paid for — and the pools
        # already open must not leak on the way out.
        if storage is not None:
            await _quietly(storage.aclose, "the database pool")
        await _quietly(client.aclose, "the HTTP client")
        raise

    application = Application(
        settings=resolved, service=service, storage=storage, client=client, ai=ai
    )
    try:
        yield application
    finally:
        # A cancellation arriving here — a user pressing Ctrl-C mid-run — would
        # otherwise be raised again by the first ``await`` inside ``aclose``,
        # abandoning the pool it was about to close. ``aclose`` logs its own
        # failures rather than raising them, so anything suppressed here is a
        # cancellation and not a defect being hidden.
        with contextlib.suppress(BaseException):
            await application.aclose()
