"""Builds the application services and shared connections, sets up logging, and closes resources
when a run ends.
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

# Keep app logging separate from the supplied ai package logger.
_LOGGER_NAMESPACE = "researcher"


def configure_logging(settings: Settings) -> None:
    """Send the application's own logs to stderr at the configured level."""
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
    """
    if settings.database_url is None:
        logger.debug("no database configured; running without persistence")
        return None

    try:
        # Cache queries run outside the fetch deadline, so give them the same timeout.
        storage = await PostgresStorage.connect(
            settings.database_url,
            command_timeout=settings.per_source_timeout_seconds,
        )
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
    """Everything one run owns, released through a single ``aclose``."""

    def __init__(
        self,
        *,
        settings: Settings,
        service: ResearchService,
        storage: Storage | None,
        client: httpx.AsyncClient,
        ai: AIService,
        cache: CacheService,
    ) -> None:
        """Initialize the application. Construct via :func:`bootstrap`."""
        self._settings = settings
        self._service = service
        self._storage = storage
        self._client = client
        self._ai = ai
        self._cache = cache
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

    @property
    def cache(self) -> CacheService:
        """The cache service the housekeeping commands drive."""
        return self._cache

    async def aclose(self) -> None:
        """Release the HTTP pool and the database, once, in that order."""
        if self._closed:
            return
        self._closed = True

        # Release the service before closing the client it uses.
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
    """
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved)

    client = build_client(resolved)
    try:
        storage = await open_storage(resolved)
    except BaseException:
        # Close the HTTP client if database setup fails or is cancelled.
        await _quietly(client.aclose, "the HTTP client")
        raise

    try:
        # All fetchers share one HTTP connection pool.
        ai = AIService(resolved, client=client)
        cache = CacheService(storage.cache if storage is not None else None, resolved)
        orchestrator = Orchestrator(ai, cache, resolved)
        service = ResearchService(
            ai=ai, orchestrator=orchestrator, settings=resolved, storage=storage
        )
    except BaseException:
        # A provider setup failure must also close the pools already opened.
        if storage is not None:
            await _quietly(storage.aclose, "the database pool")
        await _quietly(client.aclose, "the HTTP client")
        raise

    application = Application(
        settings=resolved, service=service, storage=storage, client=client, ai=ai, cache=cache
    )
    try:
        yield application
    finally:
        # Do not let cancellation interrupt cleanup. aclose logs cleanup failures.
        with contextlib.suppress(BaseException):
            await application.aclose()
