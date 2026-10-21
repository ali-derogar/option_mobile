"""Independent whole-market expiry snapshot and manual background refresh."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any

import requests

from options.backend.services.public_options import (
    enrich_public_contracts_with_instrument_info,
    fetch_public_instrument_info_many,
    fetch_public_option_market_watch,
    normalize_public_option_pairs,
)
from options.backend.storage import Storage

logger = logging.getLogger(__name__)
REFRESH_COOLDOWN_SECONDS = 60


def valid_expiry(value: Any) -> bool:
    text = str(value)
    if len(text) != 8 or not text.isascii() or not text.isdigit() or int(text[:4]) < 1700:
        return False
    try:
        date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return False
    return True


def fetch_expiry_contracts() -> tuple[list[dict[str, Any]], str | None]:
    contracts = normalize_public_option_pairs(fetch_public_option_market_watch())
    # Check every unique contract before choosing the nearest fifty: metadata
    # can correct a feed's missing or incorrect expiry/strike.
    by_code = {row["ins_code"]: row for row in contracts if row.get("ins_code", 0) > 0}
    if not by_code:
        raise ValueError("no option contracts returned")
    try:
        info = fetch_public_instrument_info_many(list(by_code))
    except (requests.RequestException, ValueError):
        logger.warning("Expiry instrument metadata unavailable", exc_info=True)
        info = {}
    enriched = enrich_public_contracts_with_instrument_info(list(by_code.values()), info)
    rows = []
    invalid_dates = 0
    for contract in enriched:
        if not valid_expiry(contract.get("end_date")):
            invalid_dates += 1
            continue
        rows.append({
            "ins_code": contract["ins_code"],
            "symbol": contract.get("symbol") or str(contract["ins_code"]),
            "underlying_ins_code": contract.get("underlying_ins_code"),
            "underlying_symbol": contract.get("underlying_symbol"),
            "option_type": contract["option_type"],
            "strike_price": contract.get("strike_price"),
            "end_date": int(contract["end_date"]),
        })
    if not rows:
        raise ValueError("no valid expiry dates returned")
    warnings = []
    if missing := len(by_code.keys() - info.keys()):
        warnings.append(f"مشخصات تکمیلی {missing} قرارداد دریافت نشد؛ از اطلاعات معتبر خوراک بازار استفاده شد.")
    if invalid_dates:
        warnings.append(f"{invalid_dates} قرارداد با تاریخ سررسید نامعتبر کنار گذاشته شد.")
    return rows, " ".join(warnings) or None


class ExpiryRefresh:
    """Reserve refreshes under a lock; publish only committed snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_started: float | None = None
        self._status: dict[str, Any] = {"running": False, "last_error": None}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def begin(self) -> str:
        with self._lock:
            if self._status["running"]:
                return "already_running"
            now = time.monotonic()
            if self._last_started is not None and now - self._last_started < REFRESH_COOLDOWN_SECONDS:
                return "cooldown"
            self._last_started = now
            self._status.update(running=True, last_error=None)
            return "started"

    def run(self, storage: Storage) -> None:
        try:
            rows, warning = fetch_expiry_contracts()
            storage.replace_expiry_contracts(rows, warning)
        except Exception:
            logger.exception("Expiry refresh failed")
            with self._lock:
                self._status["last_error"] = "به‌روزرسانی سررسیدها ناموفق بود؛ اطلاعات قبلی حفظ شد. دوباره تلاش کنید."
        finally:
            with self._lock:
                self._status.update(running=False, finished_at=datetime.now(timezone.utc).isoformat())
