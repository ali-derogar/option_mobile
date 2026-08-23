"""Options sentiment analysis.

The scoring here is intentionally transparent: every label is backed by a
small set of human-readable reasons so the dashboard can show why a group was
classified as bullish, bearish, or mixed.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, List, Optional

import pandas as pd


PERSIAN_LABELS = {
    "bullish": "صعودی",
    "cautious_bullish": "صعودی محتاطانه",
    "bearish": "نزولی",
    "neutral": "خنثی / متناقض",
}


def detect_option_type(row: Dict[str, Any] | pd.Series) -> Optional[str]:
    """Infer option type from the available TSETMC text fields."""
    parts = [
        row.get("option_type"),
        row.get("symbol"),
        row.get("short_name"),
        row.get("long_name"),
        row.get("instrument_id"),
    ]
    text = " ".join(str(p) for p in parts if p).strip().lower()
    if not text:
        return None

    if "put" in text or "اختیار فروش" in text:
        return "put"
    if "call" in text or "اختیار خرید" in text:
        return "call"

    symbol = str(row.get("symbol") or "").strip()
    if symbol.startswith("ض"):
        return "call"
    if symbol.startswith("ط"):
        return "put"
    return None


def compute_moneyness(
    option_type: Optional[str],
    strike_price: Any,
    underlying_price: Any,
) -> str:
    strike = _to_float(strike_price)
    underlying = _to_float(underlying_price)
    if option_type not in {"call", "put"} or strike is None or underlying is None:
        return "unknown"
    if underlying == strike:
        return "ATM"
    if option_type == "call":
        return "ITM" if underlying > strike else "OTM"
    return "ITM" if underlying < strike else "OTM"


def compute_intrinsic_value(
    option_type: Optional[str],
    strike_price: Any,
    underlying_price: Any,
) -> Optional[float]:
    strike = _to_float(strike_price)
    underlying = _to_float(underlying_price)
    if option_type not in {"call", "put"} or strike is None or underlying is None:
        return None
    if option_type == "call":
        return max(underlying - strike, 0.0)
    return max(strike - underlying, 0.0)


def analyze_options_sentiment(contracts: pd.DataFrame) -> Dict[str, Any]:
    """Build grouped sentiment rows by underlying asset and expiry."""
    if contracts.empty:
        return {"items": [], "summary": _empty_summary()}

    df = contracts.copy()
    df["option_type"] = df.apply(detect_option_type, axis=1)
    df["effective_underlying_price"] = df.apply(_effective_underlying_price, axis=1)
    df["moneyness"] = df.apply(
        lambda row: compute_moneyness(
            row.get("option_type"),
            row.get("strike_price"),
            row.get("effective_underlying_price"),
        ),
        axis=1,
    )
    df["intrinsic_value"] = df.apply(
        lambda row: compute_intrinsic_value(
            row.get("option_type"),
            row.get("strike_price"),
            row.get("effective_underlying_price"),
        ),
        axis=1,
    )
    df["sentiment_buy_volume"] = df.apply(_buy_volume, axis=1)
    df["sentiment_sell_volume"] = df.apply(_sell_volume, axis=1)
    df["sentiment_trade_volume"] = df.apply(_trade_volume, axis=1)
    df["sentiment_current_oi"] = df.apply(_current_open_interest, axis=1)
    df["sentiment_oi_change"] = df.apply(_open_interest_change, axis=1)

    group_cols = ["underlying_ins_code", "end_date"]
    items: List[Dict[str, Any]] = []
    grouped = df.groupby(group_cols, dropna=False, sort=True)
    for (underlying_ins_code, end_date), group in grouped:
        items.append(_analyze_group(group, underlying_ins_code, end_date))

    summary = _summarize(items)
    return {"items": items, "summary": summary}


def _analyze_group(group: pd.DataFrame, underlying_ins_code: Any, end_date: Any) -> Dict[str, Any]:
    calls = group[group["option_type"] == "call"]
    puts = group[group["option_type"] == "put"]
    unknown = group[group["option_type"].isna()]

    call_buy = _sum(calls, "sentiment_buy_volume")
    call_sell = _sum(calls, "sentiment_sell_volume")
    put_buy = _sum(puts, "sentiment_buy_volume")
    put_sell = _sum(puts, "sentiment_sell_volume")
    call_volume = _sum(calls, "sentiment_trade_volume")
    put_volume = _sum(puts, "sentiment_trade_volume")
    call_put_ratio = _ratio(call_volume, put_volume)

    call_itm = _sum(calls[calls["moneyness"] == "ITM"], "sentiment_trade_volume")
    call_otm = _sum(calls[calls["moneyness"] == "OTM"], "sentiment_trade_volume")
    put_itm = _sum(puts[puts["moneyness"] == "ITM"], "sentiment_trade_volume")
    put_otm = _sum(puts[puts["moneyness"] == "OTM"], "sentiment_trade_volume")
    call_known_volume = _total_or_none([call_itm, call_otm])
    call_itm_share = _ratio(call_itm, call_known_volume)
    call_otm_share = _ratio(call_otm, call_known_volume)

    current_oi = _sum(group, "sentiment_current_oi")
    oi_change = _sum(group, "sentiment_oi_change")
    yesterday_oi = current_oi - oi_change if current_oi is not None and oi_change is not None else None
    oi_change_pct = _ratio(oi_change, abs(yesterday_oi)) if yesterday_oi not in (None, 0) else None

    natural_flow = _sum(group, "natural_money_flow")
    legal_flow = _sum(group, "legal_money_flow")

    score = 0
    reasons: List[str] = []
    warnings: List[str] = []

    if _is_advancing(call_buy, call_sell):
        score += 2
        reasons.append("برتری حجم خرید اختیار خرید")
    if _is_advancing(put_sell, put_buy):
        score += 2
        reasons.append("برتری حجم فروش اختیار فروش")
    if _is_advancing(call_sell, call_buy):
        score -= 2
        reasons.append("برتری حجم فروش اختیار خرید")
    if _is_advancing(put_buy, put_sell):
        score -= 2
        reasons.append("برتری حجم خرید اختیار فروش")

    if call_put_ratio is not None:
        if call_put_ratio >= 3:
            score += 3
            reasons.append("نسبت Call/Put بسیار بالاتر از ۱")
        elif call_put_ratio >= 1.5:
            score += 2
            reasons.append("نسبت Call/Put بالاتر از ۱.۵")
        elif call_put_ratio >= 1.2:
            score += 1
            reasons.append("نسبت Call/Put بالاتر از ۱.۲")
        elif call_put_ratio <= 0.5:
            score -= 3
            reasons.append("نسبت Call/Put بسیار پایین‌تر از ۱")
        elif call_put_ratio <= 0.8:
            score -= 1
            reasons.append("نسبت Call/Put پایین‌تر از ۰.۸")

    if natural_flow is not None:
        if natural_flow > 0:
            score += 1
            reasons.append("خالص جریان پول حقیقی مثبت است")
        elif natural_flow < 0:
            score -= 1
            reasons.append("خالص جریان پول حقیقی منفی است")

    if oi_change and oi_change > 0:
        if score > 0:
            score += 1
            reasons.append("افزایش موقعیت باز هم‌جهت با نشانه‌های صعودی")
        elif score < 0:
            score -= 1
            reasons.append("افزایش موقعیت باز هم‌جهت با نشانه‌های نزولی")
        else:
            reasons.append("افزایش موقعیت باز، اما بدون جهت روشن")
    elif oi_change and oi_change < 0:
        warnings.append("موقعیت باز کاهش یافته؛ بخشی از حجم می‌تواند بستن موقعیت قبلی باشد")

    if call_otm_share is not None and call_otm_share >= 0.35 and score > 0:
        score += 1
        reasons.append("سهم قابل توجه اختیار خرید OTM")
    if call_itm_share is not None and call_itm_share >= 0.65 and score > 0:
        reasons.append("تمرکز اختیار خرید روی ITM؛ برداشت صعودی محتاطانه‌تر است")

    if not reasons:
        reasons.append("داده‌ها جهت غالب روشنی نشان نمی‌دهند")
    if _same_side_volume(call_buy, call_sell) and _same_side_volume(put_buy, put_sell):
        warnings.append("داده عمومی سمت آغازکننده معامله را مشخص نمی‌کند؛ خرید/فروش برابر می‌تواند صرفاً دو سمت هر معامله باشد")
    if unknown.shape[0]:
        warnings.append(f"{unknown.shape[0]} قرارداد بدون تشخیص قطعی Call/Put")
    if group["effective_underlying_price"].isna().all():
        warnings.append("قیمت دارایی پایه موجود نیست؛ ITM/OTM قابل اتکا نیست")
    client_volume_cols = [
        "natural_buy_volume",
        "legal_buy_volume",
        "natural_sell_volume",
        "legal_sell_volume",
    ]
    available_client_cols = [col for col in client_volume_cols if col in group.columns]
    if not available_client_cols or group[available_client_cols].isna().all().all():
        warnings.append("داده حقیقی/حقوقی موجود نیست؛ برتری خرید/فروش محدودتر تفسیر می‌شود")

    sentiment_class = _label_from_score(score, call_itm_share, call_otm_share)
    confidence = _confidence(score, warnings)
    sample = group.iloc[0]

    return {
        "row_key": f"{_clean_key(underlying_ins_code)}-{_clean_key(end_date)}",
        "underlying_ins_code": _serialize_num(underlying_ins_code),
        "underlying_symbol": _first_non_empty(group, "underlying_symbol") or _first_non_empty(group, "underlying_short_name"),
        "end_date": _serialize_num(end_date),
        "contract_count": int(group.shape[0]),
        "call_count": int(calls.shape[0]),
        "put_count": int(puts.shape[0]),
        "unknown_count": int(unknown.shape[0]),
        "underlying_price": _to_float(sample.get("effective_underlying_price")),
        "call_buy_volume": call_buy,
        "call_sell_volume": call_sell,
        "put_buy_volume": put_buy,
        "put_sell_volume": put_sell,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_put_ratio": call_put_ratio,
        "call_itm_volume": call_itm,
        "call_otm_volume": call_otm,
        "put_itm_volume": put_itm,
        "put_otm_volume": put_otm,
        "call_itm_share": call_itm_share,
        "call_otm_share": call_otm_share,
        "open_interest": current_oi,
        "yesterday_open_interest": yesterday_oi,
        "open_interest_change": oi_change,
        "open_interest_change_pct": oi_change_pct,
        "natural_money_flow": natural_flow,
        "legal_money_flow": legal_flow,
        "score": score,
        "confidence": confidence,
        "sentiment_class": sentiment_class,
        "sentiment_label": PERSIAN_LABELS[sentiment_class],
        "reasons": reasons,
        "warnings": warnings,
    }


def _effective_underlying_price(row: pd.Series) -> Optional[float]:
    return _to_float(row.get("underlying_last_price")) or _to_float(row.get("underlying_closing_price"))


def _buy_volume(row: pd.Series) -> Optional[float]:
    values = [_to_float(row.get("natural_buy_volume")), _to_float(row.get("legal_buy_volume"))]
    return _sum_values(values)


def _sell_volume(row: pd.Series) -> Optional[float]:
    values = [_to_float(row.get("natural_sell_volume")), _to_float(row.get("legal_sell_volume"))]
    return _sum_values(values)


def _trade_volume(row: pd.Series) -> Optional[float]:
    direct = _to_float(row.get("trade_volume"))
    if direct is not None:
        return direct
    buy = _buy_volume(row)
    sell = _sell_volume(row)
    values = [v for v in (buy, sell) if v is not None]
    if not values:
        return None
    return max(values)


def _current_open_interest(row: pd.Series) -> Optional[float]:
    values = [_to_float(row.get("buy_open_positions")), _to_float(row.get("sell_open_positions"))]
    return _sum_values(values)


def _open_interest_change(row: pd.Series) -> Optional[float]:
    current = _current_open_interest(row)
    yesterday = _to_float(row.get("yesterday_open_positions"))
    if current is None or yesterday is None:
        return None
    return current - yesterday


def _label_from_score(score: int, call_itm_share: Optional[float], call_otm_share: Optional[float]) -> str:
    if score >= 4:
        if (call_itm_share or 0) >= 0.65 and (call_otm_share or 0) < 0.35:
            return "cautious_bullish"
        return "bullish"
    if score <= -3:
        return "bearish"
    return "neutral"


def _confidence(score: int, warnings: List[str]) -> int:
    value = 35 + min(abs(score), 6) * 10 - len(warnings) * 8
    return max(15, min(95, value))


def _summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = _empty_summary()
    summary["group_count"] = len(items)
    for item in items:
        key = item["sentiment_class"]
        summary[key] = summary.get(key, 0) + 1
    if items:
        summary["average_confidence"] = round(sum(i["confidence"] for i in items) / len(items), 1)
    return summary


def _empty_summary() -> Dict[str, Any]:
    return {
        "group_count": 0,
        "bullish": 0,
        "cautious_bullish": 0,
        "bearish": 0,
        "neutral": 0,
        "average_confidence": None,
    }


def _sum(df: pd.DataFrame, column: str) -> Optional[float]:
    if df.empty or column not in df.columns:
        return None
    values = [_to_float(v) for v in df[column].tolist()]
    return _sum_values(values)


def _sum_values(values: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None and isfinite(v)]
    if not clean:
        return None
    return float(sum(clean))


def _total_or_none(values: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None and isfinite(v)]
    if not clean:
        return None
    return float(sum(clean))


def _ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den in (None, 0):
        return None
    return float(num / den)


def _is_advancing(a: Optional[float], b: Optional[float], threshold: float = 1.05) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return a > 0
    return a / b >= threshold


def _same_side_volume(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= 0.000001


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(num):
        return None
    return num


def _serialize_num(value: Any) -> Any:
    num = _to_float(value)
    if num is None:
        return None
    if num.is_integer():
        return int(num)
    return num


def _first_non_empty(df: pd.DataFrame, column: str) -> Optional[str]:
    if column not in df.columns:
        return None
    for value in df[column].tolist():
        if value is not None and str(value).strip() and str(value) != "nan":
            return str(value)
    return None


def _clean_key(value: Any) -> str:
    if value is None or str(value) == "nan":
        return "none"
    return str(_serialize_num(value) or value)
