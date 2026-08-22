"""Service normalization tests with explicit numerical expectations."""

import pytest

from darush.backend.services.client_type import normalize_client_type
from darush.backend.services.historical_options import (
    _api_date,
    _jalali_expiry_to_gregorian_int,
    _normalize_historical_option,
    _parse_option_name,
)
from darush.backend.services.options import (
    enrich_with_instrument,
    enrich_with_underlying,
    normalize_option,
)
from darush.backend.services.public_options import (
    normalize_public_client_type,
    normalize_public_option_pairs,
)
from darush.backend.services.trades import normalize_trade


def test_normalize_option_preserves_all_numeric_fields() -> None:
    row = {
        "InsCode": "1001",
        "InstrumentID": "OPT001",
        "BuyOP": "500.5",
        "SellOP": 300,
        "YesterdayOP": None,
        "ContractSize": "1000",
        "StrikePrice": "12000.25",
        "UAInsCode": "2001",
        "BeginDate": "20250101",
        "EndDate": "20250630",
        "AFactor": "1.1",
        "BFactor": "2.2",
        "CFactor": "bad",
    }

    normalized = normalize_option(row)

    assert normalized["ins_code"] == 1001
    assert normalized["buy_open_positions"] == pytest.approx(500.5)
    assert normalized["sell_open_positions"] == pytest.approx(300.0)
    assert normalized["yesterday_open_positions"] is None
    assert normalized["contract_size"] == pytest.approx(1000.0)
    assert normalized["strike_price"] == pytest.approx(12000.25)
    assert normalized["underlying_ins_code"] == 2001
    assert normalized["begin_date"] == 20250101
    assert normalized["end_date"] == 20250630
    assert normalized["a_factor"] == pytest.approx(1.1)
    assert normalized["b_factor"] == pytest.approx(2.2)
    assert normalized["c_factor"] is None


def test_enrich_with_instrument_and_underlying_computes_option_numbers() -> None:
    option = normalize_option(
        {
            "InsCode": 1001,
            "InstrumentID": "OPT001",
            "StrikePrice": 12000,
            "UAInsCode": 2001,
        }
    )
    enriched = enrich_with_instrument(
        option,
        {
            "CValMne": "ضخود1230",
            "LVal18": "ضخود",
            "LVal30": "اختیار خرید خودرو",
            "CIsin": "IRTEST001",
            "YMarNSC": "بازار مشتقه",
            "CGdSVal": "خودرو",
        },
    )
    enriched = enrich_with_underlying(
        enriched,
        {"CValMne": "خودرو", "LVal18": "خودرو"},
        {"last_price": 12500, "closing_price": 12400},
    )

    assert enriched["option_type"] == "call"
    assert enriched["underlying_symbol"] == "خودرو"
    assert enriched["underlying_last_price"] == 12500
    assert enriched["moneyness"] == "ITM"
    assert enriched["intrinsic_value"] == pytest.approx(500.0)


def test_normalize_trade_keeps_prices_and_counts_in_expected_types() -> None:
    trade = normalize_trade(
        {
            "InsCode": "1001",
            "DEven": "20250614",
            "ZTotTran": "50",
            "QTotTran5J": "10000.5",
            "QTotCap": "120000000",
            "PClosing": "1200",
            "PDrCotVal": "1210",
            "PriceChange": "+10",
            "PriceMin": "1180",
            "PriceMax": "1220",
            "PriceFirst": "1190",
            "PriceYesterday": "1200",
        }
    )

    assert trade["ins_code"] == 1001
    assert trade["trade_date"] == 20250614
    assert trade["trade_count"] == 50
    assert trade["volume"] == pytest.approx(10000.5)
    assert trade["value"] == pytest.approx(120_000_000.0)
    assert trade["last_price"] == pytest.approx(1210.0)
    assert trade["price_change"] == "+10"


def test_normalize_client_type_money_flow_is_buy_minus_sell() -> None:
    normalized = normalize_client_type(
        {
            "RecDate": 20250614,
            "InsCode": 1001,
            "Buy_N_Volume": 1000,
            "Buy_I_Volume": 5000,
            "Buy_N_Value": 1_000_000,
            "Buy_I_Value": 5_000_000,
            "Buy_Count_ClientN": 10,
            "Buy_Count_ClientI": 2,
            "Sell_N_Volume": 800,
            "Sell_I_Volume": 4000,
            "Sell_N_Value": 1_250_000,
            "Sell_I_Value": 4_000_000,
            "Sell_Count_ClientN": 8,
            "Sell_Count_ClientI": 1,
        }
    )

    assert normalized["natural_money_flow"] == pytest.approx(-250_000.0)
    assert normalized["legal_money_flow"] == pytest.approx(1_000_000.0)
    assert normalized["natural_buy_count"] == 10
    assert normalized["legal_sell_count"] == 1


