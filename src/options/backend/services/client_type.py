"""Client type (real/legal) trading data service."""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, List, Optional

from options.backend.client import TsetmcClient


def fetch_client_type_all(client: TsetmcClient) -> List[Dict[str, Any]]:
    """Fetch client type data for all instruments."""
    data = client.call("client_type_all")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def fetch_client_type_by_ins(
    client: TsetmcClient,
    ins_code: int,
) -> List[Dict[str, Any]]:
    """Fetch client type data for a single instrument (includes value fields)."""
    data = client.call("client_type_by_ins", {"Inscode": ins_code})
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def normalize_client_type(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize client type record with Persian labels in field names."""
    buy_n_value = _to_float(row.get("Buy_N_Value"))
    buy_i_value = _to_float(row.get("Buy_I_Value"))
    sell_n_value = _to_float(row.get("Sell_N_Value"))
    sell_i_value = _to_float(row.get("Sell_I_Value"))

    return {
        "rec_date": _to_int(row.get("RecDate")) or 0,
        "ins_code": _to_int(row.get("InsCode")) or 0,
        # Authenticated API uses ClientN/ClientI: N = natural, I = legal.
        "natural_buy_volume": _to_float(row.get("Buy_N_Volume")),
        "natural_buy_value": buy_n_value,
        "natural_buy_count": _to_int(row.get("Buy_Count_ClientN")),
        "natural_sell_volume": _to_float(row.get("Sell_N_Volume")),
        "natural_sell_value": sell_n_value,
        "natural_sell_count": _to_int(row.get("Sell_Count_ClientN")),
        "legal_buy_volume": _to_float(row.get("Buy_I_Volume")),
        "legal_buy_value": buy_i_value,
        "legal_buy_count": _to_int(row.get("Buy_Count_ClientI")),
        "legal_sell_volume": _to_float(row.get("Sell_I_Volume")),
        "legal_sell_value": sell_i_value,
        "legal_sell_count": _to_int(row.get("Sell_Count_ClientI")),
        # money flow (ورود/خروج پول)
        "natural_money_flow": _net_flow(buy_n_value, sell_n_value),
        "legal_money_flow": _net_flow(buy_i_value, sell_i_value),
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


def _net_flow(buy_value: Optional[float], sell_value: Optional[float]) -> Optional[float]:
    if buy_value is None and sell_value is None:
        return None
    return (buy_value or 0.0) - (sell_value or 0.0)
