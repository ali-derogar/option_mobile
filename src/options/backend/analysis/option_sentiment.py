"""Strike distributions and premium turnover, independent of client-type scoring."""

from __future__ import annotations

from math import isfinite
from datetime import datetime
from typing import Any

from options.backend.analysis.sentiment import current_open_interest, detect_option_type
from options.backend.analysis.numeric import finite_sum


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
    text = str(value).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).replace(",", "").replace("٬", "").replace("،", "")
    if text.endswith(".0"):
        text = text[:-2]
    return (text.lstrip("0") or "0") if text.isascii() and text.isdigit() else ""


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if text.lower() in {"nan", "none", "nat", "<na>"}:
        return ""
    return text.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")


def underlying_key(row: dict) -> str:
    code = identity(row.get("underlying_ins_code"))
    return code if code and code != "0" else (normalize_text(row.get("underlying_symbol")) or normalize_text(row.get("underlying_short_name")))


def _resolve_underlying_keys(rows: list[dict]) -> None:
    aliases: dict[str, set[str]] = {}
    for row in rows:
        code = identity(row.get("underlying_ins_code"))
        if code and code != "0":
            for field in ("underlying_symbol", "underlying_short_name"):
                alias = normalize_text(row.get(field))
                if alias:
                    aliases.setdefault(alias, set()).add(code)
    for row in rows:
        key = underlying_key(row)
        if not identity(row.get("underlying_ins_code")) or identity(row.get("underlying_ins_code")) == "0":
            candidates = set().union(*(aliases.get(normalize_text(row.get(field)), set())
                for field in ("underlying_symbol", "underlying_short_name")))
            if len(candidates) == 1:
                key = next(iter(candidates))
        row["_underlying_key"] = key


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
    known = [v for v in values if v is not None and isfinite(v)]
    total = finite_sum(known) if known or not values else None
    overflow = bool(known) and total is None
    return {"value": None if overflow else total,
        "known": len(known), "total": len(values), "complete": len(known) == len(values) and not overflow,
        **({"overflow": True} if overflow else {})}


def trade_summary(rows: list[dict]) -> dict:
    def turnover(row):
        value = number(row.get("trade_value"))
        return value if value is not None and value >= 0 else None

    calls = metric([turnover(r) for r in rows if r["option_type"] == "call"])
    puts = metric([turnover(r) for r in rows if r["option_type"] == "put"])
    total = metric([turnover(r) for r in rows])
    unknown_count = sum(r["option_type"] not in {"call", "put"} for r in rows)
    if unknown_count:
        for info in (calls, puts):
            info["total"] += unknown_count
            info["complete"] = False
            if not info["known"]:
                info["value"] = None
    ratio = None
    ratio_state = "unavailable"
    if calls["complete"] and puts["complete"]:
        if puts["value"] > 0:
            quotient = calls["value"] / puts["value"]
            if isfinite(quotient):
                ratio, ratio_state = quotient, "finite"
        elif calls["value"] > 0:
            ratio_state = "infinite"
    return {"call": calls, "put": puts, "total": total, "call_put_ratio": ratio, "ratio_state": ratio_state}


def contract_scope(contracts: list[dict], selected_key: str | None = None, expiry: str = "all") -> dict:
    by_code = {}
    for row in contracts:
        code = identity(row.get("ins_code"))
        if code and code != "0":
            by_code[code] = {**row, "ins_code": code, "option_type": detect_option_type(row)}
    all_rows = list(by_code.values())
    _resolve_underlying_keys(all_rows)
    assets = {}
    for row in all_rows:
        key = row["_underlying_key"]
        if key:
            symbol = normalize_text(row.get("underlying_symbol")) or normalize_text(row.get("underlying_short_name"))
            if key not in assets or symbol:
                assets[key] = {"key": key, "symbol": symbol or key}
    assets = sorted(assets.values(), key=lambda a: a["symbol"])
    selected_key = (identity(selected_key) or normalize_text(selected_key)) if selected_key else (assets[0]["key"] if assets else "")
    symbol_matches = [a["key"] for a in assets if normalize_text(a["symbol"]) == selected_key]
    if len(symbol_matches) == 1:
        selected_key = symbol_matches[0]
    asset_rows = [r for r in all_rows if r["_underlying_key"] == selected_key]
    expiries = sorted({contract_expiry(r) for r in asset_rows if contract_expiry(r)})
    expiry = expiry if expiry in expiries else "all"
    selected_all = [r for r in asset_rows if expiry == "all" or identity(r.get("end_date")) == expiry]
    return {"market": all_rows, "underlying": asset_rows, "selection": selected_all,
            "underlying_key": selected_key, "expiry": expiry, "underlyings": assets, "expiries": expiries}


