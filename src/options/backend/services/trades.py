"""Trade data service."""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, List, Optional

from options.backend.client import TsetmcClient
from options.backend.config import TSETMC_FLOW


def fetch_trade_last_day(
    client: TsetmcClient,
    flow: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch last day trade data for a market flow."""
    flow = flow if flow is not None else TSETMC_FLOW
    data = client.call("trade_last_day", {"Flow": flow})
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def normalize_trade(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ins_code": _to_int(row.get("InsCode")) or 0,
        "trade_date": _to_int(row.get("DEven")) or 0,
        "symbol": row.get("LVal18AFC"),
        "long_name": row.get("LVal30"),
        "trade_count": _to_int(row.get("ZTotTran")),
        "volume": _to_float(row.get("QTotTran5J")),
        "value": _to_float(row.get("QTotCap")),
        "closing_price": _to_float(row.get("PClosing")),
        "last_price": _to_float(row.get("PDrCotVal")),
        "price_change": row.get("PriceChange"),
        "price_min": _to_float(row.get("PriceMin")),
        "price_max": _to_float(row.get("PriceMax")),
        "price_first": _to_float(row.get("PriceFirst")),
        "price_yesterday": _to_float(row.get("PriceYesterday")),
        "raw": row,
    }


def filter_for_ins_codes(
    rows: List[Dict[str, Any]],
    ins_codes: set[int],
) -> List[Dict[str, Any]]:
    return [
        row
        for row in rows
        if isinstance(row, dict) and (_to_int(row.get("InsCode")) or 0) in ins_codes
    ]


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
    return (
        value.strip()
        .replace(",", "")
        .replace("٬", "")
        .replace("،", "")
        .replace(" ", "")
    )
