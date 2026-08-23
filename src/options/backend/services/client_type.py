"""Client type (real/legal) trading data service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from options.backend.client import TsetmcClient


def fetch_client_type_all(client: TsetmcClient) -> List[Dict[str, Any]]:
    """Fetch client type data for all instruments."""
    data = client.call("client_type_all")
    if not isinstance(data, list):
        return []
    return data


def fetch_client_type_by_ins(
    client: TsetmcClient,
    ins_code: int,
) -> List[Dict[str, Any]]:
    """Fetch client type data for a single instrument (includes value fields)."""
    data = client.call("client_type_by_ins", {"Inscode": ins_code})
    if isinstance(data, list):
        return data
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
        "rec_date": int(row.get("RecDate", 0) or 0),
        "ins_code": int(row.get("InsCode", 0) or 0),
        # حقیقی (natural / N)
        "natural_buy_volume": _to_float(row.get("Buy_N_Volume")),
        "natural_buy_value": buy_n_value,
        "natural_buy_count": _to_int(row.get("Buy_Count_ClientN")),
        "natural_sell_volume": _to_float(row.get("Sell_N_Volume")),
        "natural_sell_value": sell_n_value,
        "natural_sell_count": _to_int(row.get("Sell_Count_ClientN")),
        # حقوقی (legal / I)
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


def _net_flow(buy_value: Optional[float], sell_value: Optional[float]) -> Optional[float]:
    if buy_value is None and sell_value is None:
        return None
    return (buy_value or 0.0) - (sell_value or 0.0)
