"""API helper tests for frontend-facing numeric data."""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from options.backend.api.data import (
    _code_to_string,
    get_merged_contracts,
    _numeric_value,
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
    assert _serialize_value("۱٬۰۰۱", "ins_code") == "1001"
    assert _serialize_value(float("nan"), "ins_code") is None
    assert _serialize_value(float("inf"), "ins_code") is None
    assert _serialize_value(float("-inf"), "underlying_ins_code") is None
    assert _serialize_value(datetime(2025, 6, 14, tzinfo=timezone.utc)).startswith("2025-06-14")
    assert _code_to_string(1001.0) == "1001"


def test_serialize_value_handles_pandas_and_numpy_scalars() -> None:
    values = {
        "missing": _serialize_value(pd.NA),
        "nat": _serialize_value(pd.NaT),
        "integer": _serialize_value(np.int64(7)),
        "boolean": _serialize_value(np.bool_(True)),
    }

    assert values == {
        "missing": None,
        "nat": None,
        "integer": 7,
        "boolean": True,
    }
    json.dumps(values)


def test_serialize_value_and_numeric_helpers_reject_infinite_values() -> None:
    values = {
        "positive": _serialize_value(float("inf")),
        "negative": _serialize_value(float("-inf")),
    }

    assert values == {"positive": None, "negative": None}
    assert _numeric_value(float("inf")) == 0.0
    assert _numeric_value(float("-inf")) == 0.0
    json.dumps(values, allow_nan=False)


def test_numeric_value_treats_pandas_missing_values_as_zero() -> None:
    assert _numeric_value(pd.NA) == 0.0
    assert _numeric_value(pd.NaT) == 0.0
    assert _numeric_value("") == 0.0
    assert _numeric_value("2.5") == pytest.approx(2.5)


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
    assert item["updated_at"].startswith("2025-06-14")
    json.dumps(result)


def test_get_merged_contracts_normalizes_instrument_code_keys() -> None:
    client_type = client_type_df()
    client_type["ins_code"] = ["۱٬۰۰۱", "1,002"]
    storage = FakeStorage(contracts_df(), client_type)

    merged = get_merged_contracts(storage)

    assert merged["ins_code"].tolist() == [1001, 1002]
    assert merged["natural_money_flow"].tolist() == [1000.0, -250.0]


def test_get_merged_contracts_deduplicates_client_type_rows() -> None:
    contracts = contracts_df().iloc[[0]].copy()
    client_type = pd.concat(
        [
            client_type_df().iloc[[0]],
            client_type_df().iloc[[0]].assign(natural_money_flow=999.0),
        ],
        ignore_index=True,
    )
    storage = FakeStorage(contracts, client_type)

    merged = get_merged_contracts(storage)

    assert len(merged) == 1
    assert merged.iloc[0]["ins_code"] == 1001
    assert merged.iloc[0]["natural_money_flow"] == pytest.approx(999.0)


def test_get_underlyings_serializes_numpy_scalars_in_underlying_summary() -> None:
    contracts = contracts_df().astype({"underlying_last_price": object, "underlying_closing_price": object})
    contracts.loc[:, "underlying_last_price"] = np.int64(1200)
    contracts.loc[:, "underlying_closing_price"] = np.float64(1190.5)
    storage = FakeStorage(contracts, client_type_df())

    result = get_underlyings(storage)
    item = result["items"][0]

    assert item["underlying_last_price"] == 1200
    assert item["underlying_closing_price"] == pytest.approx(1190.5)
    json.dumps(result, allow_nan=False)


def test_get_underlying_contracts_filters_by_key_and_persian_query() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_underlying_contracts(storage, "2001", q="خود")

    assert result["total"] == 2
    assert result["underlying"]["underlying_ins_code"] == "2001"
    assert {row["ins_code"] for row in result["items"]} == {"1001", "1002"}


def test_get_underlying_contracts_serializes_numpy_scalars_in_underlying_metadata() -> None:
    contracts = contracts_df().astype({"underlying_last_price": object, "underlying_closing_price": object})
    contracts.loc[:, "underlying_last_price"] = np.int64(1200)
    contracts.loc[:, "underlying_closing_price"] = np.float64(1190.5)
    storage = FakeStorage(contracts, client_type_df())

    result = get_underlying_contracts(storage, "2001")

    assert result["underlying"]["underlying_last_price"] == 1200
    assert result["underlying"]["underlying_closing_price"] == pytest.approx(1190.5)
    json.dumps(result, allow_nan=False)


def test_get_underlying_contracts_accepts_persian_digit_code() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_underlying_contracts(storage, "۲۰۰۱")

    assert result["total"] == 2
    assert result["underlying"]["underlying_ins_code"] == "2001"


def test_get_underlying_contracts_accepts_grouped_persian_digit_code() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_underlying_contracts(storage, "۲٬۰۰۱")

    assert result["total"] == 2
    assert result["underlying"]["underlying_ins_code"] == "2001"


def test_get_underlying_contracts_matches_grouped_stored_underlying_code() -> None:
    df = contracts_df()
    df["underlying_ins_code"] = "۲٬۰۰۱"
    storage = FakeStorage(df, client_type_df())

    result = get_underlying_contracts(storage, "2001")

    assert result["total"] == 2
    assert result["underlying"]["underlying_ins_code"] == "2001"


def test_get_underlying_contracts_normalizes_symbol_lookup_key() -> None:
    df = contracts_df()
    df["underlying_ins_code"] = None
    df["underlying_symbol"] = "کیان"
    df["underlying_short_name"] = "کیان"
    storage = FakeStorage(df, client_type_df())

    result = get_underlying_contracts(storage, "كيان")

    assert result["total"] == 2
    assert result["underlying"]["underlying_key"] == "کیان"


def test_underlying_lookup_merges_symbol_rows_with_known_code() -> None:
    df = contracts_df()
    df.loc[1, "underlying_ins_code"] = None
    storage = FakeStorage(df, client_type_df())

    underlyings = get_underlyings(storage)
    by_code = get_underlying_contracts(storage, "2001")
    by_symbol = get_underlying_contracts(storage, "خودرو")
    summary = get_summary(storage)

    assert underlyings["total"] == 1
    assert underlyings["items"][0]["underlying_key"] == "2001"
    assert underlyings["items"][0]["contract_count"] == 2
    assert by_code["total"] == 2
    assert by_symbol["total"] == 2
    assert summary["underlying_count"] == 1


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


def test_get_summary_counts_underlyings_by_code_when_symbol_is_missing() -> None:
    df = contracts_df()
    df["underlying_symbol"] = None
    df["underlying_short_name"] = None
    storage = FakeStorage(df)

    summary = get_summary(storage)

    assert summary["underlying_count"] == 1


def test_api_aggregates_ignore_infinite_numeric_values() -> None:
    df = contracts_df()
    df.loc[0, "trade_volume"] = float("inf")
    df.loc[0, "strike_price"] = float("-inf")
    df["end_date"] = df["end_date"].astype(object)
    df.loc[1, "end_date"] = float("inf")
    client_type = client_type_df()
    client_type.loc[0, "natural_money_flow"] = float("inf")
    client_type.loc[0, "legal_money_flow"] = float("-inf")
    df.loc[0, "buy_open_positions"] = float("inf")
    df.loc[0, "sell_open_positions"] = float("-inf")
    storage = FakeStorage(df, client_type)

    underlyings = get_underlyings(storage)
    summary = get_summary(storage)

    assert underlyings["items"][0]["trade_volume"] == pytest.approx(50.0)
    assert underlyings["items"][0]["min_strike_price"] == pytest.approx(1300.0)
    assert underlyings["items"][0]["latest_end_date"] == 20250630
    assert summary["total_trade_volume"] == pytest.approx(50.0)
    assert summary["total_natural_flow"] == pytest.approx(-250.0)
    assert summary["total_legal_flow"] == pytest.approx(50.0)
    assert summary["total_buy_oi"] == pytest.approx(22.0)
    assert summary["total_sell_oi"] == pytest.approx(22.0)
    json.dumps(underlyings, allow_nan=False)
    json.dumps(summary, allow_nan=False)


def test_api_aggregates_accept_grouped_numeric_strings() -> None:
    df = contracts_df()
    df["trade_volume"] = ["۱۲٬۳۴۵", "1,000"]
    df["strike_price"] = ["۱٬۰۰۰", "1,300"]
    client_type = client_type_df()
    client_type["natural_money_flow"] = ["۱٬۰۰۰", "-250"]
    storage = FakeStorage(df, client_type)

    underlyings = get_underlyings(storage)
    summary = get_summary(storage)

    assert underlyings["items"][0]["trade_volume"] == pytest.approx(13345.0)
    assert underlyings["items"][0]["min_strike_price"] == pytest.approx(1000.0)
    assert underlyings["items"][0]["max_strike_price"] == pytest.approx(1300.0)
    assert summary["total_trade_volume"] == pytest.approx(13345.0)
    assert summary["total_natural_flow"] == pytest.approx(750.0)


def test_get_sentiment_filter_keeps_summary_unfiltered_but_items_filtered() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_sentiment(storage, q="خودرو")

    assert result["total"] == 2
    assert result["summary"]["group_count"] == 2
    assert {item["underlying_ins_code"] for item in result["items"]} == {2001}


def test_get_sentiment_filter_accepts_persian_digit_codes() -> None:
    storage = FakeStorage(contracts_df(), client_type_df())

    result = get_sentiment(storage, q="۲۰۰۱")

    assert result["total"] == 2
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


def test_get_underlying_trend_uses_available_open_interest_side() -> None:
    contracts = contracts_df()
    contracts.loc[0, "sell_open_positions"] = 15.0
    storage = FakeStorage(contracts, client_type_df())

    result = get_underlying_trend(storage, "2001", ["2025-06-14"])
    natural = result["items"][0]["people"]["natural"]

    assert natural["open_interest"] == pytest.approx(37.0)
    assert natural["open_interest_change"] == pytest.approx(7.0)


def test_get_underlying_trend_requires_current_and_yesterday_open_interest_for_change() -> None:
    contracts = contracts_df()
    contracts["yesterday_open_positions"] = None
    storage = FakeStorage(contracts, client_type_df())

    result = get_underlying_trend(storage, "2001", ["2025-06-14"])
    natural = result["items"][0]["people"]["natural"]

    assert natural["open_interest"] == pytest.approx(33.0)
    assert natural["yesterday_open_interest"] is None
    assert natural["open_interest_change"] is None
    assert natural["has_open_interest"] is False
