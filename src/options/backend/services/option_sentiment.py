"""Whole-market sentiment acquisition, independent of dashboard watchlists."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from options.backend.analysis.option_sentiment import analyze_strikes, identity, number, underlying_key
from options.backend.config import TSETMC_REQUEST_TIMEOUT
from options.backend.services.expiry_selection import valid_expiry
from options.backend.services.historical_options import fetch_public_option_history_contracts
from options.backend.services.public_options import (
    _session,
    enrich_public_contracts_with_instrument_info,
    fetch_public_instrument_info_many,
    fetch_public_option_market_watch,
    normalize_public_option_pairs,
)
from options.backend.storage import MARKET_TZ, Storage

logger = logging.getLogger(__name__)
MARKET_OVERVIEW_URL = "https://cdn.tsetmc.com/api/MarketData/GetMarketOverview/{market}"
REFRESH_COOLDOWN_SECONDS = 60
TEXT_FIELDS = ("symbol", "underlying_symbol", "underlying_short_name", "option_type")
NUMERIC_FIELDS = (
    "strike_price", "underlying_last_price", "underlying_closing_price", "buy_open_positions",
    "sell_open_positions", "yesterday_open_positions", "trade_value", "trade_volume", "contract_size",
)


def fetch_market_date() -> str:
    def fetch(market):
        with _session() as session:
            response = session.get(MARKET_OVERVIEW_URL.format(market=market), timeout=min(TSETMC_REQUEST_TIMEOUT, 15))
            response.raise_for_status()
            raw = response.json().get("marketOverview", {}).get("marketActivityDEven")
        parsed = datetime.strptime(identity(raw), "%Y%m%d").date()
        if parsed > datetime.now(MARKET_TZ).date():
            raise ValueError("future market date")
        return parsed.isoformat()

    with ThreadPoolExecutor(max_workers=2) as pool:
        dates = list(pool.map(fetch, (1, 2)))
    if len(set(dates)) != 1:
        raise ValueError("inconsistent exchange dates")
    return dates[0]


def compact_contracts(rows: list[dict]) -> list[dict]:
    result = {}
    for row in rows:
        code = identity(row.get("ins_code"))
        if not code or code == "0":
            continue
        result[code] = {
            "ins_code": code, "underlying_ins_code": identity(row.get("underlying_ins_code")) or None,
            "end_date": int(identity(row.get("end_date"))) if identity(row.get("end_date")) else None,
            **{k: str(row[k]) if row.get(k) is not None else None for k in TEXT_FIELDS},
            **{k: number(row.get(k)) for k in NUMERIC_FIELDS},
        }
    return list(result.values())


def fetch_current_snapshot() -> dict:
    data_date = fetch_market_date()
    feed = fetch_public_option_market_watch()
    received_at = datetime.now(timezone.utc).isoformat()
    contracts = normalize_public_option_pairs(feed)
    by_code = {r["ins_code"]: r for r in contracts if r.get("ins_code")}
    if not by_code:
        raise ValueError("empty market feed")
    try:
        info = fetch_public_instrument_info_many(list(by_code))
    except Exception:
        logger.warning("Sentiment metadata unavailable; keeping valid market-watch fields", exc_info=True)
        info = {}
    contracts = enrich_public_contracts_with_instrument_info(list(by_code.values()), info)
    valid = [r for r in contracts if valid_expiry(r.get("end_date")) and underlying_key(r)]
    if not valid:
        raise ValueError("no valid option contracts")
    if fetch_market_date() != data_date:
        raise ValueError("market date changed during acquisition")
    missing = sum(not info.get(r["ins_code"]) for r in contracts)
    warnings = []
    if missing:
        warnings.append(f"مشخصات تکمیلی {missing} قرارداد دریافت نشد؛ اطلاعات معتبر خوراک بازار استفاده شد.")
    if len(valid) != len(contracts):
        warnings.append(f"{len(contracts) - len(valid)} قرارداد با مشخصات نامعتبر کنار گذاشته شد؛ پوشش بازار ناقص است.")
    return {"data_date": data_date, "received_at": received_at, "contracts": compact_contracts(valid),
            "source": "public_cdn", "warning": " ".join(warnings) or None, "market_complete": len(valid) == len(contracts)}


def fetch_historical_snapshot(storage: Storage, data_date: str) -> dict:
    metadata = storage.get_contract_metadata_by_ins_code()
    latest = storage.get_option_sentiment_snapshot()
    for row in latest["contracts"] if latest else []:
        code = identity(row.get("ins_code"))
        if code and code != "0":
            metadata.setdefault(int(code), row)
    rows = fetch_public_option_history_contracts(data_date, metadata)
    if not rows:
        raise ValueError("historical option data not found")
    by_code = {identity(r.get("ins_code")): r for r in rows}
    local = storage.get_contract_snapshot_df(data_date)
    for row in local.to_dict(orient="records"):
        code = identity(row.get("ins_code"))
        if code in by_code:
            for field in ("buy_open_positions", "sell_open_positions", "yesterday_open_positions"):
                by_code[code][field] = number(row.get(field))
        elif code:
            by_code[code] = row
    return {
        "data_date": data_date, "received_at": datetime.now(timezone.utc).isoformat(),
        "source": "public_history", "contracts": compact_contracts(list(by_code.values())),
        "warning": "موقعیت باز تاریخی فقط برای قراردادهای دارای آرشیو محلی موجود است؛ خوراک تاریخی عمومی شامل موقعیت باز نیست.",
    }


def get_payload(storage: Storage, key: str | None, expiry: str, data_date: str | None) -> dict:
    snapshot = storage.get_option_sentiment_snapshot(data_date)
    dates = sorted(set(storage.get_option_sentiment_dates() + storage.get_available_snapshot_dates()), reverse=True)
    cached = snapshot is not None
    if snapshot is None:
        local = storage.get_contract_snapshot_df(data_date) if data_date else None
        snapshot = {
            "contracts": compact_contracts(local.to_dict(orient="records")) if local is not None else [],
            "data_date": data_date, "received_at": None, "source": "local_archive" if data_date else None,
            "warning": "داده کامل بازار برای این تاریخ هنوز دریافت نشده است." if data_date else None,
        }
    result = analyze_strikes(snapshot["contracts"], key, expiry)
    if not cached:
        unknown_scopes = ["market"]
        if not result["quality"]["selected_contract_count"]:
            unknown_scopes.extend(("underlying", "selection"))
        for scope in unknown_scopes:
            summary = result["summaries"][scope]
            for name in ("call", "put", "total"):
                summary[name].update(value=None, complete=False)
            summary.update(call_put_ratio=None, ratio_state="unavailable")
    elif not snapshot.get("market_complete", True):
        for summary in result["summaries"].values():
            for name in ("call", "put", "total"):
                summary[name]["complete"] = False
            summary.update(call_put_ratio=None, ratio_state="unavailable")
        for peak in result["peaks"].values():
            if peak:
                peak["scope_complete"] = False
    return {**result, **{k: snapshot[k] for k in ("data_date", "received_at", "source", "warning")},
            "dates": dates, "cached": cached}


class OptionSentimentRefresh:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_started = None
        self._status = {"running": False, "last_error": None, "target_date": None}

    def status(self):
        with self._lock:
            return dict(self._status)

    def begin(self, data_date: str | None = None) -> dict:
        with self._lock:
            if self._status["running"]:
                return {"status": "already_running"}
            elapsed = time.monotonic() - self._last_started if self._last_started is not None else REFRESH_COOLDOWN_SECONDS
            if elapsed < REFRESH_COOLDOWN_SECONDS:
                return {"status": "cooldown", "retry_after": max(1, int(REFRESH_COOLDOWN_SECONDS - elapsed) + 1)}
            self._last_started = time.monotonic()
            self._status = {"running": True, "last_error": None, "target_date": data_date,
                            "started_at": datetime.now(timezone.utc).isoformat()}
            return {"status": "started"}

    def run(self, storage: Storage, data_date: str | None = None):
        error = None
        try:
            snapshot = fetch_historical_snapshot(storage, data_date) if data_date else fetch_current_snapshot()
            storage.replace_option_sentiment_snapshot(snapshot["data_date"], snapshot["contracts"],
                snapshot["received_at"], snapshot["source"], snapshot["warning"], market_complete=snapshot.get("market_complete", True))
            if not data_date:
                storage.cleanup_old_history()
        except Exception:
            logger.exception("Option sentiment refresh failed")
            error = "دریافت داده سنتیمنت ناموفق بود؛ داده موفق قبلی حفظ شده است. اتصال و تاریخ انتخابی را بررسی کنید."
        finally:
            with self._lock:
                self._status.update(running=False, last_error=error, finished_at=datetime.now(timezone.utc).isoformat())
