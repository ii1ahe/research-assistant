"""The cache, and the promise that it cannot break a request.

The cache is an optimisation, so every test here is really asking the same
question in a different way: when this goes wrong, does the run survive? A
service that raised on a storage fault would turn an optimisation into a
dependency, which is the failure mode these tests exist to prevent.

Storage is the in-memory double from ADR-004, so nothing here needs a database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from researcher.config import Settings
from researcher.errors import StorageError
from researcher.models import CacheKey, SourceName, utc_now
from researcher.services.cache import CacheService
from researcher.storage.memory import InMemorySourceCache
from tests.conftest import BrokenCache, make_source

pytestmark = pytest.mark.asyncio

SettingsFactory = Callable[..., Settings]


def _key(**overrides: object) -> CacheKey:
    base: dict[str, object] = {
        "source": SourceName.WIKIPEDIA,
        "query": "what is photosynthesis",
        "provider": "wikipedia",
        "max_results": 3,
    }
    return CacheKey(**(base | overrides))  # type: ignore[arg-type]


@pytest.fixture
def cache(make_settings: SettingsFactory) -> CacheService:
    return CacheService(InMemorySourceCache(), make_settings())


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def test_an_empty_cache_reports_a_plain_miss(cache: CacheService) -> None:
    """A miss is not a failure, and must not be described as one."""
    lookup = await cache.lookup(_key())

    assert lookup.entry is None
    assert lookup.failure is None
    assert not lookup.hit


async def test_a_failed_read_is_a_miss_that_says_so(make_settings: SettingsFactory) -> None:
    """Storage fault and nothing-stored are different, and both continue.

    The caller treats them identically — fetch the source — but only one of them
    is worth telling the user about, so they must be distinguishable.
    """
    service = CacheService(BrokenCache(), make_settings())

    lookup = await service.lookup(_key())

    assert lookup.entry is None
    assert not lookup.hit
    assert lookup.failure is not None
    assert lookup.failure.code == "storage_error"


async def test_a_stored_result_is_returned(cache: CacheService) -> None:
    key = _key()
    sources = (make_source("wikipedia", 1), make_source("wikipedia", 2))

    await cache.store(key, sources)
    lookup = await cache.lookup(key)

    assert lookup.hit
    assert lookup.entry is not None
    assert lookup.entry.sources == sources


async def test_a_stored_result_is_scoped_to_its_whole_key(cache: CacheService) -> None:
    """Every component of the key separates results, not just the query.

    Two entries that differ only in the result limit must not be confused, or
    asking for five results would serve three.
    """
    await cache.store(_key(max_results=3), (make_source("wikipedia", 1),))

    assert not (await cache.lookup(_key(max_results=5))).hit
    assert not (await cache.lookup(_key(query="a different question"))).hit
    assert not (await cache.lookup(_key(provider="tavily"))).hit
    assert (await cache.lookup(_key(max_results=3))).hit


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def test_a_write_reports_success(cache: CacheService) -> None:
    write = await cache.store(_key(), (make_source("wikipedia", 1),))

    assert write.stored
    assert write.failure is None


async def test_a_failed_write_is_reported_and_not_raised(make_settings: SettingsFactory) -> None:
    """A cache that has quietly stopped storing is worse than no cache at all."""
    service = CacheService(BrokenCache(), make_settings())

    write = await service.store(_key(), (make_source("wikipedia", 1),))

    assert not write.stored
    assert write.failure is not None
    assert write.failure.code == "storage_error"


async def test_an_empty_result_is_cached(cache: CacheService) -> None:
    """Remembering "there is nothing here" is the point of a cache.

    Without it, every run repeats the lookup that already came back empty.
    """
    key = _key()

    await cache.store(key, ())
    lookup = await cache.lookup(key)

    assert lookup.hit
    assert lookup.entry is not None
    assert lookup.entry.sources == ()


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


async def test_an_entry_is_fresh_within_its_ttl(cache: CacheService) -> None:
    now = utc_now()
    key = _key()

    await cache.store(key, (make_source("wikipedia", 1),), now=now)

    assert (await cache.lookup(key, now=now)).hit


async def test_an_entry_is_stale_after_its_ttl(make_settings: SettingsFactory) -> None:
    """Staleness is decided by the store, so no caller can forget to check."""
    service = CacheService(InMemorySourceCache(), make_settings(cache_ttl_seconds=60))
    now = utc_now()
    key = _key()

    await service.store(key, (make_source("wikipedia", 1),), now=now)

    assert (await service.lookup(key, now=now + timedelta(seconds=59))).hit
    assert not (await service.lookup(key, now=now + timedelta(seconds=61))).hit


async def test_a_zero_ttl_disables_caching_immediately(make_settings: SettingsFactory) -> None:
    """``CACHE_TTL_SECONDS=0`` is a supported way to turn the cache off."""
    service = CacheService(InMemorySourceCache(), make_settings(cache_ttl_seconds=0))
    key = _key()

    await service.store(key, (make_source("wikipedia", 1),))

    assert not (await service.lookup(key)).hit


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


async def test_purging_removes_expired_entries(make_settings: SettingsFactory) -> None:
    service = CacheService(InMemorySourceCache(), make_settings(cache_ttl_seconds=60))
    now = utc_now()

    await service.store(_key(query="one"), (make_source("wikipedia", 1),), now=now)
    await service.store(_key(query="two"), (make_source("wikipedia", 2),), now=now)

    assert await service.purge_expired(now=now) == 0
    assert await service.purge_expired(now=now + timedelta(seconds=61)) == 2


async def test_a_failed_purge_reports_nothing_removed(make_settings: SettingsFactory) -> None:
    """Housekeeping is not part of a request path, so it cannot propagate."""
    service = CacheService(BrokenCache(), make_settings())

    assert await service.purge_expired() == 0


async def test_a_strict_purge_propagates_a_storage_failure(make_settings: SettingsFactory) -> None:
    """A purge command is the delete's whole purpose, so it cannot degrade."""
    service = CacheService(BrokenCache(), make_settings())

    with pytest.raises(StorageError):
        await service.purge_expired_strict()


async def test_a_strict_purge_with_no_store_reports_nothing_removed(
    make_settings: SettingsFactory,
) -> None:
    service = CacheService(None, make_settings())

    assert await service.purge_expired_strict() == 0


# ---------------------------------------------------------------------------
# No store at all
# ---------------------------------------------------------------------------


async def test_no_store_is_not_reported_as_a_failure(make_settings: SettingsFactory) -> None:
    """Running without a database is a supported configuration, not a fault.

    A store that is absent and a store that is broken both fail to return an
    entry, and the caller does the same thing either way — fetch the source. The
    difference is what the user is told. ``DATABASE_URL`` unset means there is
    nothing to report, so the diagnostics stay clean; reporting a storage error
    here would send someone hunting for a fault that does not exist.
    """
    service = CacheService(None, make_settings())

    lookup = await service.lookup(_key())
    write = await service.store(_key(), (make_source("wikipedia", 1),))

    assert lookup.entry is None
    assert not lookup.hit
    assert lookup.failure is None
    assert not write.stored
    assert write.failure is None


async def test_no_store_means_nothing_is_remembered_between_reads(
    make_settings: SettingsFactory,
) -> None:
    """The no-op is a real no-op, not a store that silently drops writes."""
    service = CacheService(None, make_settings())
    key = _key()

    await service.store(key, (make_source("wikipedia", 1),))

    assert not (await service.lookup(key)).hit


async def test_no_store_has_nothing_to_purge(make_settings: SettingsFactory) -> None:
    assert await CacheService(None, make_settings()).purge_expired() == 0
