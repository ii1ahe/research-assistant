"""Composition, and the guarantee that nothing is left open.

``bootstrap`` is the only module that touches the outside world, so these tests
are about two things: that the graph it builds is the graph the rest of the
application expects, and that every resource it opens is released on every path
out — the path that returns, the path that raises, and the path where setup
itself fails halfway.

Nothing here reaches the network or a database. The database is simulated by
replacing ``PostgresStorage.connect``, which is the single call that would open
a socket.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

import researcher.bootstrap as bootstrap_module
from researcher.bootstrap import Application, bootstrap, open_storage
from researcher.config import Settings
from researcher.core.researcher import ResearchService
from researcher.errors import ConfigurationError, StorageError
from researcher.storage.memory import InMemorySessionRepository, InMemorySourceCache
from researcher.storage.postgres import PostgresStorage

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("restore_researcher_logger"),
]

SettingsFactory = Callable[..., Settings]

_DSN = "postgresql://researcher:researcher@localhost:5432/researcher"


class StubbornClient:
    """An HTTP client that reports a failure to close, after marking itself shut."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        """Mark closed, then fail — as a socket refusing to close would."""
        self.closed = True
        raise RuntimeError("the socket would not close")


class RecordingStorage:
    """A storage double that records whether it was released."""

    def __init__(self) -> None:
        self.closed = False

    @property
    def cache(self) -> InMemorySourceCache:
        """A cache, for shape."""
        return InMemorySourceCache()

    @property
    def sessions(self) -> InMemorySessionRepository:
        """A session store, for shape."""
        return InMemorySessionRepository()

    async def aclose(self) -> None:
        """Record that the pool was released."""
        self.closed = True


class SilentAI:
    """Stands in for ``AIService``; only ``aclose`` matters here."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        """Record that the service released its client."""
        self.closed = True


def _unreachable(cls: type, dsn: str, **kwargs: Any) -> Any:
    """Stand in for ``PostgresStorage.connect`` when the database is down."""
    raise StorageError("could not connect to the database", source="storage")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def test_no_dsn_means_no_database_and_no_error(make_settings: SettingsFactory) -> None:
    """Running without persistence is supported; it is not a degraded mode."""
    assert await open_storage(make_settings(database_url=None)) is None


async def test_a_configured_database_is_opened(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = object()
    seen: list[str] = []

    async def connect(cls: type, dsn: str, **kwargs: Any) -> object:
        seen.append(dsn)
        return sentinel

    monkeypatch.setattr(PostgresStorage, "connect", classmethod(connect))

    assert await open_storage(make_settings(database_url=_DSN)) is sentinel
    assert seen == [_DSN]


async def test_an_unreachable_database_names_the_variable_that_would_avoid_it(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit status 2, and a message that says what to do about it.

    Quietly downgrading to running without persistence would mean the user
    discovered their sessions were never recorded days later, from an empty
    history, with nothing to point at.
    """
    monkeypatch.setattr(PostgresStorage, "connect", classmethod(_unreachable))

    with pytest.raises(ConfigurationError) as caught:
        await open_storage(make_settings(database_url=_DSN))

    assert "Unset DATABASE_URL" in str(caught.value)
    assert _DSN not in str(caught.value)


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


async def test_bootstrap_yields_a_service_and_no_storage_without_a_database(
    make_settings: SettingsFactory,
) -> None:
    async with bootstrap(make_settings(database_url=None)) as application:
        assert isinstance(application.service, ResearchService)
        assert application.storage is None


async def test_bootstrap_uses_the_settings_it_was_given(make_settings: SettingsFactory) -> None:
    """Explicit settings must win, or every test would read the developer's .env.

    ``storage is None`` is the observable proof. The fixture sets
    ``database_url=None`` while the developer's own ``.env`` very likely sets a
    DSN, so a ``bootstrap`` that consulted the environment instead would have
    opened a connection here.
    """
    settings = make_settings(database_url=None, llm_model="a-model-only-this-test-knows")

    async with bootstrap(settings) as application:
        assert application.settings is settings
        assert application.settings.llm_model == "a-model-only-this-test-knows"
        assert application.storage is None


