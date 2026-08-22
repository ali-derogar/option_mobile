"""Trade data service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from darush.backend.client import TsetmcClient
from darush.backend.config import TSETMC_FLOW


def fetch_trade_last_day(
    client: TsetmcClient,
    flow: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch last day trade data for a market flow."""
    flow = flow if flow is not None else TSETMC_FLOW
    data = client.call("trade_last_day", {"Flow": flow})
    if not isinstance(data, list):
        return []
    return data


def normalize_trade(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ins_code": int(row.get("InsCode", 0) or 0),
        "trade_date": int(row.get("DEven", 0) or 0),
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
    return [r for r in rows if int(r.get("InsCode", 0) or 0) in ins_codes]


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
