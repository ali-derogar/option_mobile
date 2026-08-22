"""Instrument list service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from darush.backend.client import TsetmcClient
from darush.backend.config import TSETMC_FLOW


def fetch_instruments(
    client: TsetmcClient,
    flow: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch instrument list for a market flow (default: derivatives/ATI)."""
    flow = flow if flow is not None else TSETMC_FLOW
    data = client.call("instrument", {"Flow": flow})
    if not isinstance(data, list):
        return []
    return data


def index_by_ins_code(instruments: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Build lookup dict keyed by InsCode."""
    index: Dict[int, Dict[str, Any]] = {}
    for row in instruments:
        ins_code = row.get("InsCode")
        if ins_code is not None:
            index[int(ins_code)] = row
    return index


def filter_by_ins_codes(
    instruments: List[Dict[str, Any]],
    ins_codes: set[int],
) -> List[Dict[str, Any]]:
    return [row for row in instruments if int(row.get("InsCode", 0)) in ins_codes]
