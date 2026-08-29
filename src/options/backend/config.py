"""Application configuration from environment variables."""

import os
import shutil
from math import isfinite
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT

load_dotenv()


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, min_value), max_value)


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not isfinite(value):
        return default
    return min(max(value, min_value), max_value)

RUNTIME_ROOT = Path(os.getenv("OPTIONS_RUNTIME_ROOT", Path.home()))
SEED_DATABASE_PATH = PACKAGE_ROOT / "resources" / "data" / "tsetmc_options.db"

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", RUNTIME_ROOT / "data" / "tsetmc_options.db"))

TSETMC_USERNAME = os.getenv("TSETMC_USERNAME", "").strip()
TSETMC_PASSWORD = os.getenv("TSETMC_PASSWORD", "").strip()
TSETMC_BASE_URL = os.getenv("TSETMC_BASE_URL", "https://api.tsetmc.com").rstrip("/")
TSETMC_FLOW = _env_int("TSETMC_FLOW", 3, 1, 10)
TSETMC_LOGIN_TIMEOUT = _env_float("TSETMC_LOGIN_TIMEOUT", 10, 3, 30)
TSETMC_REQUEST_TIMEOUT = _env_float("TSETMC_REQUEST_TIMEOUT", 60, 5, 120)
TSETMC_TRUST_ENV_PROXY = os.getenv("TSETMC_TRUST_ENV_PROXY", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
TSETMC_PUBLIC_CLIENT_TYPE_WORKERS = _env_int("TSETMC_PUBLIC_CLIENT_TYPE_WORKERS", 16, 1, 16)

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

if not DATABASE_PATH.exists() and SEED_DATABASE_PATH.exists():
    shutil.copy2(SEED_DATABASE_PATH, DATABASE_PATH)


def validate_credentials() -> None:
    if not TSETMC_USERNAME or not TSETMC_PASSWORD:
        raise ValueError(
            "TSETMC_USERNAME and TSETMC_PASSWORD must be set in .env file. "
            "Copy .env.example to .env and fill in your credentials."
        )
