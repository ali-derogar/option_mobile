"""Option (derivative) data service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from darush.backend.client import TsetmcClient
from darush.backend.analysis.sentiment import compute_intrinsic_value, compute_moneyness, detect_option_type


def fetch_all_options(client: TsetmcClient) -> List[Dict[str, Any]]:
    """Fetch all option contracts with open position data."""
    data = client.call("option")
    if not isinstance(data, list):
        return []
    return data


def normalize_option(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize option record with consistent field names."""
    return {
        "ins_code": int(row.get("InsCode", 0)),
        "instrument_id": row.get("InstrumentID"),
        "buy_open_positions": _to_float(row.get("BuyOP")),
        "sell_open_positions": _to_float(row.get("SellOP")),
        "yesterday_open_positions": _to_float(row.get("YesterdayOP")),
        "contract_size": _to_float(row.get("ContractSize")),
        "strike_price": _to_float(row.get("StrikePrice")),
        "underlying_ins_code": int(row.get("UAInsCode", 0) or 0),
        "begin_date": int(row.get("BeginDate", 0) or 0),
        "end_date": int(row.get("EndDate", 0) or 0),
        "a_factor": _to_float(row.get("AFactor")),
        "b_factor": _to_float(row.get("BFactor")),
        "c_factor": _to_float(row.get("CFactor")),
        "raw": row,
    }


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    underlying_price = enriched.get("underlying_last_price") or enriched.get("underlying_closing_price")
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
