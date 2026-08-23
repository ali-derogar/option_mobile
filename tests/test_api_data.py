"""API helper tests for frontend-facing numeric data."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from options.backend.api.data import (
    _code_to_string,
    _serialize_value,
    get_sentiment,
    get_summary,
    get_underlying_contracts,
    get_underlying_trend,
    get_underlyings,
)


class FakeStorage:
    def __init__(self, contracts: pd.DataFrame, client_type: pd.DataFrame | None = None):
        self.contracts = contracts
        self.client_type = client_type if client_type is not None else pd.DataFrame()

    def get_contracts_df(self, snapshot_date=None):
        return self.contracts.copy()

    def get_latest_client_type_df(self, snapshot_date=None):
        return self.client_type.copy()


def contracts_df() -> pd.DataFrame:
    now = datetime(2025, 6, 14, 9, 30, tzinfo=timezone.utc)
    return pd.DataFrame(
        [
            {
                "ins_code": 1001,
                "option_type": "call",
                "symbol": "ضخود",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_short_name": "خودرو",
                "underlying_last_price": 1200.0,
                "underlying_closing_price": 1190.0,
                "end_date": 20250630,
                "strike_price": 1000.0,
                "trade_volume": 100.0,
                "trade_value": 25_000.0,
                "buy_open_positions": 11.0,
                "sell_open_positions": 11.0,
                "yesterday_open_positions": 10.0,
                "moneyness": "ITM",
                "updated_at": now,
            },
            {
                "ins_code": 1002,
                "option_type": "put",
                "symbol": "طخود",
                "underlying_ins_code": 2001,
                "underlying_symbol": "خودرو",
                "underlying_short_name": "خودرو",
                "underlying_last_price": 1200.0,
                "underlying_closing_price": 1190.0,
                "end_date": 20250731,
                "strike_price": 1300.0,
                "trade_volume": 50.0,
                "trade_value": 1_250.0,
                "buy_open_positions": 22.0,
                "sell_open_positions": 22.0,
                "yesterday_open_positions": 20.0,
                "moneyness": "ITM",
                "updated_at": now,
            },
        ]
    )


def client_type_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ins_code": 1001,
                "natural_buy_volume": 80.0,
                "natural_sell_volume": 20.0,
                "legal_buy_volume": 10.0,
                "legal_sell_volume": 15.0,
                "natural_money_flow": 1_000.0,
                "legal_money_flow": -100.0,
            },
            {
                "ins_code": 1002,
                "natural_buy_volume": 10.0,
                "natural_sell_volume": 90.0,
                "legal_buy_volume": 5.0,
                "legal_sell_volume": 20.0,
                "natural_money_flow": -250.0,
                "legal_money_flow": 50.0,
            },
        ]
    )


def test_serialize_value_keeps_large_identifier_precision_as_string() -> None:
    assert _serialize_value(12345678901234567890, "ins_code") == "12345678901234567890"
    assert _serialize_value(123.0, "underlying_ins_code") == "123"
    assert _serialize_value(float("nan"), "ins_code") is None
    assert _serialize_value(datetime(2025, 6, 14, tzinfo=timezone.utc)).startswith("2025-06-14")
    assert _code_to_string(1001.0) == "1001"


def test_get_underlyings_groups_and_sums_numeric_fields() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_underlyings(storage)
    item = result["items"][0]

    assert result["total"] == 1
    assert item["underlying_ins_code"] == "2001"
    assert item["contract_count"] == 2
    assert item["call_count"] == 1
    assert item["put_count"] == 1
    assert item["nearest_end_date"] == 20250630
    assert item["latest_end_date"] == 20250731
    assert item["min_strike_price"] == pytest.approx(1000.0)
    assert item["max_strike_price"] == pytest.approx(1300.0)
    assert item["trade_volume"] == pytest.approx(150.0)
    assert item["trade_value"] == pytest.approx(26_250.0)
    assert item["open_interest"] == pytest.approx(33.0)
    assert item["natural_money_flow"] == pytest.approx(750.0)
    assert item["legal_money_flow"] == pytest.approx(-50.0)


def test_get_underlying_contracts_filters_by_key_and_persian_query() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_underlying_contracts(storage, "2001", q="خود")

    assert result["total"] == 2
    assert result["underlying"]["underlying_ins_code"] == "2001"
    assert {row["ins_code"] for row in result["items"]} == {"1001", "1002"}


def test_get_summary_totals_are_exact() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    summary = get_summary(storage)

    assert summary["contract_count"] == 2
    assert summary["underlying_count"] == 1
    assert summary["call_count"] == 1
    assert summary["put_count"] == 1
    assert summary["total_trade_volume"] == pytest.approx(150.0)
    assert summary["total_trade_value"] == pytest.approx(26_250.0)
    assert summary["total_natural_flow"] == pytest.approx(750.0)
    assert summary["total_legal_flow"] == pytest.approx(-50.0)
    assert summary["total_buy_oi"] == pytest.approx(33.0)
    assert summary["total_sell_oi"] == pytest.approx(33.0)


def test_get_sentiment_filter_keeps_summary_unfiltered_but_items_filtered() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_sentiment(storage, q="خودرو")

    assert result["total"] == 2
    assert result["summary"]["group_count"] == 2
    assert {item["underlying_ins_code"] for item in result["items"]} == {2001}


def test_get_underlying_trend_computes_participant_scores() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_underlying_trend(storage, "2001", ["2025-06-14"])
    natural = result["items"][0]["people"]["natural"]

    assert result["total"] == 1
    assert natural["call_buy"] == pytest.approx(80.0)
    assert natural["call_sell"] == pytest.approx(20.0)
    assert natural["put_buy"] == pytest.approx(10.0)
    assert natural["put_sell"] == pytest.approx(90.0)
    assert natural["call_put_ratio"] == pytest.approx(100.0 / 100.0)
    assert natural["open_interest"] == pytest.approx(33.0)
    assert natural["open_interest_change"] == pytest.approx(3.0)