async def test_a_missing_credential_fails_before_any_work_and_still_cleans_up(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every run ends in synthesis, so a missing key is fatal at startup.

    Checked before the first fetch rather than at first use, because otherwise
    retrieval would spend the user's time and the providers' quota on an answer
    that could never be produced. The client already built must still close.
    """
    client = httpx.AsyncClient()
    monkeypatch.setattr(bootstrap_module, "build_client", lambda settings: client)

    with pytest.raises(ConfigurationError):
        async with bootstrap(make_settings(database_url=None, google_api_key=None)):
            pass

    assert client.is_closed


async def test_a_credential_failure_after_the_database_opened_releases_both(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one ordering where two resources are open when setup is rejected.

    A credential problem is found after the database is dialled, so this is the
    only path on which the pool *and* the client can both be live with nothing
    to hand them to. Both must close, which is why the assembly step has its own
    ``except`` rather than sharing the one around ``open_storage``.
    """
    client = httpx.AsyncClient()
    storage = RecordingStorage()
    monkeypatch.setattr(bootstrap_module, "build_client", lambda settings: client)

    async def connect(cls: type, dsn: str, **kwargs: Any) -> Any:
        return storage

    monkeypatch.setattr(PostgresStorage, "connect", classmethod(connect))

    with pytest.raises(ConfigurationError):
        async with bootstrap(make_settings(database_url=_DSN, google_api_key=None)):
            pass

    assert storage.closed
    assert client.is_closed


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_the_http_client_is_open_inside_and_closed_afterwards(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = httpx.AsyncClient()
    monkeypatch.setattr(bootstrap_module, "build_client", lambda settings: client)

    async with bootstrap(make_settings(database_url=None)):
        assert not client.is_closed

    assert client.is_closed


async def test_a_client_failure_does_not_abandon_the_database(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each resource is released independently, so one failure is not two leaks."""
    client = StubbornClient()
    storage = RecordingStorage()
    monkeypatch.setattr(bootstrap_module, "build_client", lambda settings: client)

    async def connect(cls: type, dsn: str, **kwargs: Any) -> Any:
        return storage

    monkeypatch.setattr(PostgresStorage, "connect", classmethod(connect))

    async with bootstrap(make_settings(database_url=_DSN)):
        pass

    assert client.closed
    assert storage.closed


async def test_a_failed_database_connection_does_not_leak_the_client(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client is built first, so setup failing halfway must still close it."""
    client = httpx.AsyncClient()
    monkeypatch.setattr(bootstrap_module, "build_client", lambda settings: client)
    monkeypatch.setattr(PostgresStorage, "connect", classmethod(_unreachable))

    with pytest.raises(ConfigurationError):
        async with bootstrap(make_settings(database_url=_DSN)):
            pass

    assert client.is_closed


async def test_an_error_inside_the_block_still_releases_everything(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raise must reach the caller with the pools already closed."""
    client = httpx.AsyncClient()
    monkeypatch.setattr(bootstrap_module, "build_client", lambda settings: client)

    with pytest.raises(RuntimeError, match="the run blew up"):
        async with bootstrap(make_settings(database_url=None)):
            raise RuntimeError("the run blew up")

    assert client.is_closed


async def test_closing_an_application_more_than_once_is_harmless(
    make_settings: SettingsFactory,
) -> None:
    """``aclose`` runs from a ``finally``, which may itself be unwound twice."""
    async with bootstrap(make_settings(database_url=None)) as application:
        await application.aclose()
        await application.aclose()


async def test_the_service_releases_its_client_before_the_client_is_closed() -> None:
    """Order matters: the service must not hold a reference to a closed client."""
    order: list[str] = []

    class OrderedAI(SilentAI):
        async def aclose(self) -> None:
            order.append("service")

    class OrderedClient(StubbornClient):
        async def aclose(self) -> None:
            order.append("client")

    application = Application(
        settings=object(),  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        storage=None,
        client=OrderedClient(),  # type: ignore[arg-type]
        ai=OrderedAI(),  # type: ignore[arg-type]
    )

    await application.aclose()

    assert order == ["service", "client"]


async def test_an_application_that_was_never_used_can_still_be_closed(
    make_settings: SettingsFactory,
) -> None:
    """``bootstrap`` may fail between constructing this and yielding it."""
    application = Application(
        settings=make_settings(),
        service=object(),  # type: ignore[arg-type]
        storage=None,
        client=httpx.AsyncClient(),
        ai=SilentAI(),  # type: ignore[arg-type]
    )

    async with application:
        pass

    assert await application.aclose() is None
