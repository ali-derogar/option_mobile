"""API helpers for web frontend."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from numbers import Real
from typing import Any, Dict, List, Optional

import pandas as pd

from options.backend.analysis.sentiment import analyze_options_sentiment, current_open_interest, open_interest_snapshot
from options.backend.analysis.numeric import finite_ratio, finite_sum
from options.backend.storage import Storage

ID_FIELDS = {"ins_code", "underlying_ins_code", "underlying_key"}


def _serialize_value(val: Any, key: Optional[str] = None) -> Any:
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, Real) and not isinstance(val, bool) and not isfinite(float(val)):
        return None
    if key in ID_FIELDS and _is_present(val):
        return _code_to_string(val)
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    if hasattr(val, "item") and type(val).__module__.startswith("numpy"):
        return _serialize_value(val.item(), key)
    if isinstance(val, datetime):
        return val.isoformat()
    return val


def _df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    records = df.to_dict(orient="records")
    return [
        {k: _serialize_value(v, k) for k, v in row.items()}
        for row in records
    ]


def get_available_dates(storage: Storage) -> Dict[str, Any]:
    dates = storage.get_available_snapshot_dates()
    return {"items": dates, "latest": dates[0] if dates else None, "total": len(dates)}


def filter_underlying_codes(df: pd.DataFrame, codes: Optional[set[int]]) -> pd.DataFrame:
    if codes is None or df.empty:
        return df
    keyed = _attach_underlying_keys(df.copy())
    return keyed[keyed["underlying_key"].isin({str(code) for code in codes})].copy()


def get_merged_contracts(
    storage: Storage,
    snapshot_date: Optional[str] = None,
    underlying_ins_codes: Optional[set[int]] = None,
) -> pd.DataFrame:
    contracts = storage.get_contracts_df(snapshot_date=snapshot_date)
    contracts = filter_underlying_codes(contracts, underlying_ins_codes)
    if contracts.empty:
        return contracts
    client_type = storage.get_latest_client_type_df(snapshot_date=snapshot_date or storage.get_latest_snapshot_date())
    if client_type.empty:
        return contracts
    if "ins_code" not in contracts.columns or "ins_code" not in client_type.columns:
        return contracts
    contracts = contracts.copy()
    client_type = client_type.copy()
    merge_key = "_merge_ins_code"
    contracts[merge_key] = contracts["ins_code"].map(_code_to_string)
    client_type[merge_key] = client_type["ins_code"].map(_code_to_string)
    ct_cols = list(client_type.columns)
    drop_cols = [c for c in ct_cols if c in contracts.columns and c != merge_key]
    client_type = client_type.drop(columns=drop_cols, errors="ignore")
    client_type = client_type.drop_duplicates(merge_key, keep="last")
    merged = contracts.merge(client_type, on=merge_key, how="left")
    return merged.drop(columns=[merge_key], errors="ignore")


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip() != ""


def _normalize_text(value: Any) -> str:
    if not _is_present(value):
        return ""
    return _translate_digits(
        str(value)
        .strip()
        .lower()
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
    )


def _text_mask(df: pd.DataFrame, columns: tuple[str, ...], query: str) -> pd.Series:
    normalized_query = _normalize_text(query)
    mask = pd.Series(False, index=df.index)
    if not normalized_query:
        return mask
    for col in columns:
        if col in df.columns:
            normalized_col = df[col].map(_normalize_text)
            mask = mask | normalized_col.str.contains(normalized_query, na=False, regex=False)
    return mask


def _code_to_string(value: Any) -> str:
    if isinstance(value, str):
        text = _clean_numeric_text(value)
        if text.isdigit():
            try:
                return str(int(text))
            except ValueError:
                return text
        return value.strip()
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value).strip()


def _underlying_key(row: pd.Series) -> Optional[str]:
    code = row.get("underlying_ins_code")
    if _is_present(code):
        key = _code_to_string(code)
        if key.isdigit() and int(key) > 0:
            return key
    for field in ("underlying_symbol", "underlying_short_name"):
        symbol = row.get(field)
        if _is_present(symbol):
            return _normalize_text(symbol)
    return None


def _attach_underlying_keys(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    symbol_to_keys: Dict[str, set[str]] = {}
    for _, row in df.iterrows():
        code = row.get("underlying_ins_code")
        symbol = row.get("underlying_symbol")
        short_name = row.get("underlying_short_name")
        if not _is_present(code):
            continue
        key = _code_to_string(code)
        if not key.isdigit() or int(key) <= 0:
            continue
        for text in (symbol, short_name):
            normalized = _normalize_text(text)
            if normalized:
                symbol_to_keys.setdefault(normalized, set()).add(key)

    def key_for(row: pd.Series) -> Optional[str]:
        code_key = _underlying_key(row)
        if code_key and _clean_numeric_text(code_key).isdigit():
            return code_key
        candidates: set[str] = set()
        for text in (row.get("underlying_symbol"), row.get("underlying_short_name")):
            candidates.update(symbol_to_keys.get(_normalize_text(text), set()))
        if len(candidates) == 1:
            return next(iter(candidates))
        return code_key

    df["underlying_key"] = df.apply(key_for, axis=1)
    return df


def _sum_or_none(series: pd.Series) -> Optional[float]:
    numeric = _numeric_series(series)
    return finite_sum(numeric) if not numeric.empty else None


def _min_or_none(series: pd.Series) -> Optional[float]:
    numeric = _numeric_series(series)
    value = numeric.min()
    return None if pd.isna(value) else float(value)


def _max_or_none(series: pd.Series) -> Optional[float]:
    numeric = _numeric_series(series)
    value = numeric.max()
    return None if pd.isna(value) else float(value)


def _numeric_series(series: pd.Series) -> pd.Series:
    numeric = series.map(_to_finite_float)
    return numeric[numeric.notna()]


def _to_finite_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError, OverflowError):
        pass
    if isinstance(value, str):
        value = _clean_numeric_text(value)
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _open_interest_value(row: Any) -> Optional[float]:
    if not hasattr(row, "get"):
        row = {field: getattr(row, field, None) for field in ("buy_open_positions", "sell_open_positions")}
    return current_open_interest(row)


def _sum_open_interest_frame(df: pd.DataFrame) -> Optional[float]:
    if "buy_open_positions" not in df.columns and "sell_open_positions" not in df.columns:
        return None
    values = df.apply(_open_interest_value, axis=1)
    return _sum_or_none(values)


def _sum_open_interest_rows(rows: List[Dict[str, Any]]) -> Optional[float]:
    values = [_open_interest_value(row) for row in rows]
    numeric = [value for value in values if value is not None]
    return finite_sum(numeric) if numeric else None


def _first_present(series: pd.Series) -> Any:
    for value in series:
        if _is_present(value):
            return value
    return None


def get_underlyings(
    storage: Storage,
    q: Optional[str] = None,
    snapshot_date: Optional[str] = None,
    underlying_ins_codes: Optional[set[int]] = None,
) -> Dict[str, Any]:
    merged = get_merged_contracts(storage, snapshot_date=snapshot_date, underlying_ins_codes=underlying_ins_codes)
    if merged.empty:
        return {"items": [], "total": 0}

    df = _attach_underlying_keys(merged)
    df = df[df["underlying_key"].notna()]
    if q:
        df = df[_text_mask(df, ("underlying_symbol", "underlying_short_name"), q)]

    items: List[Dict[str, Any]] = []
    for key, group in df.groupby("underlying_key", dropna=True):
        end_dates = group.get("end_date", pd.Series(dtype=float))
        nearest_end_date = _min_or_none(end_dates)
        latest_end_date = _max_or_none(end_dates)
        strikes = group.get("strike_price", pd.Series(dtype=float))
        items.append(
            {
                "underlying_key": key,
                "underlying_ins_code": _serialize_value(
                    _first_present(group.get("underlying_ins_code", pd.Series(dtype=object))),
                    "underlying_ins_code",
                ),
                "underlying_symbol": _first_present(group.get("underlying_symbol", pd.Series(dtype=object))),
                "underlying_short_name": _first_present(group.get("underlying_short_name", pd.Series(dtype=object))),
                "underlying_last_price": _serialize_value(
                    _first_present(group.get("underlying_last_price", pd.Series(dtype=object)))
                ),
                "underlying_closing_price": _serialize_value(
                    _first_present(group.get("underlying_closing_price", pd.Series(dtype=object)))
                ),
                "contract_count": int(group.shape[0]),
                "call_count": int((group.get("option_type") == "call").sum()) if "option_type" in group else 0,
                "put_count": int((group.get("option_type") == "put").sum()) if "option_type" in group else 0,
                "nearest_end_date": None if nearest_end_date is None else int(nearest_end_date),
                "latest_end_date": None if latest_end_date is None else int(latest_end_date),
                "min_strike_price": _min_or_none(strikes),
                "max_strike_price": _max_or_none(strikes),
                "trade_volume": _sum_or_none(group.get("trade_volume", pd.Series(dtype=float))),
                "trade_value": _sum_or_none(group.get("trade_value", pd.Series(dtype=float))),
                "open_interest": _sum_open_interest_frame(group),
                "natural_money_flow": _sum_or_none(group.get("natural_money_flow", pd.Series(dtype=float))),
                "legal_money_flow": _sum_or_none(group.get("legal_money_flow", pd.Series(dtype=float))),
                "updated_at": _serialize_value(_first_present(group.get("updated_at", pd.Series(dtype=object)))),
            }
        )

    items.sort(key=lambda item: str(item.get("underlying_symbol") or ""))
    return {"items": items, "total": len(items)}


def get_underlying_contracts(
    storage: Storage,
    underlying_key: str,
    q: Optional[str] = None,
    snapshot_date: Optional[str] = None,
) -> Dict[str, Any]:
    merged = get_merged_contracts(storage, snapshot_date=snapshot_date)
    if merged.empty:
        return {"items": [], "total": 0, "underlying": None}

    df = _attach_underlying_keys(merged)
    target_key = _lookup_underlying_key(underlying_key)
    if not _clean_numeric_text(target_key).isdigit():
        normalized_target = _normalize_text(underlying_key)
        symbol_matches = df[
            df.get("underlying_symbol", pd.Series(index=df.index, dtype=object)).map(_normalize_text).eq(normalized_target)
            | df.get("underlying_short_name", pd.Series(index=df.index, dtype=object)).map(_normalize_text).eq(normalized_target)
        ]
        if not symbol_matches.empty:
            target_key = str(symbol_matches.iloc[0]["underlying_key"])
    df = df[df["underlying_key"] == target_key]
    if q:
        df = df[_text_mask(df, ("symbol", "short_name", "long_name"), q)]

    underlying = None
    if not df.empty:
        underlying = {
            "underlying_key": str(df.iloc[0]["underlying_key"]),
            "underlying_ins_code": _serialize_value(
                _first_present(df.get("underlying_ins_code", pd.Series(dtype=object))),
                "underlying_ins_code",
            ),
            "underlying_symbol": _first_present(df.get("underlying_symbol", pd.Series(dtype=object))),
            "underlying_short_name": _first_present(df.get("underlying_short_name", pd.Series(dtype=object))),
            "underlying_last_price": _serialize_value(
                _first_present(df.get("underlying_last_price", pd.Series(dtype=object)))
            ),
            "underlying_closing_price": _serialize_value(
                _first_present(df.get("underlying_closing_price", pd.Series(dtype=object)))
            ),
        }
    return {"items": _df_to_records(df), "total": len(df), "underlying": underlying}


def _lookup_underlying_key(value: Any) -> str:
    if _is_present(value):
        text = _clean_numeric_text(str(value))
        if text.isdigit():
            try:
                return str(int(text))
            except ValueError:
                return text
    return _normalize_text(value)


def _clean_numeric_text(value: str) -> str:
    return _translate_digits(
        value.strip()
        .replace(",", "")
        .replace("٬", "")
        .replace("،", "")
        .replace("٫", ".")
        .replace(" ", "")
    )


def _translate_digits(value: str) -> str:
    return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def get_summary(
    storage: Storage,
    snapshot_date: Optional[str] = None,
    underlying_ins_codes: Optional[set[int]] = None,
) -> Dict[str, Any]:
    merged = get_merged_contracts(storage, snapshot_date=snapshot_date, underlying_ins_codes=underlying_ins_codes)
    contracts = filter_underlying_codes(storage.get_contracts_df(snapshot_date=snapshot_date), underlying_ins_codes)
    last_update = None
    if not contracts.empty and "updated_at" in contracts.columns:
        last_update = contracts["updated_at"].max()
    summary: Dict[str, Any] = {
        "contract_count": len(contracts),
        "underlying_count": 0,
        "call_count": 0,
        "put_count": 0,
        "total_trade_volume": None,
        "total_trade_value": None,
        "last_update": _serialize_value(last_update),
        "total_natural_flow": None,
        "total_legal_flow": None,
        "total_buy_oi": None,
        "total_sell_oi": None,
        "total_open_interest": None,
    }
    if not merged.empty:
        if "natural_money_flow" in merged.columns:
            summary["total_natural_flow"] = _sum_or_none(merged["natural_money_flow"])
        if "legal_money_flow" in merged.columns:
            summary["total_legal_flow"] = _sum_or_none(merged["legal_money_flow"])
        if "buy_open_positions" in merged.columns:
            summary["total_buy_oi"] = _sum_or_none(merged["buy_open_positions"])
        if "sell_open_positions" in merged.columns:
            summary["total_sell_oi"] = _sum_or_none(merged["sell_open_positions"])
        summary["total_open_interest"] = _sum_open_interest_frame(merged)
        merged_with_keys = merged.copy()
        merged_with_keys = _attach_underlying_keys(merged_with_keys)
        summary["underlying_count"] = int(merged_with_keys["underlying_key"].dropna().nunique())
        if "option_type" in merged.columns:
            summary["call_count"] = int((merged["option_type"] == "call").sum())
            summary["put_count"] = int((merged["option_type"] == "put").sum())
        if "trade_volume" in merged.columns:
            summary["total_trade_volume"] = _sum_or_none(merged["trade_volume"])
        if "trade_value" in merged.columns:
            summary["total_trade_value"] = _sum_or_none(merged["trade_value"])
    return summary


def get_watchlist_underlyings(
    storage: Storage, q: Optional[str] = None, snapshot_date: Optional[str] = None,
) -> Dict[str, Any]:
    codes = set(storage.get_watchlist_codes())
    payload = get_underlyings(storage, snapshot_date=snapshot_date, underlying_ins_codes=codes)
    items = {item["underlying_key"]: item for item in payload["items"]}
    catalog = {row["ins_code"]: row for row in storage.get_underlying_catalog()}
    for code in sorted(codes):
        key = str(code)
        meta = catalog.get(key, {})
        item = items.setdefault(key, {
            "underlying_key": key,
            "underlying_ins_code": key,
            "underlying_symbol": meta.get("symbol") or key,
            "underlying_short_name": meta.get("name"),
            "data_status": "needs_refresh",
        })
        item.setdefault("data_status", "ready")
        item["available"] = meta.get("available", False)
    rows = list(items.values())
    if q:
        needle = _normalize_text(q)
        rows = [row for row in rows if any(needle in _normalize_text(row.get(k)) for k in (
            "underlying_symbol", "underlying_short_name",
        ))]
    rows.sort(key=lambda row: str(row.get("underlying_symbol") or ""))
    return {"items": rows, "total": len(rows), "watchlist_count": len(codes)}


def get_sentiment(
    storage: Storage,
    q: Optional[str] = None,
    snapshot_date: Optional[str] = None,
) -> Dict[str, Any]:
    merged = get_merged_contracts(storage, snapshot_date=snapshot_date)
    result = analyze_options_sentiment(merged)
    items = result["items"]
    if q:
        q_lower = _normalize_text(q)
        items = [
            item
            for item in items
            if q_lower in _normalize_text(item.get("underlying_symbol"))
            or q_lower in _normalize_text(item.get("underlying_ins_code"))
            or q_lower in _normalize_text(item.get("sentiment_label"))
        ]
    return {
        "items": [{key: _serialize_value(value, key) for key, value in item.items()} for item in items],
        "total": len(items),
        "summary": result["summary"],
    }


def get_underlying_trend(
    storage: Storage,
    underlying_key: str,
    dates: List[str],
) -> Dict[str, Any]:
    daily_items: List[Dict[str, Any]] = []
    for snapshot_date in dates:
        contracts = get_underlying_contracts(
            storage,
            underlying_key=underlying_key,
            snapshot_date=snapshot_date,
        )
        rows = contracts.get("items", [])
        if not rows:
            continue
        daily_items.append(
            {
                "date": snapshot_date,
                "contract_count": len(rows),
                "underlying": contracts.get("underlying"),
                "people": {
                    "natural": _build_trend_person(rows, "natural"),
                    "legal": _build_trend_person(rows, "legal"),
                },
            }
        )
    return {
        "items": daily_items,
        "total": len(daily_items),
        "summary": {
            "natural": _build_trend_summary(daily_items, "natural"),
            "legal": _build_trend_summary(daily_items, "legal"),
        },
    }


def _build_trend_person(rows: List[Dict[str, Any]], prefix: str) -> Dict[str, Any]:
    call_rows = [row for row in rows if row.get("option_type") == "call"]
    put_rows = [row for row in rows if row.get("option_type") == "put"]
    types_complete = len(call_rows) + len(put_rows) == len(rows)
    call_moneyness_complete = types_complete and all(row.get("moneyness") in {"ITM", "OTM", "ATM"} for row in call_rows)
    put_moneyness_complete = types_complete and all(row.get("moneyness") in {"ITM", "OTM", "ATM"} for row in put_rows)
    moneyness_complete = types_complete and call_moneyness_complete and put_moneyness_complete
    # Moneyness buckets only cover ITM/OTM; ATM contracts stay in the totals.
    itm_rows = [row for row in rows if row.get("moneyness") == "ITM"]
    otm_rows = [row for row in rows if row.get("moneyness") == "OTM"]

    call_buy = _sum_rows(call_rows, f"{prefix}_buy_volume") if types_complete else None
    call_sell = _sum_rows(call_rows, f"{prefix}_sell_volume") if types_complete else None
    put_buy = _sum_rows(put_rows, f"{prefix}_buy_volume") if types_complete else None
    put_sell = _sum_rows(put_rows, f"{prefix}_sell_volume") if types_complete else None
    itm_volume = _sum_participant_volume(itm_rows, prefix) if moneyness_complete else None
    otm_volume = _sum_participant_volume(otm_rows, prefix) if moneyness_complete else None
    call_volume = _sum_participant_volume(call_rows, prefix) if types_complete else None
    put_volume = _sum_participant_volume(put_rows, prefix) if types_complete else None

    oi = open_interest_snapshot(rows)
    current_oi, yesterday_oi, oi_change = oi["current"], oi["yesterday"], oi["change"]
    call_oi = open_interest_snapshot(call_rows)["change"] if types_complete else None
    put_oi = open_interest_snapshot(put_rows)["change"] if types_complete else None

    def compare(left, right):
        return 0 if left is None or right is None else (left > right) - (left < right)

    call_signal = compare(call_buy, call_sell)
    put_signal = compare(put_sell, put_buy)
    call_itm = _sum_participant_volume([row for row in call_rows if row.get("moneyness") == "ITM"], prefix) if call_moneyness_complete else None
    call_otm = _sum_participant_volume([row for row in call_rows if row.get("moneyness") == "OTM"], prefix) if call_moneyness_complete else None
    put_itm = _sum_participant_volume([row for row in put_rows if row.get("moneyness") == "ITM"], prefix) if put_moneyness_complete else None
    put_otm = _sum_participant_volume([row for row in put_rows if row.get("moneyness") == "OTM"], prefix) if put_moneyness_complete else None
    call_moneyness = compare(call_otm, call_itm)
    moneyness_score = (1 if call_moneyness > 0 else 0.5 if call_moneyness < 0 else 0) - (1 if compare(put_otm, put_itm) > 0 else 0)
    call_put_score = compare(call_volume, put_volume)
    oi_score = (1 if call_oi is not None and call_oi > 0 else -1 if call_oi is not None and call_oi < 0 else 0)
    oi_score -= (1 if put_oi is not None and put_oi > 0 else -1 if put_oi is not None and put_oi < 0 else 0)
    complete = bool(rows) and types_complete and moneyness_complete and all(value is not None for value in (call_buy, call_sell, put_buy, put_sell))
    score = call_signal + put_signal + moneyness_score + call_put_score + oi_score if complete else None
    unavailable_label = "نوع قرارداد مشخص نیست" if not types_complete else (
        "وضعیت ITM/OTM کامل نیست" if not moneyness_complete else "داده حقیقی/حقوقی کامل نیست")

    return {
        "score": score,
        "label": _trend_day_label(score) if score is not None else unavailable_label,
        "class_name": _trend_day_class(score) if score is not None else "neutral",
        "call_buy": call_buy,
        "call_sell": call_sell,
        "put_buy": put_buy,
        "put_sell": put_sell,
        "itm_volume": itm_volume,
        "otm_volume": otm_volume,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_put_ratio": finite_ratio(call_volume, put_volume) if types_complete else None,
        "open_interest": current_oi,
        "yesterday_open_interest": yesterday_oi,
        "open_interest_change": oi_change,
        "has_open_interest": oi_change is not None,
    }


def _build_trend_summary(daily_items: List[Dict[str, Any]], person: str) -> Dict[str, Any]:
    scores = [
        item.get("people", {}).get(person, {}).get("score")
        for item in daily_items
    ]
    if not scores:
        return {"label": "داده کافی نیست", "class_name": "neutral", "average_score": None}
    recent = scores[-3:]
    previous = scores[:-3]
    if any(value is None for value in recent):
        return {"label": "داده روزهای اخیر کامل نیست", "class_name": "neutral", "average_score": None,
                "previous_average_score": None, "score_change": None}
    recent_avg = sum(recent) / len(recent)
    previous_avg = (sum(previous) / len(previous) if all(value is not None for value in previous) else None) if previous else recent_avg
    delta = recent_avg - previous_avg if previous_avg is not None else None
    if recent_avg >= 3 and delta is not None and delta >= 0.5:
        label = "روند صعودی تقویت‌شونده"
        class_name = "bullish"
    elif recent_avg >= 2:
        label = "روند صعودی اما کم‌شتاب"
        class_name = "cautious"
    elif recent_avg <= -1:
        label = "روند ضعیف / احتیاطی"
        class_name = "weak"
    elif delta is not None and delta >= 0.75:
        label = "روند رو به بهبود"
        class_name = "cautious"
    else:
        label = "روند خنثی"
        class_name = "neutral"
    return {
        "label": label,
        "class_name": class_name,
        "average_score": recent_avg,
        "previous_average_score": previous_avg,
        "score_change": delta,
    }


def _sum_participant_volume(rows: List[Dict[str, Any]], prefix: str) -> Optional[float]:
    buy = _sum_rows(rows, f"{prefix}_buy_volume")
    sell = _sum_rows(rows, f"{prefix}_sell_volume")
    return finite_sum((buy, sell)) if buy is not None and sell is not None else None


def _sum_rows(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    values = [_to_finite_float(row.get(key)) for row in rows]
    return finite_sum(values) if all(value is not None and value >= 0 for value in values) else None


def _numeric_value(value: Any) -> float:
    return _to_finite_float(value) or 0.0


def _trend_day_label(score: float) -> str:
    if score >= 5:
        return "صعودی قوی"
    if score >= 3:
        return "صعودی محتاط"
    if score <= -1:
        return "ضعیف"
    return "خنثی"


def _trend_day_class(score: float) -> str:
    if score >= 5:
        return "bullish"
    if score >= 3:
        return "cautious"
    if score <= -1:
        return "weak"
    return "neutral"
