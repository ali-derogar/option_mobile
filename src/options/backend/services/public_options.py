"""Public TSETMC option market watch service.

This endpoint is part of TSETMC's public CDN API and does not require the paid
REST API login. It is a useful fallback when account credentials are invalid or
the authenticated API is unavailable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests

from options.backend.analysis.sentiment import compute_intrinsic_value, compute_moneyness
from options.backend.config import (
    TSETMC_PUBLIC_CLIENT_TYPE_WORKERS,
    TSETMC_REQUEST_TIMEOUT,
    TSETMC_TRUST_ENV_PROXY,
)

OPTION_MARKET_WATCH_URL = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentOptionMarketWatch/1"
CLIENT_TYPE_HISTORY_URL = "https://cdn.tsetmc.com/api/ClientType/GetClientTypeHistory/{ins_code}"


def fetch_public_option_market_watch() -> List[Dict[str, Any]]:
    session = _session()
    response = session.get(OPTION_MARKET_WATCH_URL, timeout=TSETMC_REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("instrumentOptMarketWatch", payload)
    return rows if isinstance(rows, list) else []


def normalize_public_option_pairs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    contracts: List[Dict[str, Any]] = []
    for row in rows:
        call = _normalize_side(row, "C", "call")
        put = _normalize_side(row, "P", "put")
        if call:
            contracts.append(call)
        if put:
            contracts.append(put)
    return contracts


def fetch_public_client_type_latest(ins_code: int) -> Optional[Dict[str, Any]]:
    session = _session()
    url = CLIENT_TYPE_HISTORY_URL.format(ins_code=ins_code)
    response = session.get(url, timeout=TSETMC_REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("clientType", [])
    if not isinstance(rows, list) or not rows:
        return None
    latest = max(rows, key=lambda row: _to_int(row.get("recDate")) or 0)
    return normalize_public_client_type(latest)


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
            except requests.RequestException:
                continue
    return rows


def normalize_public_client_type(row: Dict[str, Any]) -> Dict[str, Any]:
    natural_buy_value = _to_float(row.get("buy_I_Value"))
    natural_sell_value = _to_float(row.get("sell_I_Value"))
    legal_buy_value = _to_float(row.get("buy_N_Value"))
    legal_sell_value = _to_float(row.get("sell_N_Value"))
    return {
        "rec_date": _to_int(row.get("recDate")),
        "ins_code": _to_int(row.get("insCode")) or 0,
        "natural_buy_volume": _to_float(row.get("buy_I_Volume")),
        "natural_buy_value": natural_buy_value,
        "natural_buy_count": _to_int(row.get("buy_I_Count")),
        "natural_sell_volume": _to_float(row.get("sell_I_Volume")),
        "natural_sell_value": natural_sell_value,
        "natural_sell_count": _to_int(row.get("sell_I_Count")),
        "legal_buy_volume": _to_float(row.get("buy_N_Volume")),
        "legal_buy_value": legal_buy_value,
        "legal_buy_count": _to_int(row.get("buy_N_Count")),
        "legal_sell_volume": _to_float(row.get("sell_N_Volume")),
        "legal_sell_value": legal_sell_value,
        "legal_sell_count": _to_int(row.get("sell_N_Count")),
        "natural_money_flow": _net_flow(natural_buy_value, natural_sell_value),
        "legal_money_flow": _net_flow(legal_buy_value, legal_sell_value),
    }


def _normalize_side(row: Dict[str, Any], suffix: str, option_type: str) -> Optional[Dict[str, Any]]:
    ins_code = _to_int(row.get(f"insCode_{suffix}"))
    if not ins_code:
        return None

    underlying_price = _to_float(row.get("pDrCotVal_UA")) or _to_float(row.get("pClosing_UA"))
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


def _price_change(row: Dict[str, Any], suffix: str) -> Optional[str]:
    last = _to_float(row.get(f"pDrCotVal_{suffix}"))
    yesterday = _to_float(row.get(f"priceYesterday_{suffix}"))
    if last is None or yesterday is None:
        return None
    change = last - yesterday
    return f"{change:+.0f}"


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
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
    )
    return session
