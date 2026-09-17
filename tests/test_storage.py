"""Storage tests: a shared behaviour contract, run against two implementations.

ADR-004 requires real PostgreSQL persistence *and* a suite that passes offline.
The way both hold at once is that the important assertions are written once, as
coroutines taking *any* storage, and then run twice — against the in-memory
implementation (always) and against PostgreSQL (when a database is reachable,
skipped with a reason when it is not).

That structure is the point. If the two implementations ever disagree, the same
assertion fails for one and passes for the other, which is a much clearer signal
than two hand-written suites drifting apart.

The PostgreSQL tests skip rather than fail when no database is configured, so
``pytest`` stays green with the network down. Run them with ``DATABASE_URL`` set
— either in the environment or via ``.env`` — to prove the SQL.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from ai.schemas import Source
from researcher.errors import StorageError
from researcher.models import (
    CacheEntry,
    CacheKey,
    ProviderIdentity,
    ResearchRequest,
    ResearchSession,
    ResultStatus,
    RetrievalResult,
    SourceName,
    SourceOutcome,
    SourceStatus,
    TimingInfo,
)
from researcher.storage.interfaces import Storage
from researcher.storage.memory import InMemoryStorage
from researcher.storage.postgres import (
    PostgresStorage,
    discover_migrations,
    run_migrations,
)

pytestmark = pytest.mark.asyncio

#: Fixed instant so expiry boundaries are exact rather than flaky. Tests pass
#: this explicitly to every call that takes ``now``.
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

#: Connection used only to prove translation works with nothing listening.
UNREACHABLE_DSN = "postgresql://nobody@127.0.0.1:1/nothing"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _source(n: int) -> Source:
    return Source(
        title=f"Title {n}",
        url=f"https://example.com/{n}",
        snippet=f"snippet-{n}",
        origin="wikipedia",
    )


def _key(tag: str, *, max_results: int = 3) -> CacheKey:
    return CacheKey(
        source=SourceName.WIKIPEDIA,
        query=f"{tag}-query",
        provider="tavily",
        max_results=max_results,
    )


def _entry(tag: str, *, ttl_seconds: int = 3600, max_results: int = 3) -> CacheEntry:
    return CacheEntry(
        key=_key(tag, max_results=max_results),
        sources=(_source(1), _source(2)),
        fetched_at=NOW,
        expires_at=NOW + timedelta(seconds=ttl_seconds),
    )


def _session(tag: str, created_at: datetime) -> ResearchSession:
    return ResearchSession(
        id=uuid.uuid4(),
        created_at=created_at,
        request=ResearchRequest(question=f"{tag} question", sources=(SourceName.WIKIPEDIA,)),
        provider=ProviderIdentity(
            llm_provider="gemini",
            llm_model="gemini-3.8-flash",
            web_search_provider="tavily",
        ),
        retrieval=RetrievalResult(
            outcomes=(
                SourceOutcome(
                    source=SourceName.WIKIPEDIA,
                    status=SourceStatus.SUCCESS,
                    sources=(_source(1),),
                ),
            ),
            sources=(_source(1),),
        ),
        status=ResultStatus.NO_SOURCES,
        timing=TimingInfo(started_at=created_at, finished_at=created_at),
    )


# ---------------------------------------------------------------------------
# The shared contract
# ---------------------------------------------------------------------------


async def assert_cache_contract(storage: Storage, tag: str) -> None:
    """Assert cache behaviour every implementation must share."""
    cache = storage.cache

    await cache.put(_entry(tag))
    got = await cache.get(_key(tag), now=NOW)
    assert got is not None
    assert got.fetched_at == NOW
    # Snippets are the payload. A cache that stored only titles and urls would
    # still satisfy a round-trip check on identity, so this asserts content.
    assert [s.snippet for s in got.sources] == ["snippet-1", "snippet-2"]

    assert await cache.get(_key(f"{tag}-absent"), now=NOW) is None

    # A second write under the same key replaces rather than conflicts.
    replacement = _entry(tag, ttl_seconds=9999).model_copy(update={"sources": (_source(7),)})
    await cache.put(replacement)
    got = await cache.get(_key(tag), now=NOW)
    assert got is not None
    assert got.sources[0].title == "Title 7"
    assert got.expires_at == NOW + timedelta(seconds=9999)

    # Freshness is strict: an entry is stale at its expiry instant, not one
    # microsecond later. Both implementations must agree on that edge.
    await cache.put(_entry(f"{tag}-edge", ttl_seconds=100))
    edge = _key(f"{tag}-edge")
    assert await cache.get(edge, now=NOW + timedelta(seconds=99)) is not None
    assert await cache.get(edge, now=NOW + timedelta(seconds=100)) is None

    # The key is scoped by result limit: the same query at a different limit
    # must not be served the other's payload.
    await cache.put(_entry(f"{tag}-scope"))
    assert await cache.get(_key(f"{tag}-scope", max_results=5), now=NOW) is None

    # Purge removes what has expired and leaves what has not.
    await cache.put(_entry(f"{tag}-purge", ttl_seconds=10))
    await cache.put(_entry(f"{tag}-keep", ttl_seconds=10_000))
    assert await cache.purge_expired(now=NOW + timedelta(seconds=1000)) >= 1
    assert await cache.get(_key(f"{tag}-keep"), now=NOW + timedelta(seconds=1000))
    assert await cache.get(_key(f"{tag}-purge"), now=NOW + timedelta(seconds=1000)) is None


async def assert_session_contract(storage: Storage, tag: str) -> None:
    """Assert session-store behaviour every implementation must share."""
    sessions = storage.sessions

    first = _session(f"{tag} one", NOW - timedelta(minutes=2))
    second = _session(f"{tag} two", NOW - timedelta(minutes=1))
    third = _session(f"{tag} three", NOW)
    for session in (first, second, third):
        await sessions.save(session)

    restored = await sessions.get(first.id)
    assert restored is not None
    assert restored.request.question == f"{tag} one question"
    # The archival record keeps snippets; the presentation format does not.
    assert restored.retrieval.sources[0].snippet == "snippet-1"
    assert restored.provider.llm_model == "gemini-3.8-flash"

    assert await sessions.get(uuid.uuid4()) is None

    # Saving the same id twice replaces rather than duplicating.
    await sessions.save(first)
    assert (
        len(
            [
                s
                for s in await sessions.list_recent(limit=50)
                if s.request.question == f"{tag} one question"
            ]
        )
        == 1
    )

    recent = [s for s in await sessions.list_recent(limit=50) if s.request.question.startswith(tag)]
    assert [s.request.question for s in recent] == [
        f"{tag} three question",
        f"{tag} two question",
        f"{tag} one question",
    ]

    assert len(await sessions.list_recent(limit=1)) == 1


@pytest.mark.parametrize("limit", [0, -1, -100])
async def test_list_recent_rejects_non_positive_limit(limit: int) -> None:
    """A limit below one raises rather than silently changing meaning.

    PostgreSQL reads a negative ``LIMIT`` as *unlimited*, so without the guard
    ``limit=-1`` would return every session ever stored.
    """
    storage = InMemoryStorage()
    with pytest.raises(ValueError, match="limit must be at least 1"):
        await storage.sessions.list_recent(limit=limit)


# ---------------------------------------------------------------------------
# In-memory implementation — always runs, no database required
# ---------------------------------------------------------------------------


async def test_in_memory_cache_contract() -> None:
    await assert_cache_contract(InMemoryStorage(), f"mem-cache-{uuid.uuid4().hex[:8]}")


async def test_in_memory_session_contract() -> None:
    await assert_session_contract(InMemoryStorage(), f"mem-sessions-{uuid.uuid4().hex[:8]}")


async def test_in_memory_aclose_is_idempotent() -> None:
    """Cleanup blocks call aclose in a `finally`, so it must tolerate repeats."""
    storage = InMemoryStorage()
    await storage.aclose()
    await storage.aclose()


async def test_connect_to_unreachable_database_raises_storage_error() -> None:
    """Driver failures surface as StorageError, never as an asyncpg type.

    This runs offline: nothing is listening on the port, so the failure is a
    refused connection, and it still has to arrive translated.
    """
    with pytest.raises(StorageError) as caught:
        await PostgresStorage.connect(UNREACHABLE_DSN)

    assert caught.value.code == "storage_error"
    # The DSN must not be echoed into the message shown to the user.
    assert "127.0.0.1" not in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError | TimeoutError)


# ---------------------------------------------------------------------------
# PostgreSQL implementation — skips when no database is reachable
# ---------------------------------------------------------------------------


def _resolve_dsn() -> str | None:
    """Return the DSN to test against, or ``None`` if none is configured."""
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return dsn
    try:
        from researcher.config import get_settings

        return get_settings().database_url
    except Exception:
        return None


@pytest_asyncio.fixture
async def storage():
    """Yield a connected PostgresStorage, skipping when none is available."""
    dsn = _resolve_dsn()
    if not dsn:
        pytest.skip("no DATABASE_URL configured; set it to run the SQL tests")
    try:
        connected = await PostgresStorage.connect(dsn)
    except StorageError as exc:
        pytest.skip(f"PostgreSQL not reachable ({exc})")

    try:
        yield connected
    finally:
        await connected.aclose()


@pytest_asyncio.fixture
async def cleanup(storage: Storage):
    """Delete rows written by one test, identified by a unique tag."""
    tag = f"test-{uuid.uuid4().hex[:12]}"
    yield tag

    pool = storage._pool  # type: ignore[attr-defined]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM source_cache WHERE query LIKE $1", tag + "%")
        await conn.execute("DELETE FROM research_sessions WHERE question LIKE $1", tag + "%")


async def test_connect_bounds_the_pool_open_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable database must fail fast, not wait out the driver's 60 s default.

    The clean-clone reproduction (``artefacts/reproduction-codespaces-bfec78.txt``)
    measured the old behaviour: seven skipped tests at sixty seconds each, because
    ``create_pool`` was called without a timeout and asyncpg waits a minute by default.
    """
    opened: list[dict[str, object]] = []

    async def fake_create_pool(dsn: str, **kwargs: object) -> object:
        opened.append(kwargs)
        return object()  # migrate=False keeps connect() from touching the pool

    monkeypatch.setattr("researcher.storage.postgres.asyncpg.create_pool", fake_create_pool)

    await PostgresStorage.connect(
        "postgresql://researcher@localhost:5432/researcher", migrate=False
    )
    await PostgresStorage.connect(
        "postgresql://researcher@localhost:5432/researcher", migrate=False, timeout=2.5
    )

    assert opened == [
        {"min_size": 1, "max_size": 5, "timeout": 10.0},
        {"min_size": 1, "max_size": 5, "timeout": 2.5},
    ]


