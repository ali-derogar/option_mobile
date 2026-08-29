"""Android app data migration checks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from options import app as app_module


def _write_db(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE contracts (id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO contracts DEFAULT VALUES", [() for _ in range(rows)])


def test_restore_legacy_database_when_current_database_is_empty(tmp_path: Path) -> None:
    current = tmp_path / "new" / "data" / "tsetmc_options.db"
    legacy = tmp_path / "old" / "data" / "tsetmc_options.db"
    _write_db(current, rows=0)
    _write_db(legacy, rows=3)

    app_module._restore_legacy_database_if_needed(current, [legacy])

    assert app_module._database_score(current) == 3
    assert list(current.parent.glob("*.empty-before-migration-*.db"))


def test_restore_legacy_database_does_not_overwrite_existing_user_data(tmp_path: Path) -> None:
    current = tmp_path / "new" / "data" / "tsetmc_options.db"
    legacy = tmp_path / "old" / "data" / "tsetmc_options.db"
    _write_db(current, rows=1)
    _write_db(legacy, rows=3)

    app_module._restore_legacy_database_if_needed(current, [legacy])

    assert app_module._database_score(current) == 1
    assert not list(current.parent.glob("*.empty-before-migration-*.db"))


def test_restore_legacy_database_chooses_candidate_with_data(tmp_path: Path) -> None:
    current = tmp_path / "new" / "data" / "tsetmc_options.db"
    empty_legacy = tmp_path / "empty" / "data" / "tsetmc_options.db"
    populated_legacy = tmp_path / "old" / "data" / "tsetmc_options.db"
    _write_db(empty_legacy, rows=0)
    _write_db(populated_legacy, rows=2)

    app_module._restore_legacy_database_if_needed(current, [empty_legacy, populated_legacy])

    assert app_module._database_score(current) == 2
