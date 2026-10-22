"""Independent whole-market expiry snapshot and manual background refresh."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

import requests

from options.backend.services.daily_prices import fetch_public_daily_price_ranges
from options.backend.services.expiry_selection import (
    ExpiryFilters,
    ExpirySnapshot,
    MARKET_TZ,
    select_expiries,
    valid_expiry,
)
from options.backend.services.public_options import (
    enrich_public_contracts_with_instrument_info,
    fetch_public_instrument_info_many,
    fetch_public_option_market_watch,
    normalize_public_option_pairs,
)
from options.backend.storage import Storage

logger = logging.getLogger(__name__)
REFRESH_COOLDOWN_SECONDS = 60
METADATA_BATCH_SIZE = 50
ProgressCallback = Callable[[ExpirySnapshot, int, int], None]


def _expiry_row(contract: dict[str, Any]) -> dict[str, Any] | None:
    if not valid_expiry(contract.get("end_date")):
        return None
    return {
        "ins_code": contract["ins_code"],
        "symbol": contract.get("symbol") or str(contract["ins_code"]),
        "underlying_ins_code": contract.get("underlying_ins_code"),
        "underlying_symbol": contract.get("underlying_symbol"),
        "option_type": contract["option_type"],
        "strike_price": contract.get("strike_price"),
        "end_date": int(contract["end_date"]),
        "ask_price": contract.get("ask_price"),
        "ask_volume": contract.get("ask_volume"),
        "underlying_last_price": contract.get("underlying_last_price"),
        "underlying_closing_price": contract.get("underlying_closing_price"),
        "trade_volume": contract.get("trade_volume"),
        "price_min": contract.get("price_min"),
        "price_max": contract.get("price_max"),
        "price_range_date": contract.get("price_range_date"),
    }


def _warning(missing: int, invalid_dates: int) -> str | None:
    warnings = []
    if missing:
        warnings.append(f"مشخصات تکمیلی {missing} قرارداد دریافت نشد؛ از اطلاعات معتبر خوراک بازار استفاده شد.")
    if invalid_dates:
        warnings.append(f"{invalid_dates} قرارداد با تاریخ سررسید نامعتبر کنار گذاشته شد.")
    return " ".join(warnings) or None


def fetch_expiry_contracts(
    progress_callback: ProgressCallback | None = None,
) -> ExpirySnapshot:
    feed = fetch_public_option_market_watch()
    received_at = datetime.now(timezone.utc).isoformat()
    contracts = normalize_public_option_pairs(feed)
    by_code = {row["ins_code"]: row for row in contracts if row.get("ins_code", 0) > 0}
    if not by_code:
        raise ValueError("no option contracts returned")
    rows_by_code = {}
    invalid_codes: set[int] = set()
    for code, contract in by_code.items():
        row = _expiry_row(contract)
        if row is None:
            invalid_codes.add(code)
        else:
            rows_by_code[code] = row
    processed: set[int] = set()
    missing: set[int] = set()

    def snapshot() -> ExpirySnapshot:
        return ExpirySnapshot(
            list(rows_by_code.values()), _warning(len(missing), len(invalid_codes)), received_at,
        )

    def publish() -> None:
        if progress_callback is not None:
            progress_callback(snapshot(), len(processed), len(by_code))

    def consume(code: int, info: dict[str, Any] | None) -> None:
        if code in processed:
            return
        enriched = enrich_public_contracts_with_instrument_info([by_code[code]], {code: info} if info else {})[0]
        row = _expiry_row(enriched)
        if row is None:
            invalid_codes.add(code)
            rows_by_code.pop(code, None)
        else:
            invalid_codes.discard(code)
            rows_by_code[code] = row
        if not info:
            missing.add(code)
        processed.add(code)
        publish()

    # Show the feed immediately, then correct each contract as its metadata
    # arrives. Unknown dates and nearby expiries are checked first in batches.
    publish()
    codes = sorted(by_code, key=lambda code: (
        rows_by_code[code]["end_date"] if code in rows_by_code else 0, code,
    ))
    for offset in range(0, len(codes), METADATA_BATCH_SIZE):
        batch = codes[offset:offset + METADATA_BATCH_SIZE]
        try:
            info = fetch_public_instrument_info_many(batch, on_result=consume)
        except (requests.RequestException, ValueError):
            logger.warning("Expiry instrument metadata unavailable", exc_info=True)
            info = {}
        # Also handle batch sources that only return a mapping (e.g. cached
        # implementations). Completed callbacks are never processed twice.
        for code in batch:
            if code not in processed:
                consume(code, info.get(code))
    if not rows_by_code:
        raise ValueError("no valid expiry dates returned")
    # Metadata may correct the expiry date. Resolve it before selecting calls
    # for the extra quote requests, and keep the original feed's Tehran day.
    market_date = datetime.fromisoformat(received_at).astimezone(MARKET_TZ).date()
    range_codes = [
        code for code, row in rows_by_code.items()
        if row["option_type"] == "call" and row["end_date"] == int(market_date.strftime("%Y%m%d"))
    ]
    if range_codes:
        ranges = fetch_public_daily_price_ranges(range_codes, market_date)
        for code in range_codes:
            if code in ranges:
                # Earlier snapshots may still be read outside the refresh lock.
                # Replace the row, as metadata updates do, rather than mutate it.
                rows_by_code[code] = {**rows_by_code[code], **ranges[code]}
        if ranges:
            publish()
    if missing:
        logger.info("Supplementary metadata unavailable for %d expiry contracts; using market-watch data.", len(missing))
    return snapshot()


class ExpiryRefresh:
    """Publish incremental previews while preserving the last complete snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_started: float | None = None
        self._status: dict[str, Any] = {"running": False, "last_error": None}
        self._preview: ExpirySnapshot | None = None
        self._preview_updated_at: str | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def view(self, today: date, filters: ExpiryFilters | None = None) -> dict[str, Any]:
        """Copy preview and status together so completion cannot race with reads."""
        with self._lock:
            result = {"refresh": dict(self._status), "partial": False}
            if not self._status["running"] or self._preview is None:
                return result
            snapshot = self._preview
            updated_at = self._preview_updated_at
        result.update(select_expiries(snapshot, today, filters))
        result.update(
            partial=True, preview_updated_at=updated_at,
        )
        return result

    def _publish(self, snapshot: ExpirySnapshot, processed: int, total: int) -> None:
        with self._lock:
            self._preview = snapshot
            self._preview_updated_at = datetime.now(timezone.utc).isoformat()
            self._status.update(stage="metadata", processed=processed, total=total)

    def begin(self) -> str:
        with self._lock:
            if self._status["running"]:
                return "already_running"
            now = time.monotonic()
            if self._last_started is not None and now - self._last_started < REFRESH_COOLDOWN_SECONDS:
                return "cooldown"
            self._last_started = now
            self._preview = None
            self._preview_updated_at = None
            self._status.update(running=True, last_error=None, stage="market", processed=0, total=0, finished_at=None)
            return "started"

    def run(self, storage: Storage) -> None:
        try:
            snapshot = fetch_expiry_contracts(progress_callback=self._publish)
            with self._lock:
                self._status["stage"] = "saving"
            storage.replace_expiry_contracts(
                snapshot.rows, snapshot.warning, market_received_at=snapshot.market_received_at,
            )
            if snapshot.rows:
                storage.cleanup_old_history()
        except Exception:
            logger.exception("Expiry refresh failed")
            with self._lock:
                self._status.update(
                    stage="failed",
                    last_error="به‌روزرسانی سررسیدها ناموفق بود؛ آخرین دریافت کامل حفظ شد. دوباره تلاش کنید.",
                )
        finally:
            with self._lock:
                self._status.update(running=False, finished_at=datetime.now(timezone.utc).isoformat())
                if not self._status["last_error"]:
                    self._status["stage"] = "complete"
                self._preview = None