async def test_postgres_cache_contract(storage: Storage, cleanup: str) -> None:
    await assert_cache_contract(storage, cleanup)


async def test_postgres_session_contract(storage: Storage, cleanup: str) -> None:
    await assert_session_contract(storage, cleanup)


async def test_postgres_schema_is_current(storage: Storage) -> None:
    """Every migration file has been applied and recorded."""
    from researcher.storage.postgres import discover_migrations

    pool = storage._pool  # type: ignore[attr-defined]
    async with pool.acquire() as conn:
        recorded = {
            row["filename"] for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }
    assert {path.name for path in discover_migrations()} <= recorded


async def test_postgres_reapplying_migrations_is_a_no_op(storage: Storage) -> None:
    """A second connect must apply nothing, or every start would re-run the schema."""
    from researcher.storage.postgres import run_migrations

    pool = storage._pool  # type: ignore[attr-defined]
    assert await run_migrations(pool) == ()


async def test_postgres_rejects_a_payload_filed_under_the_wrong_key(
    storage: Storage, cleanup: str
) -> None:
    """A cached entry whose payload disagrees with its key is refused.

    Only reachable if a row is written by something other than ``put`` — a
    hand-edit, or a future bug — but serving it would return one query's sources
    for another query, which is worse than a miss.
    """
    pool = storage._pool  # type: ignore[attr-defined]

    # The row's *columns* must match the key we will look up — otherwise the
    # query simply misses and no mismatch is ever examined. So the stored key is
    # the one used for the column values, and only the payload's copy is wrong.
    stored_key = _key(f"{cleanup}-wrong")
    mismatched = _entry(f"{cleanup}-wrong").model_copy(update={"key": _key(f"{cleanup}-claimed")})

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO source_cache (source, query, provider, max_results,"
            " schema_version, payload, fetched_at, expires_at)"
            " VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)",
            stored_key.source.value,
            stored_key.query,
            stored_key.provider,
            stored_key.max_results,
            stored_key.schema_version,
            mismatched.model_dump_json(),
            NOW,
            NOW + timedelta(seconds=9999),
        )

    # Guard against the test passing for the wrong reason: the row must be
    # findable by its key, so the only thing that can reject it is the payload.
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM source_cache WHERE query = $1", stored_key.query
            )
            == 1
        )

    with pytest.raises(StorageError, match="does not match the key"):
        await storage.cache.get(stored_key, now=NOW)


