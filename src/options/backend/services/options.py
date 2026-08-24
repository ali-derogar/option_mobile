"""Option (derivative) data service."""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, List, Optional

from options.backend.client import TsetmcClient
from options.backend.analysis.sentiment import compute_intrinsic_value, compute_moneyness, detect_option_type


def fetch_all_options(client: TsetmcClient) -> List[Dict[str, Any]]:
    """Fetch all option contracts with open position data."""
    data = client.call("option")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def normalize_option(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize option record with consistent field names."""
    return {
        "ins_code": _to_int(row.get("InsCode")) or 0,
        "instrument_id": row.get("InstrumentID"),
        "buy_open_positions": _to_float(row.get("BuyOP")),
        "sell_open_positions": _to_float(row.get("SellOP")),
        "yesterday_open_positions": _to_float(row.get("YesterdayOP")),
        "contract_size": _to_float(row.get("ContractSize")),
        "strike_price": _to_float(row.get("StrikePrice")),
        "underlying_ins_code": _to_int(row.get("UAInsCode")) or 0,
        "begin_date": _to_int(row.get("BeginDate")) or 0,
        "end_date": _to_int(row.get("EndDate")) or 0,
        "a_factor": _to_float(row.get("AFactor")),
        "b_factor": _to_float(row.get("BFactor")),
        "c_factor": _to_float(row.get("CFactor")),
        "raw": row,
    }


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


def enrich_with_instrument(
    option: Dict[str, Any],
    instrument: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Merge instrument metadata into normalized option."""
    enriched = dict(option)
    if instrument:
        enriched.update(
            {
                "symbol": instrument.get("CValMne") or instrument.get("LVal18"),
                "short_name": instrument.get("LVal18"),
                "long_name": instrument.get("LVal30") or instrument.get("LVal18AFC"),
                "isin": instrument.get("CIsin"),
                "market_name": instrument.get("YMarNSC") or instrument.get("LSoc30"),
                "sector": instrument.get("CGdSVal"),
                "instrument_meta": instrument,
            }
        )
    enriched["option_type"] = detect_option_type(enriched)
    return enriched


def enrich_with_underlying(
    option: Dict[str, Any],
    underlying_instrument: Dict[str, Any] | None,
    underlying_trade: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Attach underlying asset metadata and latest price to an option row."""
    enriched = dict(option)
    if underlying_instrument:
        enriched.update(
            {
                "underlying_symbol": underlying_instrument.get("CValMne")
                or underlying_instrument.get("LVal18"),
                "underlying_short_name": underlying_instrument.get("LVal18"),
            }
        )
    if underlying_trade:
        enriched.update(
            {
                "underlying_last_price": underlying_trade.get("last_price"),
                "underlying_closing_price": underlying_trade.get("closing_price"),
            }
        )

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
    return enriched


def _first_present_number(*values: Any) -> Optional[float]:
    for value in values:
        number = _to_float(value)
        if number is not None:
            return number
    return None
