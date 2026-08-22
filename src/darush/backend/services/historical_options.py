"""Historical public TSETMC option data reconstruction."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests

from darush.backend.analysis.sentiment import compute_intrinsic_value, compute_moneyness
from darush.backend.config import (
    TSETMC_PUBLIC_CLIENT_TYPE_WORKERS,
    TSETMC_REQUEST_TIMEOUT,
    TSETMC_TRUST_ENV_PROXY,
)
from darush.backend.services.public_options import normalize_public_client_type

HISTORY_IN_DAY_URL = "https://cdn.tsetmc.com/api/ClosingPrice/GetInstrmentsHistoryInDay/{date}"
CLIENT_TYPE_HISTORY_URL = "https://cdn.tsetmc.com/api/ClientType/GetClientTypeHistory/{ins_code}"


def import_public_option_history(
    snapshot_date: str,
    metadata_by_ins_code: Optional[Dict[int, Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch and normalize option contracts for a historical trading date.

    The public historical API provides price/volume data for all traded symbols
    and client type history per instrument, but does not expose historical open
    interest. Open interest fields are therefore left empty unless a local
    snapshot already exists.
    """
    api_date = _api_date(snapshot_date)
    rows = fetch_instruments_history_in_day(api_date)
    metadata_by_ins_code = metadata_by_ins_code or {}
    lookup = _build_underlying_lookup(rows)
    contracts = [
        contract
        for contract in (
            _normalize_historical_option(row, lookup, metadata_by_ins_code)
            for row in rows
        )
        if contract
    ]
    client_type_rows = fetch_public_client_type_for_date_many(
        [c["ins_code"] for c in contracts],
        api_date,
    )
    return contracts, client_type_rows