def test_normalize_client_type_missing_one_side_treats_it_as_zero() -> None:
    assert normalize_client_type({"Buy_N_Value": 500})["natural_money_flow"] == 500.0
    assert normalize_client_type({"Sell_N_Value": 500})["natural_money_flow"] == -500.0
    assert normalize_client_type({})["natural_money_flow"] is None


def test_public_option_pair_normalization_creates_call_and_put_with_exact_values() -> None:
    contracts = normalize_public_option_pairs(
        [
            {
                "insCode_C": 101,
                "insCode_P": 102,
                "lVal18AFC_C": "ضخود",
                "lVal18AFC_P": "طخود",
                "lVal30_C": "اختیار خرید خودرو",
                "lVal30_P": "اختیار فروش خودرو",
                "uaInsCode": 2001,
                "lval30_UA": "خودرو",
                "pDrCotVal_UA": 1200,
                "pClosing_UA": 1190,
                "strikePrice": 1000,
                "contractSize": 1000,
                "oP_C": 11,
                "oP_P": 22,
                "yesterdayOP_C": 10,
                "yesterdayOP_P": 20,
                "beginDate": 20250101,
                "endDate": 20250630,
                "pDrCotVal_C": 250,
                "pDrCotVal_P": 25,
                "pClosing_C": 240,
                "pClosing_P": 30,
                "priceYesterday_C": 200,
                "priceYesterday_P": 30,
                "qTotTran5J_C": 100,
                "qTotTran5J_P": 50,
                "qTotCap_C": 25_000,
                "qTotCap_P": 1_250,
                "zTotTran_C": 5,
                "zTotTran_P": 3,
            }
        ]
    )

    call, put = contracts
    assert call["ins_code"] == 101
    assert call["option_type"] == "call"
    assert call["moneyness"] == "ITM"
    assert call["intrinsic_value"] == pytest.approx(200.0)
    assert call["price_change"] == "+50"
    assert put["ins_code"] == 102
    assert put["option_type"] == "put"
    assert put["moneyness"] == "OTM"
    assert put["intrinsic_value"] == pytest.approx(0.0)
    assert put["price_change"] == "-5"


def test_public_option_pair_skips_missing_side() -> None:
    contracts = normalize_public_option_pairs([{"insCode_C": 101, "strikePrice": 1000}])

    assert len(contracts) == 1
    assert contracts[0]["option_type"] == "call"


def test_public_client_type_mapping_and_money_flow_are_explicit() -> None:
    normalized = normalize_public_client_type(
        {
            "recDate": 20250614,
            "insCode": 1001,
            "buy_I_Volume": 100,
            "buy_I_Value": 1_000,
            "buy_I_Count": 2,
            "sell_I_Volume": 80,
            "sell_I_Value": 750,
            "sell_I_Count": 1,
            "buy_N_Volume": 50,
            "buy_N_Value": 500,
            "buy_N_Count": 3,
            "sell_N_Volume": 70,
            "sell_N_Value": 900,
            "sell_N_Count": 4,
        }
    )

    assert normalized["natural_buy_value"] == pytest.approx(1_000.0)
    assert normalized["natural_money_flow"] == pytest.approx(250.0)
    assert normalized["legal_buy_value"] == pytest.approx(500.0)
    assert normalized["legal_money_flow"] == pytest.approx(-400.0)


def test_historical_option_parser_and_normalizer_compute_expected_numbers() -> None:
    option_row = {
        "insCode": 1001,
        "instrumentID": "OPT001",
        "lVal18AFC": "ضخود",
        "lVal30": "اختیارخ خودرو-1000-1404/03/31",
        "pDrCotVal": 25,
        "pClosing": 20,
        "priceChange": 5,
        "qTotTran5J": 100,
        "qTotCap": 2_500,
        "zTotTran": 10,
        "priceMin": 15,
        "priceMax": 30,
    }
    lookup = {
        "خودرو": {
            "insCode": 2001,
            "lVal18AFC": "خودرو",
            "lVal30": "خودرو",
            "pDrCotVal": 1200,
            "pClosing": 1190,
        }
    }

    parsed = _parse_option_name(option_row)
    normalized = _normalize_historical_option(option_row, lookup, {})

    assert parsed == {
        "option_type": "call",
        "underlying_symbol": "خودرو",
        "strike_price": "1000",
        "end_date": 20250621,
    }
    assert normalized is not None
    assert normalized["underlying_ins_code"] == 2001
    assert normalized["moneyness"] == "ITM"
    assert normalized["intrinsic_value"] == pytest.approx(200.0)
    assert normalized["price_change"] == "+5"
    assert normalized["trade_value"] == pytest.approx(2_500.0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1404/03/31", 20250621),
        ("04/03/31", 20250621),
        ("14040331", 20250621),
        ("040331", 20250621),
        ("bad", None),
    ],
)
def test_jalali_expiry_to_gregorian_int_known_cases(value, expected) -> None:
    assert _jalali_expiry_to_gregorian_int(value) == expected


def test_api_date_removes_dashes_only() -> None:
    assert _api_date("2025-06-14") == "20250614"
