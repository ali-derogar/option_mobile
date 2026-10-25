"""SQLite copies keep market data separate from installation activation."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def copy_database(source_path: Path, destination_path: Path, *, include_activation: bool = False) -> None:
    """Include committed WAL data; activation is excluded unless moving user data."""
    with closing(sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(destination)
            if not include_activation:
                with destination:
                    exists = destination.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_state'"
                    ).fetchone()
                    if exists:
                        destination.execute("DELETE FROM app_state WHERE key = ?", ("activated",))
