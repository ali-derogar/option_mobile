"""Strike distributions and premium turnover, independent of client-type scoring."""

from __future__ import annotations

from math import isfinite
from datetime import datetime
from typing import Any

from options.backend.analysis.sentiment import current_open_interest, detect_option_type


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).replace(",", "").replace("٬", "").replace("٫", "."))
    except (ValueError, TypeError, OverflowError):
        return None
    return result if isfinite(result) else None


def identity(value: Any) -> str:
    """Do not round TSETMC's 17-digit identifiers through float conversion."""
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if text.endswith(".0"):
        text = text[:-2]
    return str(int(text)) if text.isdigit() else ""


def normalize_text(value: Any) -> str:
    return str(value or "").strip().replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")


def underlying_key(row: dict) -> str:
    code = identity(row.get("underlying_ins_code"))
    return code if code and code != "0" else normalize_text(row.get("underlying_symbol") or row.get("underlying_short_name"))


def contract_expiry(row: dict) -> str | None:
    value = identity(row.get("end_date"))
    if len(value) != 8:
        return None
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None
    return value


def current_oi(row: dict) -> float | None:
    return current_open_interest(row)


def oi_change(row: dict) -> float | None:
    current = current_oi(row)
    previous = number(row.get("yesterday_open_positions"))
    return current - previous if current is not None and previous is not None and previous >= 0 else None


def metric(values: list[float | None]) -> dict:
    known = [v for v in values if v is not None]
    return {
        "value": sum(known) if known or not values else None,
        "known": len(known), "total": len(values), "complete": len(known) == len(values),
    }


def trade_summary(rows: list[dict], unknown_count: int = 0) -> dict:
    def turnover(row):
        value = number(row.get("trade_value"))
        return value if value is not None and value >= 0 else None

    calls = metric([turnover(r) for r in rows if r["option_type"] == "call"])
    puts = metric([turnover(r) for r in rows if r["option_type"] == "put"])
    total = metric([turnover(r) for r in rows])
    if unknown_count:
        for info in (calls, puts, total):
            info["total"] += unknown_count
            info["complete"] = False
            if not info["known"]:
                info["value"] = None
    ratio = None
    ratio_state = "unavailable"
    if calls["complete"] and puts["complete"]:
        if puts["value"] > 0:
            ratio, ratio_state = calls["value"] / puts["value"], "finite"
        elif calls["value"] > 0:
            ratio_state = "infinite"
    return {"call": calls, "put": puts, "total": total, "call_put_ratio": ratio, "ratio_state": ratio_state}


def analyze_strikes(contracts: list[dict], selected_key: str | None = None, expiry: str = "all") -> dict:
    by_code = {}
    for row in contracts:
        code = identity(row.get("ins_code"))
        if code and code != "0":
            by_code[code] = {**row, "ins_code": code, "option_type": detect_option_type(row)}
    unknown_count = sum(r["option_type"] not in {"call", "put"} for r in by_code.values())
    rows = [r for r in by_code.values() if r["option_type"] in {"call", "put"}]
    assets = {}
    for row in rows:
        key = underlying_key(row)
        if key:
            assets[key] = {"key": key, "symbol": row.get("underlying_symbol") or row.get("underlying_short_name") or key}
    assets = sorted(assets.values(), key=lambda a: a["symbol"])
    selected_key = normalize_text(selected_key) if selected_key else (assets[0]["key"] if assets else "")
    selected_key = next((a["key"] for a in assets if normalize_text(a["symbol"]) == selected_key), selected_key)
    asset_rows = [r for r in rows if underlying_key(r) == selected_key]
    expiries = sorted({contract_expiry(r) for r in asset_rows if contract_expiry(r)})
    expiry = expiry if expiry in expiries else "all"
    selected = [r for r in asset_rows if expiry == "all" or identity(r.get("end_date")) == expiry]
    unknown_asset = [r for r in by_code.values() if r["option_type"] not in {"call", "put"} and underlying_key(r) == selected_key]
    unknown_selected = [r for r in unknown_asset if expiry == "all" or identity(r.get("end_date")) == expiry]
    price, price_kind = None, None
    for field in ("underlying_last_price", "underlying_closing_price"):
        values = [number(r.get(field)) for r in asset_rows]
        price = next((v for v in values if v is not None and v >= 0), None)
        if price is not None:
            price_kind = "last" if field == "underlying_last_price" else "closing"
            break
    groups = {}
    for row in selected:
        strike = number(row.get("strike_price"))
        if strike is not None and strike > 0:
            groups.setdefault(strike, []).append(row)
    strikes = []
    for strike, group in sorted(groups.items()):
        item = {"strike_price": strike, "coverage": {}}
        for side in ("call", "put"):
            side_rows = [r for r in group if r["option_type"] == side]
            for name, fn in (("oi", current_oi), ("change", oi_change)):
                key = f"{side}_{name}"
                info = metric([fn(r) for r in side_rows])
                item[key], item["coverage"][key] = info["value"], info
        strikes.append(item)
    atm = min(groups, key=lambda k: (abs(k - price), k)) if groups and price is not None else None
    peaks = {}
    for side in ("call", "put"):
        available = [r for r in strikes if r[f"{side}_oi"] is not None and r[f"{side}_oi"] > 0]
        peak = min(available, key=lambda r: (-r[f"{side}_oi"], r["strike_price"])) if available else None
        peaks[side] = {"strike_price": peak["strike_price"], **peak["coverage"][f"{side}_oi"]} if peak else None
        if peak:
            peaks[side]["scope_complete"] = not unknown_selected and all(
                current_oi(row) is not None and (number(row.get("strike_price")) or 0) > 0
                for row in selected if row["option_type"] == side
            )
    return {
        "underlying_key": selected_key, "expiry": expiry,
        "underlyings": assets, "expiries": expiries,
        "spot_price": price, "spot_price_kind": price_kind, "atm_strike": atm,
        "strikes": strikes, "peaks": peaks,
        "summaries": {"market": trade_summary(rows, unknown_count), "underlying": trade_summary(asset_rows, len(unknown_asset)), "selection": trade_summary(selected, len(unknown_selected))},
        "quality": {
            "contract_count": len(rows), "selected_contract_count": len(selected), "unknown_type_count": unknown_count,
            "invalid_strike_count": sum(number(r.get("strike_price")) is None or number(r.get("strike_price")) <= 0 for r in selected),
            "oi": metric([current_oi(r) for r in selected]), "change": metric([oi_change(r) for r in selected]),
        },
    }
