"""API helpers for web frontend."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from numbers import Real
from typing import Any, Dict, List, Optional

import pandas as pd

from options.backend.analysis.sentiment import analyze_options_sentiment
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


def get_merged_contracts(storage: Storage, snapshot_date: Optional[str] = None) -> pd.DataFrame:
    contracts = storage.get_contracts_df(snapshot_date=snapshot_date)
    if contracts.empty:
        return contracts
    client_type = storage.get_latest_client_type_df(snapshot_date=snapshot_date)
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
        return _code_to_string(code)
    symbol = row.get("underlying_symbol")
    if _is_present(symbol):
        return _normalize_text(symbol)
    return None


def _attach_underlying_keys(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    symbol_to_key: Dict[str, str] = {}
    for _, row in df.iterrows():
        code = row.get("underlying_ins_code")
        symbol = row.get("underlying_symbol")
        short_name = row.get("underlying_short_name")
        if not _is_present(code):
            continue
        key = _code_to_string(code)
        for text in (symbol, short_name):
            normalized = _normalize_text(text)
            if normalized:
                symbol_to_key.setdefault(normalized, key)

    def key_for(row: pd.Series) -> Optional[str]:
        code_key = _underlying_key(row)
        if code_key and _clean_numeric_text(code_key).isdigit():
            return code_key
        for text in (row.get("underlying_symbol"), row.get("underlying_short_name")):
            normalized = _normalize_text(text)
            if normalized in symbol_to_key:
                return symbol_to_key[normalized]
        return code_key

    df["underlying_key"] = df.apply(key_for, axis=1)
    return df


def _sum_or_none(series: pd.Series) -> Optional[float]:
    numeric = _numeric_series(series)
    value = numeric.sum(min_count=1)
    return None if pd.isna(value) else float(value)


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
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        value = _clean_numeric_text(value)
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _first_present(series: pd.Series) -> Any:
    for value in series:
        if _is_present(value):
            return value
    return None


def get_underlyings(
    storage: Storage,
    q: Optional[str] = None,
    snapshot_date: Optional[str] = None,
) -> Dict[str, Any]:
    merged = get_merged_contracts(storage, snapshot_date=snapshot_date)
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
                "open_interest": _sum_or_none(group.get("buy_open_positions", pd.Series(dtype=float))),
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
        .replace(" ", "")
    )


def _translate_digits(value: str) -> str:
    return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def get_summary(storage: Storage, snapshot_date: Optional[str] = None) -> Dict[str, Any]:
    merged = get_merged_contracts(storage, snapshot_date=snapshot_date)
    contracts = storage.get_contracts_df(snapshot_date=snapshot_date)
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
        "items": items,
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
    itm_rows = [row for row in rows if row.get("moneyness") == "ITM"]
    otm_rows = [row for row in rows if row.get("moneyness") == "OTM"]

    call_buy = _sum_rows(call_rows, f"{prefix}_buy_volume")
    call_sell = _sum_rows(call_rows, f"{prefix}_sell_volume")
    put_buy = _sum_rows(put_rows, f"{prefix}_buy_volume")
    put_sell = _sum_rows(put_rows, f"{prefix}_sell_volume")
    itm_volume = _sum_participant_volume(itm_rows, prefix)
    otm_volume = _sum_participant_volume(otm_rows, prefix)
    call_volume = _sum_participant_volume(call_rows, prefix)
    put_volume = _sum_participant_volume(put_rows, prefix)

    has_current_oi = any(row.get("buy_open_positions") is not None for row in rows)
    has_yesterday_oi = any(row.get("yesterday_open_positions") is not None for row in rows)
    current_oi = _sum_rows(rows, "buy_open_positions") if has_current_oi else None
    yesterday_oi = _sum_rows(rows, "yesterday_open_positions") if has_yesterday_oi else None
    oi_change = current_oi - yesterday_oi if has_current_oi and has_yesterday_oi else None

    call_signal = 1 if call_buy > call_sell else -1 if call_sell > call_buy else 0
    put_signal = 1 if put_sell > put_buy else -1 if put_buy > put_sell else 0
    moneyness_score = 2 if otm_volume > itm_volume else 1 if itm_volume > otm_volume else 0
    call_put_score = 1 if call_volume > put_volume else -1 if put_volume > call_volume else 0
    oi_score = 1 if oi_change is not None and oi_change > 0 else -1 if oi_change is not None and oi_change < 0 else 0
    score = call_signal + put_signal + moneyness_score + call_put_score + oi_score

    return {
        "score": score,
        "label": _trend_day_label(score),
        "class_name": _trend_day_class(score),
        "call_buy": call_buy,
        "call_sell": call_sell,
        "put_buy": put_buy,
        "put_sell": put_sell,
        "itm_volume": itm_volume,
        "otm_volume": otm_volume,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_put_ratio": None if put_volume == 0 else call_volume / put_volume,
        "open_interest": current_oi,
        "yesterday_open_interest": yesterday_oi,
        "open_interest_change": oi_change,
        "has_open_interest": oi_change is not None,
    }


def _build_trend_summary(daily_items: List[Dict[str, Any]], person: str) -> Dict[str, Any]:
    scores = [
        item["people"][person]["score"]
        for item in daily_items
        if item.get("people", {}).get(person)
    ]
    if not scores:
        return {"label": "داده کافی نیست", "class_name": "neutral", "average_score": None}
    recent = scores[-3:] if len(scores) >= 3 else scores
    previous = scores[:-3] if len(scores) > 3 else scores[:-1]
    recent_avg = sum(recent) / len(recent)
    previous_avg = sum(previous) / len(previous) if previous else recent_avg
    delta = recent_avg - previous_avg
    if recent_avg >= 3 and delta >= 0.5:
        label = "روند صعودی تقویت‌شونده"
        class_name = "bullish"
    elif recent_avg >= 2:
        label = "روند صعودی اما کم‌شتاب"
        class_name = "cautious"
    elif recent_avg <= -1:
        label = "روند ضعیف / احتیاطی"
        class_name = "weak"
    elif delta >= 0.75:
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


def _sum_participant_volume(rows: List[Dict[str, Any]], prefix: str) -> float:
    return _sum_rows(rows, f"{prefix}_buy_volume") + _sum_rows(rows, f"{prefix}_sell_volume")


def _sum_rows(rows: List[Dict[str, Any]], key: str) -> float:
    return sum(_numeric_value(row.get(key)) for row in rows)


def _numeric_value(value: Any) -> float:
    return _to_finite_float(value) or 0.0


def _trend_day_label(score: int) -> str:
    if score >= 4:
        return "صعودی قوی"
    if score >= 2:
        return "صعودی محتاط"
    if score <= -2:
        return "ضعیف"
    return "خنثی"


def _trend_day_class(score: int) -> str:
    if score >= 4:
        return "bullish"
    if score >= 2:
        return "cautious"
    if score <= -2:
        return "weak"
    return "neutral"
