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


def test_restore_legacy_database_includes_committed_wal_data(tmp_path: Path) -> None:
    current = tmp_path / "new" / "options.db"
    legacy = tmp_path / "old" / "options.db"
    _write_db(legacy, rows=1)
    # Keep the connection open so committed rows remain in the WAL rather than
    # being checkpointed into the main file on close.
    db = sqlite3.connect(legacy)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("INSERT INTO contracts DEFAULT VALUES")
        db.commit()
        app_module._restore_legacy_database_if_needed(current, [legacy])
        assert app_module._database_score(current) == 2
    finally:
        db.close()


def _set_activation(path: Path, activated: bool) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL)")
        db.execute(
            "INSERT INTO app_state VALUES ('activated', ?, '2026-10-24 08:00:00')",
            ("1" if activated else "0",),
        )


def _is_activated(path: Path) -> bool:
    with sqlite3.connect(path) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_state'").fetchone()
        if not exists:
            return False
        row = db.execute("SELECT value FROM app_state WHERE key='activated'").fetchone()
        return row is not None and row[0] == "1"


def test_restoring_market_data_cannot_activate_an_existing_unactivated_install(tmp_path: Path) -> None:
    current = tmp_path / "new" / "options.db"
    legacy = tmp_path / "old" / "options.db"
    _write_db(current, rows=0)
    _write_db(legacy, rows=3)
    _set_activation(legacy, True)

    app_module._restore_legacy_database_if_needed(current, [legacy])

    with sqlite3.connect(current) as db:
        assert db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 3
    assert not _is_activated(current)
    assert _is_activated(legacy)  # The old database itself is never modified.


def test_already_activated_install_stays_activated_during_update(tmp_path: Path) -> None:
    current = tmp_path / "new" / "options.db"
    legacy = tmp_path / "old" / "options.db"
    _write_db(current, rows=0)
    _set_activation(current, True)
    _write_db(legacy, rows=3)

    app_module._restore_legacy_database_if_needed(current, [legacy])

    assert _is_activated(current)


def test_moving_existing_user_database_preserves_its_valid_activation(tmp_path: Path) -> None:
    current = tmp_path / "new" / "options.db"
    legacy = tmp_path / "old" / "options.db"
    _write_db(legacy, rows=3)
    _set_activation(legacy, True)

    app_module._restore_legacy_database_if_needed(current, [legacy])

    assert _is_activated(current)


def test_explicitly_locked_install_cannot_be_overwritten_by_activated_legacy_data(tmp_path: Path) -> None:
    current = tmp_path / "new" / "options.db"
    legacy = tmp_path / "old" / "options.db"
    _write_db(current, rows=0)
    _set_activation(current, False)
    _write_db(legacy, rows=3)
    _set_activation(legacy, True)

    app_module._restore_legacy_database_if_needed(current, [legacy])

    assert not _is_activated(current)
