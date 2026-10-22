"""Underlying catalogue discovery and persistent watchlist operations."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from math import isfinite
from typing import Any

import requests

from options.backend.config import (
    TSETMC_PUBLIC_CLIENT_TYPE_WORKERS,
    TSETMC_REQUEST_TIMEOUT,
    TSETMC_TRUST_ENV_PROXY,
)
from options.backend.services.public_options import (
    fetch_public_option_market_watch,
    normalize_public_option_pairs,
)
from options.backend.storage import Storage

logger = logging.getLogger(__name__)
CLOSING_PRICE_URL = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceInfo/{ins_code}"


def fetch_underlying_volume(ins_code: int) -> tuple[float | None, str | None]:
    with requests.Session() as session:
        session.trust_env = TSETMC_TRUST_ENV_PROXY
        response = session.get(
            CLOSING_PRICE_URL.format(ins_code=ins_code),
            timeout=min(TSETMC_REQUEST_TIMEOUT, 10),
        )
        response.raise_for_status()
        payload = response.json()
    info = payload.get("closingPriceInfo") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        raise ValueError("missing closing price information")
    raw = info.get("qTotTran5J")
    volume = float(raw) if raw is not None and not isinstance(raw, bool) else None
    if volume is not None and (not isfinite(volume) or volume < 0):
        volume = None
    market_date = str(info.get("dEven") or "")
    return volume, market_date or None


def build_catalog(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate each contract once, including call/put pairs repeated upstream."""
    grouped: dict[int, dict[str, Any]] = {}
    seen: set[int] = set()
    for contract in contracts:
        code = contract.get("underlying_ins_code")
        contract_code = contract.get("ins_code")
        symbol = str(contract.get("underlying_symbol") or "").strip()
        if not code or not contract_code or not symbol or contract_code in seen:
            continue
        seen.add(contract_code)
        item = grouped.setdefault(code, {
            "ins_code": code,
            "symbol": symbol,
            "name": contract.get("underlying_short_name") or symbol,
            "options_trade_volume": None,
            "underlying_trade_volume": None,
            "market_date": None,
        })
        volume = contract.get("trade_volume")
        if volume is not None and isfinite(volume) and volume >= 0:
            item["options_trade_volume"] = (item["options_trade_volume"] or 0) + volume
    return list(grouped.values())


def fetch_catalog() -> tuple[list[dict[str, Any]], int]:
    rows = build_catalog(normalize_public_option_pairs(fetch_public_option_market_watch()))
    if not rows:
        raise ValueError("no valid underlying instruments returned")
    failures = 0
    with ThreadPoolExecutor(max_workers=TSETMC_PUBLIC_CLIENT_TYPE_WORKERS) as executor:
        futures = {executor.submit(fetch_underlying_volume, row["ins_code"]): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                row["underlying_trade_volume"], row["market_date"] = future.result()
                if row["underlying_trade_volume"] is None:
                    failures += 1
            except (requests.RequestException, ValueError, TypeError):
                failures += 1
                logger.warning("Underlying volume unavailable for %s", row["ins_code"])
    return rows, failures


def save_watchlist(storage: Storage, codes: list[int]) -> None:
    existing = set(storage.get_watchlist_codes())
    available = {int(row["ins_code"]) for row in storage.get_underlying_catalog() if row["available"]}
    if set(codes) - available - existing:
        raise ValueError("unknown or unavailable instrument code")
    storage.replace_watchlist(codes)


class CatalogRefresh:
    """One background catalogue refresh at a time; commit only completed results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {"running": False, "last_error": None, "volume_failures": 0}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def begin(self) -> bool:
        with self._lock:
            if self._status["running"]:
                return False
            self._status.update(running=True, last_error=None, volume_failures=0)
            return True

    def run(self, storage: Storage) -> None:
        try:
            rows, failures = fetch_catalog()
            storage.replace_underlying_catalog(rows)
            if rows:
                storage.cleanup_old_history()
            with self._lock:
                self._status.update(volume_failures=failures)
        except Exception:
            logger.exception("Catalogue refresh failed")
            with self._lock:
                self._status["last_error"] = "دریافت فهرست نمادها ناموفق بود؛ دوباره تلاش کنید."
        finally:
            with self._lock:
                self._status.update(running=False, finished_at=datetime.now(timezone.utc).isoformat())