def activity_matrix(rows: list[dict]) -> dict:
    groups = {}
    for row in rows:
        strike, expiry = number(row.get("strike_price")), contract_expiry(row)
        if strike is not None and strike > 0 and expiry:
            groups.setdefault((strike, expiry), []).append(row)
    cells = []
    for (strike, expiry), group in sorted(groups.items()):
        summary = trade_summary(group)
        cells.append({"strike_price": strike, "expiry": expiry,
                      **{name: summary[name] for name in ("call", "put", "total")}})
    return {"strikes": sorted({strike for strike, _ in groups}),
            "expiries": sorted({expiry for _, expiry in groups}), "cells": cells}


def analyze_participant_flow(rows: list[dict], participants: list[dict], *, known_zero_codes: set[str] | None = None) -> dict:
    by_code = {identity(r.get("ins_code")): r for r in participants}
    groups = {}
    for row in rows:
        strike = number(row.get("strike_price"))
        if row["option_type"] in {"call", "put"} and strike is not None and strike > 0:
            groups.setdefault(strike, []).append(row)

    def known_zero(row):
        return ((known_zero_codes is None or identity(row.get("ins_code")) in known_zero_codes)
                and number(row.get("trade_volume")) == 0 and number(row.get("trade_value")) == 0)

    def net(row, audience, measure):
        participant = by_code.get(identity(row.get("ins_code")), {})
        buy, sell = (number(participant.get(f"{audience}_{side}_{measure}")) for side in ("buy", "sell"))
        if buy is not None and sell is not None and buy >= 0 and sell >= 0:
            return finite_sum([buy, -sell])
        # A confirmed non-trading contract has zero flow; missing trade data
        # or a zero price alone cannot establish that no trade occurred.
        if known_zero(row):
            return 0.0
        return None

    def complete_metric(values):
        info = metric(values)
        if not info["complete"]:
            info["value"] = None
        return info

    traded_rows = [r for group in groups.values() for r in group if not known_zero(r)]
    audiences = {}
    for audience in ("natural", "legal"):
        audiences[audience] = {}
        for measure in ("value", "volume"):
            strikes = []
            for strike, group in sorted(groups.items()):
                item = {"strike_price": strike, "coverage": {}}
                for side in ("call", "put"):
                    info = complete_metric([net(r, audience, measure) for r in group if r["option_type"] == side])
                    item[f"{side}_flow"] = info["value"]
                    item["coverage"][f"{side}_flow"] = info
                strikes.append(item)
            audiences[audience][measure] = {"strikes": strikes, "coverage": complete_metric([
                net(r, audience, measure) for group in groups.values() for r in group]),
                "traded_coverage": complete_metric([net(r, audience, measure) for r in traded_rows])}
    return {"audiences": audiences}


def analyze_strikes(contracts: list[dict], selected_key: str | None = None, expiry: str = "all") -> dict:
    scope = contract_scope(contracts, selected_key, expiry)
    all_rows, asset_rows, selected_all = (scope[k] for k in ("market", "underlying", "selection"))
    selected_key, expiry, assets, expiries = (scope[k] for k in ("underlying_key", "expiry", "underlyings", "expiries"))
    rows = [r for r in all_rows if r["option_type"] in {"call", "put"}]
    unknown_count = len(all_rows) - len(rows)
    selected = [r for r in selected_all if r["option_type"] in {"call", "put"}]
    unknown_selected = [r for r in selected_all if r["option_type"] not in {"call", "put"}]
    price, price_kind = None, None
    price_conflict = False
    for field in ("underlying_last_price", "underlying_closing_price"):
        values = [number(r.get(field)) for r in asset_rows if r["option_type"] in {"call", "put"}]
        prices = {v for v in values if v is not None and v >= 0}
        if len(prices) > 1:
            price_conflict = True
        elif prices:
            price = prices.pop()
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
        "activity": activity_matrix(selected_all),
        "summaries": {"market": trade_summary(all_rows), "underlying": trade_summary(asset_rows), "selection": trade_summary(selected_all)},
        "quality": {
            "contract_count": len(rows), "selected_contract_count": len(selected), "unknown_type_count": unknown_count,
            "spot_price_conflict": price_conflict,
            "invalid_strike_count": sum(number(r.get("strike_price")) is None or number(r.get("strike_price")) <= 0 for r in selected),
            "oi": metric([current_oi(r) for r in selected]), "change": metric([oi_change(r) for r in selected]),
        },
    }