def fetch_instruments_history_in_day(api_date: str) -> List[Dict[str, Any]]:
    session = _session()
    response = session.get(
        HISTORY_IN_DAY_URL.format(date=api_date),
        timeout=TSETMC_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("closingPriceDailyHistoryWithInstDetails", payload)
    return rows if isinstance(rows, list) else []


def fetch_public_client_type_for_date_many(
    ins_codes: List[int],
    api_date: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    workers = max(1, TSETMC_PUBLIC_CLIENT_TYPE_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_public_client_type_for_date, ins_code, api_date): ins_code
            for ins_code in ins_codes
        }
        for future in as_completed(futures):
            try:
                row = future.result()
                if row:
                    rows.append(row)
            except requests.RequestException:
                continue
    return rows


def fetch_public_client_type_for_date(ins_code: int, api_date: str) -> Optional[Dict[str, Any]]:
    session = _session()
    response = session.get(
        CLIENT_TYPE_HISTORY_URL.format(ins_code=ins_code),
        timeout=TSETMC_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("clientType", [])
    if not isinstance(rows, list):
        return None
    rec_date = int(api_date)
    for row in rows:
        if _to_int(row.get("recDate")) == rec_date:
            return normalize_public_client_type(row)
    return None


def _normalize_historical_option(
    row: Dict[str, Any],
    lookup: Dict[str, Dict[str, Any]],
    metadata_by_ins_code: Dict[int, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    parsed = _parse_option_name(row)
    if not parsed:
        return None

    ins_code = _to_int(row.get("insCode"))
    if not ins_code:
        return None

    meta = metadata_by_ins_code.get(ins_code, {})
    underlying = lookup.get(_normalize_text(parsed["underlying_symbol"]), {})
    underlying_price = _to_float(underlying.get("pDrCotVal")) or _to_float(underlying.get("pClosing"))
    strike_price = _to_float(parsed.get("strike_price"))
    option_type = parsed["option_type"]

    return {
        "ins_code": ins_code,
        "instrument_id": row.get("instrumentID") or meta.get("instrument_id") or str(ins_code),
        "option_type": option_type,
        "symbol": row.get("lVal18AFC"),
        "short_name": row.get("lVal18AFC"),
        "long_name": row.get("lVal30"),
        "isin": meta.get("isin"),
        "buy_open_positions": None,
        "sell_open_positions": None,
        "yesterday_open_positions": None,
        "contract_size": meta.get("contract_size") or 1000,
        "strike_price": strike_price,
        "underlying_ins_code": meta.get("underlying_ins_code") or _to_int(underlying.get("insCode")),
        "underlying_symbol": meta.get("underlying_symbol") or parsed["underlying_symbol"],
        "underlying_short_name": meta.get("underlying_short_name") or parsed["underlying_symbol"],
        "underlying_last_price": _to_float(underlying.get("pDrCotVal")),
        "underlying_closing_price": _to_float(underlying.get("pClosing")),
        "moneyness": compute_moneyness(option_type, strike_price, underlying_price),
        "intrinsic_value": compute_intrinsic_value(option_type, strike_price, underlying_price),
        "begin_date": meta.get("begin_date"),
        "end_date": parsed.get("end_date") or meta.get("end_date"),
        "a_factor": meta.get("a_factor"),
        "b_factor": meta.get("b_factor"),
        "c_factor": meta.get("c_factor"),
        "market_name": meta.get("market_name") or "بازار اختیار معامله",
        "sector": meta.get("sector"),
        "last_price": _to_float(row.get("pDrCotVal")),
        "closing_price": _to_float(row.get("pClosing")),
        "price_change": _price_change(row),
        "trade_volume": _to_float(row.get("qTotTran5J")),
        "trade_value": _to_float(row.get("qTotCap")),
        "trade_count": _to_int(row.get("zTotTran")),
        "price_min": _to_float(row.get("priceMin")),
        "price_max": _to_float(row.get("priceMax")),
        "instrument_meta": row,
    }


def _parse_option_name(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = str(row.get("lVal30") or "")
    symbol = str(row.get("lVal18AFC") or "")
    option_type = None
    if "اختيارخ" in name or "اختیارخ" in name or symbol.startswith("ض"):
        option_type = "call"
    elif "اختيارف" in name or "اختیارف" in name or symbol.startswith("ط"):
        option_type = "put"
    if not option_type:
        return None

    match = re.search(r"اخت[يی]ار[خف]\s+(.+?)-([0-9.]+)-([0-9/]+)\s*$", name)
    if not match:
        return None
    underlying_symbol, strike_price, expiry = match.groups()
    return {
        "option_type": option_type,
        "underlying_symbol": underlying_symbol.strip(),
        "strike_price": strike_price,
        "end_date": _jalali_expiry_to_gregorian_int(expiry),
    }


def _build_underlying_lookup(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_text(row.get("lVal18AFC"))
        name = _normalize_text(row.get("lVal30"))
        if symbol:
            lookup.setdefault(symbol, row)
        if name:
            lookup.setdefault(name, row)
    return lookup


def _api_date(snapshot_date: str) -> str:
    return snapshot_date.replace("-", "")


def _jalali_expiry_to_gregorian_int(value: str) -> Optional[int]:
    parts = value.strip().split("/")
    try:
        if len(parts) == 3:
            year = int(parts[0])
            if year < 100:
                year += 1400
            month = int(parts[1])
            day = int(parts[2])
        elif len(value) == 8 and value.startswith("14"):
            year = int(value[:4])
            month = int(value[4:6])
            day = int(value[6:8])
        elif len(value) == 6:
            year = int(value[:2]) + 1400
            month = int(value[2:4])
            day = int(value[4:6])
        else:
            return None
    except ValueError:
        return None
    g_year, g_month, g_day = _jalali_to_gregorian(year, month, day)
    return g_year * 10000 + g_month * 100 + g_day


def _jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0)
    month_days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    for dim in month_days:
        if gd <= dim:
            break
        gd -= dim
        gm += 1
    return gy, gm, gd


def _normalize_text(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
    )


def _price_change(row: Dict[str, Any]) -> Optional[str]:
    value = _to_float(row.get("priceChange"))
    return None if value is None else f"{value:+.0f}"


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = TSETMC_TRUST_ENV_PROXY
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tsetmc.com/",
        }
    )
    return session
