"""Pure selection rules shared by saved and incremental expiry snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from options.backend.analysis.arbitrage import estimate_call_cash_arbitrage
from options.backend.analysis.sentiment import (
    compute_intrinsic_value,
    compute_moneyness,
)

MARKET_TZ = ZoneInfo("Asia/Tehran")


@dataclass(frozen=True)
class ExpiryFilters:
    underlying_ins_code: int | None = None
    option_type: str | None = None
    min_days: int = 0
    max_days: int | None = None
    moneyness: str | None = None
    min_score: float | None = None
    min_volume: int | None = None
    arbitrage_only: bool = False


@dataclass(frozen=True)
class ExpirySnapshot:
    rows: list[dict[str, Any]]
    warning: str | None = None
    market_received_at: str | None = None


def valid_expiry(value: Any) -> bool:
    text = str(value)
    if len(text) != 8 or not text.isascii() or not text.isdigit() or int(text[:4]) < 1700:
        return False
    try:
        date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return False
    return True


def _number(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(number) or number < 0 or (positive and number == 0):
        return None
    return number


def _pricing_current(received_at: str | None, today: date) -> bool:
    if not received_at:
        return False
    try:
        received = datetime.fromisoformat(received_at)
        return received.tzinfo is not None and received.astimezone(MARKET_TZ).date() == today
    except (TypeError, ValueError):
        return False


def public_warning(warning: str | None) -> str | None:
    """Remove internal fallback diagnostics while keeping data-quality warnings."""
    return re.sub(
        r"مشخصات تکمیلی \d+ قرارداد دریافت نشد؛ (?:از )?اطلاعات معتبر خوراک بازار استفاده شد\.\s*",
        "", warning or "",
    ).strip() or None


def _matches(row: dict[str, Any], filters: ExpiryFilters) -> bool:
    return (
        (filters.underlying_ins_code is None or row["underlying_ins_code"] == str(filters.underlying_ins_code))
        and (filters.option_type is None or row["option_type"] == filters.option_type)
        and (not filters.arbitrage_only or row["is_arbitrage_candidate"])
        and row["days_remaining"] >= filters.min_days
        and (filters.max_days is None or row["days_remaining"] <= filters.max_days)
        and (filters.moneyness is None or row["moneyness"] == filters.moneyness)
        and (filters.min_score is None or (
            row["intrinsic_gap_percent"] is not None and row["intrinsic_gap_percent"] >= filters.min_score
        ))
        and (filters.min_volume is None or (
            row["trade_volume"] is not None and row["trade_volume"] >= filters.min_volume
        ))
    )


def select_expiries(
    snapshot: ExpirySnapshot,
    today: date,
    filters: ExpiryFilters | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Calculate, filter and rank the entire snapshot before applying the limit."""
    filters = filters or ExpiryFilters()
    pricing_current = _pricing_current(snapshot.market_received_at, today)
    cutoff = int(today.strftime("%Y%m%d"))
    by_code = {
        row["ins_code"]: row for row in snapshot.rows
        if row.get("ins_code", 0) > 0 and valid_expiry(row.get("end_date")) and int(row["end_date"]) >= cutoff
    }
    upcoming = []
    underlyings = {}
    for row in by_code.values():
        strike = _number(row.get("strike_price"), positive=True)
        underlying_price = _number(row.get("underlying_last_price"), positive=True)
        underlying_closing_price = _number(row.get("underlying_closing_price"), positive=True)
        ask = _number(row.get("ask_price"), positive=True)
        ask_volume = _number(row.get("ask_volume"))
        intrinsic = compute_intrinsic_value(row["option_type"], strike, underlying_price)
        price_min = _number(row.get("price_min"), positive=True)
        price_max = _number(row.get("price_max"), positive=True)
        range_current = str(row.get("price_range_date")) == str(cutoff)
        if not range_current or price_min is None or price_max is None or price_min > price_max:
            price_min = price_max = None
        # Android uses Python 3.10, whose fromisoformat rejects YYYYMMDD.
        days_remaining = (datetime.strptime(str(row["end_date"]), "%Y%m%d").date() - today).days
        estimate = estimate_call_cash_arbitrage(
            option_type=row["option_type"], days_remaining=days_remaining,
            pricing_current=pricing_current, strike_price=strike,
            underlying_closing_price=underlying_closing_price, ask_price=ask, ask_volume=ask_volume,
        )
        underlying_code = str(row["underlying_ins_code"]) if row.get("underlying_ins_code") else None
        if underlying_code:
            underlyings[underlying_code] = row.get("underlying_symbol") or underlying_code
        upcoming.append({
            **row,
            "ins_code": str(row["ins_code"]),
            "underlying_ins_code": underlying_code,
            "end_date": int(row["end_date"]),
            "strike_price": strike,
            "days_remaining": days_remaining,
            "ask_price": ask,
            "ask_volume": ask_volume,
            "underlying_last_price": underlying_price,
            "underlying_closing_price": underlying_closing_price,
            "trade_volume": _number(row.get("trade_volume")),
            "price_min": price_min,
            "price_max": price_max,
            "intrinsic_value": intrinsic,
            "moneyness": compute_moneyness(row["option_type"], strike, underlying_price),
            # Keep the public score/filter name for existing API consumers.
            "intrinsic_gap_percent": estimate.return_percent,
            "estimated_cash_value_per_unit": estimate.estimated_cash_value_per_unit,
            "gross_profit_per_unit": estimate.gross_profit_per_unit,
            "is_arbitrage_candidate": estimate.is_arbitrage_candidate,
        })

    def sort_key(row: dict[str, Any]) -> tuple[int, bool, float, int]:
        score = row["intrinsic_gap_percent"] if row["days_remaining"] == 0 else None
        return row["end_date"], score is None, -score if score is not None else 0, int(row["ins_code"])

    matching = sorted((row for row in upcoming if _matches(row, filters)), key=sort_key)
    return {
        "items": matching[:limit],
        "total": len(matching),
        "total_upcoming": len(upcoming),
        "nearest_days": min((row["days_remaining"] for row in upcoming), default=None),
        "as_of_date": today.isoformat(),
        "market_received_at": snapshot.market_received_at,
        "pricing_current": pricing_current,
        "underlyings": [
            {"ins_code": code, "symbol": symbol}
            for code, symbol in sorted(underlyings.items(), key=lambda item: (item[1], int(item[0])))
        ],
        "warning": public_warning(snapshot.warning),
    }