async def test_postgres_survives_a_reconnect(storage: Storage, cleanup: str) -> None:
    """Whatever is written is durable across a fresh pool, not cached in memory."""
    session = _session(f"{cleanup} durable", NOW)
    await storage.sessions.save(session)

    dsn = _resolve_dsn()
    assert dsn is not None
    second = await PostgresStorage.connect(dsn)
    try:
        restored = await second.sessions.get(session.id)
        assert restored is not None
        assert restored.request.question == f"{cleanup} durable question"
    finally:
        await second.aclose()


async def test_postgres_refuses_a_migration_edited_after_it_was_applied(
    storage: Storage, tmp_path
) -> None:
    """Migrations are append-only, and that is enforced rather than assumed.

    The check is a checksum recorded at apply time. Without it, editing an
    applied migration would be silently skipped on every later start — the
    worst outcome, because the file would claim to describe the schema while
    the database held something else.
    """
    applied = discover_migrations()[0]
    pool = storage._pool  # type: ignore[attr-defined]

    edited_dir = tmp_path / "migrations"
    edited_dir.mkdir()
    # Same filename, different contents: a record exists for this name, so the
    # checksum is compared and must not match.
    (edited_dir / applied.name).write_text("-- edited\nSELECT 1;\n", encoding="utf-8")

    with pytest.raises(StorageError, match="changed since it was applied"):
        await run_migrations(pool, directory=edited_dir)
