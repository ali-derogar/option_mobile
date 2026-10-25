"""Whole-market sentiment acquisition, independent of dashboard watchlists."""

from __future__ import annotations

import logging
import threading
import time
from math import isclose
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from options.backend.analysis.option_sentiment import analyze_participant_flow, analyze_strikes, contract_scope, identity, number, underlying_key
from options.backend.analysis.numeric import finite_sum
from options.backend.config import TSETMC_REQUEST_TIMEOUT
from options.backend.services.expiry_selection import public_warning, valid_expiry
from options.backend.services.historical_options import fetch_public_client_type_for_date_many, fetch_public_option_history_contracts
from options.backend.services.public_options import (
    _session,
    enrich_public_contracts_with_instrument_info,
    fetch_public_instrument_info_many,
    fetch_public_client_type_current_many,
    fetch_public_live_quotes,
    fetch_public_option_market_watch,
    normalize_public_option_pairs,
)
from options.backend.storage import MARKET_TZ, Storage

logger = logging.getLogger(__name__)
MARKET_OVERVIEW_URL = "https://cdn.tsetmc.com/api/MarketData/GetMarketOverview/{market}"
REFRESH_COOLDOWN_SECONDS = 60
TEXT_FIELDS = ("symbol", "underlying_symbol", "underlying_short_name", "option_type", "trade_date")
NUMERIC_FIELDS = (
    "strike_price", "underlying_last_price", "underlying_closing_price", "buy_open_positions",
    "sell_open_positions", "yesterday_open_positions", "trade_value", "trade_volume", "contract_size",
)
PARTICIPANT_FIELDS = {
    measure: tuple(f"{group}_{side}_{measure}" for side in ("buy", "sell") for group in ("natural", "legal"))
    for measure in ("value", "volume")
}


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
    quotes = fetch_public_live_quotes(list(by_code), data_date)
    for row in valid:
        quote = quotes.get(row["ins_code"], {})
        row["trade_value"] = quote.get("trade_value")
        row["trade_volume"] = quote.get("trade_volume")
        row["trade_date"] = data_date if quote else None
        underlying = quotes.get(row.get("underlying_ins_code"), {})
        row["underlying_last_price"] = underlying.get("last_price")
        row["underlying_closing_price"] = underlying.get("closing_price")
    received_at = datetime.now(timezone.utc).isoformat()
    missing_quotes = sum(row["ins_code"] not in quotes for row in valid)
    if missing_quotes:
        logger.info("Live quotes unavailable for %d sentiment contracts; leaving activity unknown.", missing_quotes)
    missing = sum(not info.get(r["ins_code"]) for r in contracts)
    warnings = []
    if missing:
        logger.info("Supplementary metadata unavailable for %d option sentiment contracts; using market-watch data.", missing)
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
    archived = storage.get_option_sentiment_snapshot(data_date)
    preserve_oi = archived is not None and archived["source"] == "public_cdn"
    if preserve_oi:
        for row in archived["contracts"]:
            metadata[int(row["ins_code"])] = row
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
    dated_codes = set(by_code)
    if preserve_oi:
        for row in archived["contracts"]:
            code = identity(row.get("ins_code"))
            if code in by_code:
                for field in ("buy_open_positions", "sell_open_positions", "yesterday_open_positions"):
                    if number(row.get(field)) is not None:
                        by_code[code][field] = number(row[field])
            else:
                # A missing historical quote must not erase an archived OI
                # position or carry old, unverified activity into the repair.
                by_code[code] = {**row, "trade_value": None, "trade_volume": None,
                                 "underlying_last_price": None, "underlying_closing_price": None}
    for code, row in by_code.items():
        row["trade_date"] = data_date if code in dated_codes else None
    return {
        "data_date": data_date, "received_at": datetime.now(timezone.utc).isoformat(),
        "source": "public_cdn" if preserve_oi else "public_history", "contracts": compact_contracts(list(by_code.values())),
        "warning": archived.get("warning") if preserve_oi else "موقعیت باز تاریخی فقط برای قراردادهای دارای آرشیو محلی موجود است؛ خوراک تاریخی عمومی شامل موقعیت باز نیست.",
        "market_complete": archived.get("market_complete", True) if preserve_oi else True,
    }


def activity_date_verified(snapshot: dict) -> bool:
    return snapshot.get("source") != "public_cdn" or any(
        row.get("trade_date") == snapshot["data_date"] for row in snapshot["contracts"])


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
    result["activity"]["date_verified"] = activity_date_verified(snapshot)
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
        for cell in result["activity"]["cells"]:
            for name in ("call", "put", "total"):
                cell[name]["complete"] = False
        for summary in result["summaries"].values():
            for name in ("call", "put", "total"):
                summary[name]["complete"] = False
            summary.update(call_put_ratio=None, ratio_state="unavailable")
        for peak in result["peaks"].values():
            if peak:
                peak["scope_complete"] = False
    return {**result, **{k: snapshot[k] for k in ("data_date", "received_at", "source")},
            "warning": public_warning(snapshot.get("warning")), "dates": dates, "cached": cached}


def _align_participant_cash(participant: dict, contract: dict) -> dict:
    """Reconcile public per-unit option values with full premium turnover.

    Some public history records omit the contract-size multiplier. Scale only
    when both buy and sell totals independently reconcile to market turnover;
    never estimate a group's transaction value using the last price.
    """
    fields = PARTICIPANT_FIELDS["value"]
    values = [number(participant.get(field)) for field in fields]
    expected = number(contract.get("trade_value"))
    size = number(contract.get("contract_size"))
    scale = None
    if expected is not None and expected >= 0 and all(value is not None and value >= 0 for value in values):
        totals = [finite_sum(values[:2]), finite_sum(values[2:])]
        for candidate in (1, size):
            if candidate is not None and candidate > 0 and all(total is not None and isclose(total * candidate, expected, rel_tol=1e-9, abs_tol=0.01) for total in totals):
                scale = candidate
                break
    return {**participant, **{field: number(value * scale) if scale is not None else None for field, value in zip(fields, values)}}


