"""Unit tests for options sentiment analysis."""

import pandas as pd

from options.backend.analysis.sentiment import (
    analyze_options_sentiment,
    compute_moneyness,
    detect_option_type,
)


def test_detect_option_type_from_persian_names() -> None:
    assert detect_option_type({"long_name": "اختیار خرید خودرو"}) == "call"
    assert detect_option_type({"long_name": "اختیار فروش خودرو"}) == "put"
    assert detect_option_type({"symbol": "ضخود1230"}) == "call"
    assert detect_option_type({"symbol": "طخود1230"}) == "put"


def test_compute_moneyness_for_call_and_put() -> None:
    assert compute_moneyness("call", 100, 120) == "ITM"
    assert compute_moneyness("call", 100, 80) == "OTM"
    assert compute_moneyness("put", 100, 80) == "ITM"
    assert compute_moneyness("put", 100, 120) == "OTM"


def test_bullish_sentiment_when_call_buy_and_put_sell_dominate() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "ضخود100",
                "long_name": "اختیار خرید خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 120,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 5000,
                "natural_buy_volume": 3000,
                "legal_buy_volume": 1000,
                "natural_sell_volume": 1500,
                "legal_sell_volume": 500,
                "buy_open_positions": 600,
                "sell_open_positions": 600,
                "yesterday_open_positions": 900,
            },
            {
                "symbol": "طخود100",
                "long_name": "اختیار فروش خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 120,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 2000,
                "natural_buy_volume": 500,
                "legal_buy_volume": 100,
                "natural_sell_volume": 2500,
                "legal_sell_volume": 500,
                "buy_open_positions": 300,
                "sell_open_positions": 300,
                "yesterday_open_positions": 400,
            },
        ]
    )

    result = analyze_options_sentiment(df)
    item = result["items"][0]

    assert item["sentiment_class"] in {"bullish", "cautious_bullish"}
    assert item["score"] >= 4
    assert item["call_put_ratio"] == 2.5
    assert item["call_itm_volume"] == 5000


def test_bearish_sentiment_when_put_buy_and_call_sell_dominate() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "ضخود100",
                "long_name": "اختیار خرید خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 90,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 1000,
                "natural_buy_volume": 400,
                "legal_buy_volume": 100,
                "natural_sell_volume": 1800,
                "legal_sell_volume": 200,
                "buy_open_positions": 500,
                "sell_open_positions": 500,
                "yesterday_open_positions": 700,
            },
            {
                "symbol": "طخود100",
                "long_name": "اختیار فروش خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 90,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 5000,
                "natural_buy_volume": 2600,
                "legal_buy_volume": 400,
                "natural_sell_volume": 500,
                "legal_sell_volume": 100,
                "buy_open_positions": 800,
                "sell_open_positions": 800,
                "yesterday_open_positions": 1000,
            },
        ]
    )

    result = analyze_options_sentiment(df)
    item = result["items"][0]

    assert item["sentiment_class"] == "bearish"
    assert item["score"] <= -3
    assert item["call_put_ratio"] == 0.2


def test_sentiment_open_interest_uses_position_count_once() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "ضخود100",
                "long_name": "اختیار خرید خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 120,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 100,
                "buy_open_positions": 600,
                "sell_open_positions": 600,
                "yesterday_open_positions": 500,
            },
            {
                "symbol": "طخود100",
                "long_name": "اختیار فروش خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 120,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 50,
                "buy_open_positions": 300,
                "sell_open_positions": 300,
                "yesterday_open_positions": 250,
            },
        ]
    )

    item = analyze_options_sentiment(df)["items"][0]

    assert item["open_interest"] == 900
    assert item["yesterday_open_interest"] == 750
    assert item["open_interest_change"] == 150


def test_sentiment_preserves_zero_underlying_last_price() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "طخود100",
                "long_name": "اختیار فروش خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 0,
                "underlying_closing_price": 120,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 100,
            }
        ]
    )

    item = analyze_options_sentiment(df)["items"][0]

    assert item["underlying_price"] == 0
    assert item["put_itm_volume"] == 100


def test_sentiment_groups_missing_underlying_codes_by_symbol() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "ضخود100",
                "long_name": "اختیار خرید خودرو",
                "underlying_ins_code": None,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 120,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 100,
            },
            {
                "symbol": "ضفلا100",
                "long_name": "اختیار خرید فولاد",
                "underlying_ins_code": None,
                "underlying_symbol": "فولاد",
                "underlying_last_price": 80,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 200,
            },
        ]
    )

    result = analyze_options_sentiment(df)

    assert result["summary"]["group_count"] == 2
    assert {item["underlying_symbol"] for item in result["items"]} == {"خودرو", "فولاد"}


def test_sentiment_groups_equivalent_underlying_codes_together() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "ضخود100",
                "long_name": "اختیار خرید خودرو",
                "underlying_ins_code": "۲٬۰۰۱",
                "underlying_symbol": "خودرو",
                "underlying_last_price": 120,
                "end_date": 20250630,
                "strike_price": 100,
                "trade_volume": 100,
            },
            {
                "symbol": "ضخود120",
                "long_name": "اختیار خرید خودرو",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_last_price": 120,
                "end_date": 20250630,
                "strike_price": 120,
                "trade_volume": 200,
            },
        ]
    )

    result = analyze_options_sentiment(df)

    assert result["summary"]["group_count"] == 1
    assert result["items"][0]["contract_count"] == 2
    assert result["items"][0]["underlying_ins_code"] == 2001
