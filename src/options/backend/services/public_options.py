"""Public TSETMC option market watch service.

This endpoint is part of TSETMC's public CDN API and does not require the paid
REST API login. It is a useful fallback when account credentials are invalid or
the authenticated API is unavailable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from math import isfinite
import re
from typing import Any, Dict, List, Optional

import requests

from options.backend.analysis.sentiment import compute_intrinsic_value, compute_moneyness
from options.backend.config import (
    TSETMC_PUBLIC_CLIENT_TYPE_WORKERS,
    TSETMC_REQUEST_TIMEOUT,
    TSETMC_TRUST_ENV_PROXY,
)

OPTION_MARKET_WATCH_URL = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentOptionMarketWatch/1"
INSTRUMENT_INFO_URL = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentInfo/{ins_code}"
CLIENT_TYPE_CURRENT_URL = "https://cdn.tsetmc.com/api/ClientType/GetClientType/{ins_code}/1/0"
CLIENT_TYPE_HISTORY_URL = "https://cdn.tsetmc.com/api/ClientType/GetClientTypeHistory/{ins_code}"


def fetch_public_option_market_watch() -> List[Dict[str, Any]]:
    session = _session()
    response = session.get(OPTION_MARKET_WATCH_URL, timeout=TSETMC_REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("instrumentOptMarketWatch", []) if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def normalize_public_option_pairs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    contracts: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        call = _normalize_side(row, "C", "call")
        put = _normalize_side(row, "P", "put")
        if call:
            contracts.append(call)
        if put:
            contracts.append(put)
    return contracts


def fetch_public_instrument_info(ins_code: int) -> Optional[Dict[str, Any]]:
    session = _session()
    response = session.get(
        INSTRUMENT_INFO_URL.format(ins_code=ins_code),
        timeout=min(TSETMC_REQUEST_TIMEOUT, 10),
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        for key in ("instrumentInfo", "instrument", "instrumentInfoModel"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload
    return None


def fetch_public_instrument_info_many(ins_codes: List[int]) -> Dict[int, Dict[str, Any]]:
    result: Dict[int, Dict[str, Any]] = {}
    workers = max(1, TSETMC_PUBLIC_CLIENT_TYPE_WORKERS)
    unique_codes = sorted({code for code in ins_codes if code})
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_public_instrument_info, ins_code): ins_code
            for ins_code in unique_codes
        }
        for future in as_completed(futures):
            ins_code = futures[future]
            try:
                row = future.result()
                if row:
                    result[ins_code] = row
            except (requests.RequestException, ValueError):
                continue
    return result


def enrich_public_contracts_with_instrument_info(
    contracts: List[Dict[str, Any]],
    instrument_info_by_code: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        _merge_public_instrument_info(contract, instrument_info_by_code.get(contract.get("ins_code") or 0))
        for contract in contracts
    ]


def fetch_public_client_type_latest(ins_code: int) -> Optional[Dict[str, Any]]:
    session = _session()
    url = CLIENT_TYPE_HISTORY_URL.format(ins_code=ins_code)
    response = session.get(url, timeout=TSETMC_REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    rows = payload.get("clientType", [])
    if not isinstance(rows, list) or not rows:
        return None
    valid_rows = [row for row in rows if isinstance(row, dict)]
    if not valid_rows:
        return None
    latest = max(valid_rows, key=lambda row: _to_int(row.get("recDate")) or 0)
    return normalize_public_client_type(latest)


def fetch_public_client_type_current(ins_code: int) -> Optional[Dict[str, Any]]:
    session = _session()
    url = CLIENT_TYPE_CURRENT_URL.format(ins_code=ins_code)
    response = session.get(url, timeout=TSETMC_REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    row = payload.get("clientType")
    if not isinstance(row, dict):
        return None
    return normalize_public_client_type({"insCode": ins_code, **row})


def fetch_public_client_type_current_many(ins_codes: List[int]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    workers = max(1, TSETMC_PUBLIC_CLIENT_TYPE_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_public_client_type_current, ins_code): ins_code
            for ins_code in ins_codes
        }
        for future in as_completed(futures):
            try:
                row = future.result()
                if row:
                    rows.append(row)
            except (requests.RequestException, ValueError):
                continue
    return rows


def fetch_public_client_type_latest_many(ins_codes: List[int]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    workers = max(1, TSETMC_PUBLIC_CLIENT_TYPE_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_public_client_type_latest, ins_code): ins_code
            for ins_code in ins_codes
        }
        for future in as_completed(futures):
            try:
                row = future.result()
                if row:
                    rows.append(row)
            except (requests.RequestException, ValueError):
                continue
    return rows


def normalize_public_client_type(row: Dict[str, Any]) -> Dict[str, Any]:
    # Public CDN/filter fields use I = individual/natural and N = legal.
    natural_buy_value = _to_float(row.get("buy_I_Value"))
    natural_sell_value = _to_float(row.get("sell_I_Value"))
    legal_buy_value = _to_float(row.get("buy_N_Value"))
    legal_sell_value = _to_float(row.get("sell_N_Value"))
    return {
        "rec_date": _to_int(row.get("recDate")),
        "ins_code": _to_int(row.get("insCode")) or 0,
        "natural_buy_volume": _to_float(row.get("buy_I_Volume")),
        "natural_buy_value": natural_buy_value,
        "natural_buy_count": _first_int(row.get("buy_I_Count"), row.get("buy_CountI")),
        "natural_sell_volume": _to_float(row.get("sell_I_Volume")),
        "natural_sell_value": natural_sell_value,
        "natural_sell_count": _first_int(row.get("sell_I_Count"), row.get("sell_CountI")),
        "legal_buy_volume": _to_float(row.get("buy_N_Volume")),
        "legal_buy_value": legal_buy_value,
        "legal_buy_count": _first_int(row.get("buy_N_Count"), row.get("buy_CountN")),
        "legal_sell_volume": _to_float(row.get("sell_N_Volume")),
        "legal_sell_value": legal_sell_value,
        "legal_sell_count": _first_int(row.get("sell_N_Count"), row.get("sell_CountN")),
        "natural_money_flow": _net_flow(natural_buy_value, natural_sell_value),
        "legal_money_flow": _net_flow(legal_buy_value, legal_sell_value),
    }


def _normalize_side(row: Dict[str, Any], suffix: str, option_type: str) -> Optional[Dict[str, Any]]:
    ins_code = _to_int(row.get(f"insCode_{suffix}"))
    if not ins_code:
        return None

    underlying_price = _first_present_number(
        row.get("pDrCotVal_UA"),
        row.get("pClosing_UA"),
    )
    strike_price = _to_float(row.get("strikePrice"))
    open_positions = _to_float(row.get(f"oP_{suffix}"))
    yesterday_open_positions = _to_float(row.get(f"yesterdayOP_{suffix}"))

    return {
        "ins_code": ins_code,
        "instrument_id": str(ins_code),
        "option_type": option_type,
        "symbol": row.get(f"lVal18AFC_{suffix}"),
        "short_name": row.get(f"lVal18AFC_{suffix}"),
        "long_name": row.get(f"lVal30_{suffix}"),
        "buy_open_positions": open_positions,
        "sell_open_positions": None,
        "yesterday_open_positions": yesterday_open_positions,
        "contract_size": _to_float(row.get("contractSize")),
        "strike_price": strike_price,
        "underlying_ins_code": _to_int(row.get("uaInsCode")),
        "underlying_symbol": row.get("lval30_UA"),
        "underlying_short_name": row.get("lval30_UA"),
        "underlying_last_price": _to_float(row.get("pDrCotVal_UA")),
        "underlying_closing_price": _to_float(row.get("pClosing_UA")),
        "moneyness": compute_moneyness(option_type, strike_price, underlying_price),
        "intrinsic_value": compute_intrinsic_value(option_type, strike_price, underlying_price),
        "begin_date": _to_int(row.get("beginDate")),
        "end_date": _to_int(row.get("endDate")),
        "last_price": _to_float(row.get(f"pDrCotVal_{suffix}")),
        "closing_price": _to_float(row.get(f"pClosing_{suffix}")),
        "price_change": _price_change(row, suffix),
        "trade_volume": _to_float(row.get(f"qTotTran5J_{suffix}")),
        "trade_value": _to_float(row.get(f"qTotCap_{suffix}")),
        "trade_count": _to_int(row.get(f"zTotTran_{suffix}")),
        "instrument_meta": row,
    }


def _merge_public_instrument_info(
    contract: Dict[str, Any],
    instrument_info: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not instrument_info:
        return contract

    enriched = dict(contract)
    symbol = _first_text(
        instrument_info.get("lVal18AFC"),
        instrument_info.get("LVal18AFC"),
        instrument_info.get("lVal18"),
        instrument_info.get("LVal18"),
        instrument_info.get("cValMne"),
        instrument_info.get("CValMne"),
    )
    long_name = _first_text(
        instrument_info.get("lVal30"),
        instrument_info.get("LVal30"),
    )
    if symbol:
        enriched["symbol"] = symbol
        enriched["short_name"] = symbol
    if long_name:
        enriched["long_name"] = long_name
        parsed = _parse_option_long_name(long_name)
        if parsed.get("strike_price") is not None:
            enriched["strike_price"] = parsed["strike_price"]
            underlying_price = _first_present_number(
                enriched.get("underlying_last_price"),
                enriched.get("underlying_closing_price"),
            )
            enriched["moneyness"] = compute_moneyness(
                enriched.get("option_type"),
                enriched.get("strike_price"),
                underlying_price,
            )
            enriched["intrinsic_value"] = compute_intrinsic_value(
                enriched.get("option_type"),
                enriched.get("strike_price"),
                underlying_price,
            )
        if parsed.get("end_date") is not None:
            enriched["end_date"] = parsed["end_date"]

    enriched["instrument_id"] = _first_text(
        instrument_info.get("instrumentID"),
        instrument_info.get("InstrumentID"),
        enriched.get("instrument_id"),
    )
    enriched["isin"] = _first_text(
        instrument_info.get("cIsin"),
        instrument_info.get("CIsin"),
        enriched.get("isin"),
    ) or None
    enriched["instrument_meta"] = {
        **(contract.get("instrument_meta") if isinstance(contract.get("instrument_meta"), dict) else {}),
        "instrumentInfo": instrument_info,
    }
    return enriched


def _parse_option_long_name(value: str) -> Dict[str, Any]:
    text = _clean_numeric_text(value)
    match = re.search(r"-(\d+)-(\d{4}/?\d{2}/?\d{2}|\d{6})", text)
    if not match:
        return {}
    return {
        "strike_price": _to_float(match.group(1)),
        "end_date": _jalali_expiry_to_gregorian_int(match.group(2)),
    }


def _jalali_expiry_to_gregorian_int(value: str) -> Optional[int]:
    value = _translate_digits(value.strip())
    parts = value.split("/")
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
    if not _valid_jalali_month_day(year, month, day):
        return None
    g_year, g_month, g_day = _jalali_to_gregorian(year, month, day)
    return g_year * 10000 + g_month * 100 + g_day


def _valid_jalali_month_day(year: int, month: int, day: int) -> bool:
    if month < 1 or month > 12 or day < 1:
        return False
    if month <= 6:
        return day <= 31
    if month <= 11:
        return day <= 30
    return day <= (30 if _is_jalali_leap_year(year) else 29)


def _is_jalali_leap_year(year: int) -> bool:
    breaks = [
        -61,
        9,
        38,
        199,
        426,
        686,
        756,
        818,
        1111,
        1181,
        1210,
        1635,
        2060,
        2097,
        2192,
        2262,
        2324,
        2394,
        2456,
        3178,
    ]
    leap_j = -14
    jp = breaks[0]
    jump = 0
    for jm in breaks[1:]:
        jump = jm - jp
        if year < jm:
            break
        leap_j += (jump // 33) * 8 + (jump % 33) // 4
        jp = jm
    n = year - jp
    leap_j += (n // 33) * 8 + ((n % 33) + 3) // 4
    if jump % 33 == 4 and jump - n == 4:
        leap_j += 1
    if jump - n < 6:
        n = n - jump + ((jump + 4) // 33) * 33
    leap = ((n + 1) % 33 - 1) % 4
    if leap == -1:
        leap = 4
    return leap == 0


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


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none"}:
            return text
    return None


def _price_change(row: Dict[str, Any], suffix: str) -> Optional[str]:
    last = _to_float(row.get(f"pDrCotVal_{suffix}"))
    yesterday = _to_float(row.get(f"priceYesterday_{suffix}"))
    if last is None or yesterday is None:
        return None
    change = last - yesterday
    return f"{change:+.0f}"


def _first_present_number(*values: Any) -> Optional[float]:
    for value in values:
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _first_int(*values: Any) -> Optional[int]:
    for value in values:
        number = _to_int(value)
        if number is not None:
            return number
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        value = _clean_numeric_text(value)
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _to_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        number = int(value) if isfinite(value) and value.is_integer() else None
        return number if number is not None and number >= 0 else None
    if isinstance(value, str):
        value = _clean_numeric_text(value)
        if not value:
            return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _clean_numeric_text(value: str) -> str:
    text = (
        value.strip()
        .replace(",", "")
        .replace("٬", "")
        .replace("،", "")
        .replace(" ", "")
    )
    return _translate_digits(text)


def _translate_digits(value: str) -> str:
    return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def _net_flow(buy_value: Optional[float], sell_value: Optional[float]) -> Optional[float]:
    if buy_value is None and sell_value is None:
        return None
    return (buy_value or 0.0) - (sell_value or 0.0)


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = TSETMC_TRUST_ENV_PROXY
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://tsetmc.com",
            "Referer": "https://tsetmc.com/",
            "User-Agent": (
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:153.0) "
                "Gecko/20100101 Firefox/153.0"
            ),
        }
    )
    return session