def _align_participant_volume(participant: dict, contract: dict) -> dict:
    """Only use group volumes that reconcile to this market observation."""
    fields = PARTICIPANT_FIELDS["volume"]
    values = [number(participant.get(field)) for field in fields]
    expected = number(contract.get("trade_volume"))
    matched = False
    if expected is not None and expected >= 0 and all(value is not None and value >= 0 for value in values):
        totals = [finite_sum(values[:2]), finite_sum(values[2:])]
        matched = all(total is not None and isclose(total, expected, rel_tol=1e-12, abs_tol=1e-8) for total in totals)
    return {**participant, **{field: value if matched else None for field, value in zip(fields, values)}}


def _merge_participant_data(previous: dict, incoming: dict, contract: dict) -> dict:
    previous = _align_participant_volume(_align_participant_cash(previous, contract), contract)
    incoming = _align_participant_volume(_align_participant_cash(incoming, contract), contract)
    result = {**previous, **incoming}
    for fields in PARTICIPANT_FIELDS.values():
        # Keep all buy/sell and natural/legal values of a measure together.
        # A live volume-only row must not erase valid same-day cash records.
        source = incoming if all(incoming[field] is not None for field in fields) else previous
        result.update({field: source[field] for field in fields})
    return result


def get_flow_payload(storage: Storage, key: str | None, expiry: str, data_date: str, *, include_cash: bool = True) -> dict:
    snapshot = storage.get_option_sentiment_snapshot(data_date)
    contracts = snapshot["contracts"] if snapshot else storage.get_contract_snapshot_df(data_date).to_dict(orient="records")
    scope = contract_scope(contracts, key, expiry)
    rows = [r for r in scope["selection"] if r["option_type"] in {"call", "put"}
            and (number(r.get("strike_price")) or 0) > 0]
    codes = {identity(r["ins_code"]) for r in rows}
    by_code = {identity(r["ins_code"]): r for r in rows}
    local = storage.get_latest_client_type_df(snapshot_date=data_date)
    participants = {identity(r.get("ins_code")): _merge_participant_data({}, r, by_code[identity(r.get("ins_code"))]) for r in local.to_dict(orient="records")
                    if identity(r.get("ins_code")) in codes}
    api_date = data_date.replace("-", "")

    def merge_remote(remote):
        for row in remote:
            code = identity(row.get("ins_code"))
            if code in codes and identity(row.get("rec_date")) == api_date:
                participants[code] = _merge_participant_data(participants.get(code, {}), row, by_code[code])
    # Use the explicit trading date even in latest mode. An undated current
    # response could otherwise put today's flows beside yesterday's market.
    known_zero_codes = {identity(r["ins_code"]) for r in rows
                        if (r.get("trade_date") == data_date or not snapshot or snapshot.get("source") == "public_history")
                        and number(r.get("trade_volume")) == 0 and number(r.get("trade_value")) == 0}
    needed = sorted({int(r["ins_code"]) for r in rows if identity(r["ins_code"]) not in known_zero_codes})
    current = False
    if needed:
        try:
            current = data_date == datetime.now(MARKET_TZ).date().isoformat() and fetch_market_date() == data_date
            if current:
                remote = fetch_public_client_type_current_many(needed, timeout=8)
                if fetch_market_date() != data_date:
                    remote = []
                    current = False
                volumes = {identity(r['ins_code']): number(r.get('trade_volume')) for r in rows}
                validated = []
                for row in remote:
                    code = identity(row.get('ins_code'))
                    recorded = identity(row.get('rec_date'))
                    # Undated live responses must also match both traded-volume
                    # totals of the exact market snapshot. Never relabel a
                    # stale or newer response as this snapshot's trading day.
                    expected = volumes.get(code)
                    totals = [sum(number(row.get(f'{group}_{side}_volume')) or 0 for group in ('natural', 'legal'))
                              if all(number(row.get(f'{group}_{side}_volume')) is not None for group in ('natural', 'legal')) else None
                              for side in ('buy', 'sell')]
                    if recorded == data_date.replace('-', '') or (not recorded and expected is not None and totals == [expected, expected]):
                        validated.append({**row, 'rec_date': int(data_date.replace('-', ''))})
                remote = validated
            else:
                remote = fetch_public_client_type_for_date_many(needed, data_date.replace("-", ""), timeout=8)
            merge_remote(remote)
        except Exception:
            logger.info("Participant flow fetch failed; using same-date local records", exc_info=True)
        if current:
            measures = ("volume", "value") if include_cash else ("volume",)
            missing_details = [code for code in needed if any(participants.get(str(code), {}).get(field) is None
                                                            for measure in measures for field in PARTICIPANT_FIELDS[measure])]
            if missing_details:
                try:
                    merge_remote(fetch_public_client_type_for_date_many(missing_details, api_date, timeout=8))
                except Exception:
                    logger.info("Same-date participant details unavailable; keeping verified values", exc_info=True)
    result = analyze_participant_flow(rows, list(participants.values()), known_zero_codes=known_zero_codes)
    if snapshot and not snapshot.get("market_complete", True):
        for audience in result["audiences"].values():
            for measure in audience.values():
                measure["coverage"]["complete"] = False
    return {**result, "underlying_key": scope["underlying_key"], "expiry": scope["expiry"], "data_date": data_date,
            "received_at": snapshot["received_at"] if snapshot else None}


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
