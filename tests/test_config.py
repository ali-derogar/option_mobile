"""Configuration loading tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_config_loads_dotenv_before_reading_values(tmp_path: Path) -> None:

    runtime_root = tmp_path / "runtime"
    database_path = tmp_path / "custom.sqlite3"
    data_dir = tmp_path / "exports"
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"OPTIONS_RUNTIME_ROOT={runtime_root}",
                f"DATABASE_PATH={database_path}",
                f"DATA_DIR={data_dir}",
                "TSETMC_USERNAME=env-user",
                "TSETMC_PASSWORD=env-pass",
            ]
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    for key in [
        "OPTIONS_RUNTIME_ROOT",
        "DATABASE_PATH",
        "DATA_DIR",
        "TSETMC_USERNAME",
        "TSETMC_PASSWORD",
    ]:
        env.pop(key, None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    script = """
import json
from options.backend import config
print(json.dumps({
    "runtime_root": str(config.RUNTIME_ROOT),
    "database_path": str(config.DATABASE_PATH),
    "data_dir": str(config.DATA_DIR),
    "username": config.TSETMC_USERNAME,
    "password": config.TSETMC_PASSWORD,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        check=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        "runtime_root": str(runtime_root),
        "database_path": str(database_path),
        "data_dir": str(data_dir),
        "username": "env-user",
        "password": "env-pass",
    }
