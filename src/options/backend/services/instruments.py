"""Instrument list service."""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, List, Optional

from options.backend.client import TsetmcClient
from options.backend.config import TSETMC_FLOW


def fetch_instruments(
    client: TsetmcClient,
    flow: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch instrument list for a market flow (default: derivatives/ATI)."""
    flow = flow if flow is not None else TSETMC_FLOW
    data = client.call("instrument", {"Flow": flow})
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def index_by_ins_code(instruments: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Build lookup dict keyed by InsCode."""
    index: Dict[int, Dict[str, Any]] = {}
    for row in instruments:
        if not isinstance(row, dict):
            continue
        ins_code = _to_int(row.get("InsCode"))
        if ins_code is not None:
            index[ins_code] = row
    return index


def filter_by_ins_codes(
    instruments: List[Dict[str, Any]],
    ins_codes: set[int],
) -> List[Dict[str, Any]]:
    return [
        row
        for row in instruments
        if isinstance(row, dict) and (_to_int(row.get("InsCode")) or 0) in ins_codes
    ]


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
