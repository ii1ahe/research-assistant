"""Migration discovery and the append-only guarantee.

These are schema concerns rather than storage behaviour, so they live apart from
``test_storage.py`` — which also keeps them free of that module's async marker,
since nothing here needs an event loop.

Discovery is tested against temporary directories, so the whole module runs
offline. The one test that needs a live database is in ``test_storage.py``
alongside the other PostgreSQL integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researcher.errors import StorageError
from researcher.storage.postgres import discover_migrations, migrations_dir


def test_default_directory_is_the_repository_root() -> None:
    """Migrations are found relative to the package, not the working directory.

    Resolving from ``__file__`` is what lets the CLI be invoked from anywhere;
    resolving from the current directory would work in a shell at the repo root
    and fail everywhere else, including in the container.
    """
    target = migrations_dir()
    assert target.name == "migrations"
    assert target.is_dir()
    assert (target / "001_initial_schema.sql").is_file()


def test_discover_migrations_rejects_a_missing_directory(tmp_path: Path) -> None:
    """A missing directory means an incomplete deployment, not an empty schema."""
    with pytest.raises(StorageError, match="migration directory not found"):
        discover_migrations(tmp_path / "absent")


def test_discover_migrations_rejects_an_empty_directory(tmp_path: Path) -> None:
    """No migrations at all is a deployment fault, so it is loud rather than silent."""
    with pytest.raises(StorageError, match="no migrations found"):
        discover_migrations(tmp_path)


def test_discover_migrations_is_ordered_by_filename(tmp_path: Path) -> None:
    """Migrations are applied in filename order, which is why they are numbered."""
    for name in ("002_second.sql", "001_first.sql", "010_tenth.sql"):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")

    assert [path.name for path in discover_migrations(tmp_path)] == [
        "001_first.sql",
        "002_second.sql",
        "010_tenth.sql",
    ]


def test_discover_migrations_ignores_files_that_are_not_sql(tmp_path: Path) -> None:
    """A README or a stray backup in the directory must not be executed."""
    (tmp_path / "001_real.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a migration", encoding="utf-8")
    (tmp_path / "001_real.sql.bak").write_text("SELECT 1;", encoding="utf-8")

    assert [path.name for path in discover_migrations(tmp_path)] == ["001_real.sql"]


def test_real_migrations_are_discovered_in_order() -> None:
    """The shipped schema is discoverable and numbered consistently."""
    names = [path.name for path in discover_migrations()]
    assert names == sorted(names)
    assert "001_initial_schema.sql" in names
