"""Application configuration from environment variables."""

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT

load_dotenv()

RUNTIME_ROOT = Path(os.getenv("OPTIONS_RUNTIME_ROOT", Path.home() / ".options"))
SEED_DATABASE_PATH = PACKAGE_ROOT / "resources" / "data" / "tsetmc_options.db"

DATA_DIR = Path(os.getenv("DATA_DIR", RUNTIME_ROOT / "data" / "exports"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", RUNTIME_ROOT / "data" / "tsetmc_options.db"))

TSETMC_USERNAME = os.getenv("TSETMC_USERNAME", "").strip()
TSETMC_PASSWORD = os.getenv("TSETMC_PASSWORD", "").strip()
TSETMC_BASE_URL = os.getenv("TSETMC_BASE_URL", "https://api.tsetmc.com").rstrip("/")
TSETMC_FLOW = int(os.getenv("TSETMC_FLOW", "3"))
TSETMC_LOGIN_TIMEOUT = float(os.getenv("TSETMC_LOGIN_TIMEOUT", "10"))
TSETMC_REQUEST_TIMEOUT = float(os.getenv("TSETMC_REQUEST_TIMEOUT", "60"))
TSETMC_TRUST_ENV_PROXY = os.getenv("TSETMC_TRUST_ENV_PROXY", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
TSETMC_PUBLIC_CLIENT_TYPE_WORKERS = int(os.getenv("TSETMC_PUBLIC_CLIENT_TYPE_WORKERS", "16"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

if not DATABASE_PATH.exists() and SEED_DATABASE_PATH.exists():
    shutil.copy2(SEED_DATABASE_PATH, DATABASE_PATH)


def validate_credentials() -> None:
    if not TSETMC_USERNAME or not TSETMC_PASSWORD:
        raise ValueError(
            "TSETMC_USERNAME and TSETMC_PASSWORD must be set in .env file. "
            "Copy .env.example to .env and fill in your credentials."
        )
