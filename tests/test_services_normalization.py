"""Service normalization tests with explicit numerical expectations."""

import pytest

from options.backend.services import historical_options, public_options
from options.backend.services.client_type import (
    fetch_client_type_all,
    fetch_client_type_by_ins,
    filter_for_ins_codes as filter_client_type_for_ins_codes,
    normalize_client_type,
)
from options.backend.services.historical_options import (
    _api_date,
    _jalali_expiry_to_gregorian_int,
    _normalize_historical_option,
    _parse_option_name,
)
from options.backend.services.instruments import (
    fetch_instruments,
    filter_by_ins_codes as filter_instruments_by_ins_codes,
    index_by_ins_code,
)
from options.backend.services.options import (
    enrich_with_instrument,
    enrich_with_underlying,
    fetch_all_options,
    normalize_option,
)
from options.backend.services.public_options import (
    fetch_public_client_type_latest,
    fetch_public_client_type_latest_many,
    fetch_public_option_market_watch,
    normalize_public_client_type,
    normalize_public_option_pairs,
)
from options.backend.services.trades import (
    fetch_trade_last_day,
    filter_for_ins_codes as filter_trades_for_ins_codes,
    normalize_trade,
)


class FakePublicResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakePublicSession:
    def __init__(self, payload):
        self.payload = payload

    def get(self, *args, **kwargs):
        return FakePublicResponse(self.payload)


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping

    def call(self, endpoint_key, json_body=None):
        return self.mapping.get(endpoint_key)


def test_authenticated_fetchers_skip_non_object_rows() -> None:
    client = FakeClient(
        {
            "option": ["bad", {"InsCode": 1001}],
            "instrument": [None, {"InsCode": 2001}],
            "trade_last_day": [1, {"InsCode": 1001}],
            "client_type_all": ["bad", {"InsCode": 1001}],
            "client_type_by_ins": ["bad", {"InsCode": 1001}],
        }
    )

    assert fetch_all_options(client) == [{"InsCode": 1001}]
    assert fetch_instruments(client) == [{"InsCode": 2001}]
    assert fetch_trade_last_day(client) == [{"InsCode": 1001}]
    assert fetch_client_type_all(client) == [{"InsCode": 1001}]
    assert fetch_client_type_by_ins(client, 1001) == [{"InsCode": 1001}]


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


def test_normalize_option_accepts_grouped_numeric_strings() -> None:
    normalized = normalize_option(
        {
            "InsCode": "1,001",
            "BuyOP": "۱۲٬۳۴۵",
            "StrikePrice": "12,000",
            "EndDate": "2,025,0630",
        }
    )

    assert normalized["ins_code"] == 1001
    assert normalized["buy_open_positions"] == pytest.approx(12345.0)
    assert normalized["strike_price"] == pytest.approx(12000.0)
    assert normalized["end_date"] == 20250630


def test_normalize_option_tolerates_empty_integer_fields() -> None:
    normalized = normalize_option(
        {
            "InsCode": "",
            "UAInsCode": "",
            "BeginDate": "",
            "EndDate": "",
        }
    )

    assert normalized["ins_code"] == 0
    assert normalized["underlying_ins_code"] == 0
    assert normalized["begin_date"] == 0
    assert normalized["end_date"] == 0


def test_normalize_option_rejects_non_finite_numbers() -> None:
    normalized = normalize_option(
        {
            "InsCode": float("inf"),
            "BuyOP": "Infinity",
            "StrikePrice": "NaN",
        }
    )

    assert normalized["ins_code"] == 0
    assert normalized["buy_open_positions"] is None
    assert normalized["strike_price"] is None


def test_normalize_option_rejects_fractional_integer_fields() -> None:
    normalized = normalize_option({"InsCode": 1001.5, "BeginDate": 20250101.5})

    assert normalized["ins_code"] == 0
    assert normalized["begin_date"] == 0


def test_normalize_option_rejects_negative_integer_fields() -> None:
    normalized = normalize_option({"InsCode": -1, "UAInsCode": -2, "BeginDate": -20250101})

    assert normalized["ins_code"] == 0
    assert normalized["underlying_ins_code"] == 0
    assert normalized["begin_date"] == 0


def test_instrument_index_and_filter_skip_bad_codes() -> None:
    rows = [
        "bad-row",
        {"InsCode": "bad"},
        {"InsCode": ""},
        {"InsCode": True},
        {"InsCode": -1},
        {"InsCode": 1001.5},
        {"InsCode": float("inf")},
        {"InsCode": "1001"},
    ]

    assert index_by_ins_code(rows) == {1001: {"InsCode": "1001"}}
    assert filter_instruments_by_ins_codes(rows, {1001}) == [{"InsCode": "1001"}]


def test_instrument_index_and_filter_accept_grouped_codes() -> None:
    rows = [
        {"InsCode": "۱٬۰۰۱", "symbol": "fa"},
        {"InsCode": "1,002", "symbol": "en"},
    ]

    assert index_by_ins_code(rows) == {
        1001: {"InsCode": "۱٬۰۰۱", "symbol": "fa"},
        1002: {"InsCode": "1,002", "symbol": "en"},
    }
    assert filter_instruments_by_ins_codes(rows, {1002}) == [
        {"InsCode": "1,002", "symbol": "en"}
    ]


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


def test_enrich_with_underlying_preserves_zero_last_price() -> None:
    option = normalize_option(
        {
            "InsCode": 1001,
            "InstrumentID": "OPT001",
            "StrikePrice": 100,
            "UAInsCode": 2001,
        }
    )
    enriched = enrich_with_instrument(option, {"CValMne": "طخود", "LVal30": "اختیار فروش خودرو"})
    enriched = enrich_with_underlying(
        enriched,
        {"CValMne": "خودرو", "LVal18": "خودرو"},
        {"last_price": 0, "closing_price": 120},
    )

    assert enriched["underlying_last_price"] == 0
    assert enriched["moneyness"] == "ITM"
    assert enriched["intrinsic_value"] == pytest.approx(100.0)


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


def test_normalize_trade_accepts_grouped_numeric_strings() -> None:
    trade = normalize_trade(
        {
            "InsCode": "1,001",
            "ZTotTran": "1,234",
            "QTotTran5J": "۱۲٬۳۴۵",
            "QTotCap": "12,000,000",
            "PClosing": "۱٬۲۰۰",
        }
    )

    assert trade["ins_code"] == 1001
    assert trade["trade_count"] == 1234
    assert trade["volume"] == pytest.approx(12345.0)
    assert trade["value"] == pytest.approx(12_000_000.0)
    assert trade["closing_price"] == pytest.approx(1200.0)


def test_trade_normalization_and_filter_tolerate_bad_instrument_codes() -> None:
    assert normalize_trade({"InsCode": "", "DEven": ""})["ins_code"] == 0
    bad_trade = normalize_trade({"InsCode": float("inf"), "DEven": 20250101.5})
    assert bad_trade["ins_code"] == 0
    assert bad_trade["trade_date"] == 0
    negative_trade = normalize_trade({"InsCode": -1, "DEven": -20250101, "ZTotTran": -5})
    assert negative_trade["ins_code"] == 0
    assert negative_trade["trade_date"] == 0
    assert negative_trade["trade_count"] is None
    assert filter_trades_for_ins_codes(
        [
            "bad-row",
            {"InsCode": "bad"},
            {"InsCode": ""},
            {"InsCode": True},
            {"InsCode": -1},
            {"InsCode": 1001.5},
            {"InsCode": "1001"},
        ],
        {1001},
    ) == [{"InsCode": "1001"}]


def test_trade_normalization_rejects_non_finite_numbers() -> None:
    trade = normalize_trade({"InsCode": "1001", "QTotTran5J": "Infinity", "PClosing": "NaN"})

    assert trade["volume"] is None
    assert trade["closing_price"] is None


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


def test_normalize_client_type_accepts_grouped_numeric_strings() -> None:
    normalized = normalize_client_type(
        {
            "InsCode": "1,001",
            "Buy_N_Value": "۱۲٬۰۰۰",
            "Sell_N_Value": "2,000",
            "Buy_Count_ClientN": "1,234",
        }
    )

    assert normalized["ins_code"] == 1001
    assert normalized["natural_buy_value"] == pytest.approx(12000.0)
    assert normalized["natural_sell_value"] == pytest.approx(2000.0)
    assert normalized["natural_money_flow"] == pytest.approx(10000.0)
    assert normalized["natural_buy_count"] == 1234


def test_normalize_client_type_missing_one_side_treats_it_as_zero() -> None:
    assert normalize_client_type({"Buy_N_Value": 500})["natural_money_flow"] == 500.0
    assert normalize_client_type({"Sell_N_Value": 500})["natural_money_flow"] == -500.0
    assert normalize_client_type({})["natural_money_flow"] is None


def test_client_type_normalization_and_filter_tolerate_bad_instrument_codes() -> None:
    assert normalize_client_type({"InsCode": "", "RecDate": ""})["ins_code"] == 0
    bad_client_type = normalize_client_type({"InsCode": float("inf"), "RecDate": 20250101.5})
    assert bad_client_type["ins_code"] == 0
    assert bad_client_type["rec_date"] == 0
    negative_client_type = normalize_client_type(
        {"InsCode": -1, "RecDate": -20250101, "Buy_Count_ClientN": -5}
    )
    assert negative_client_type["ins_code"] == 0
    assert negative_client_type["rec_date"] == 0
    assert negative_client_type["natural_buy_count"] is None
    assert filter_client_type_for_ins_codes(
        [
            "bad-row",
            {"InsCode": "bad"},
            {"InsCode": ""},
            {"InsCode": True},
            {"InsCode": -1},
            {"InsCode": 1001.5},
            {"InsCode": "1001"},
        ],
        {1001},
    ) == [{"InsCode": "1001"}]


def test_client_type_normalization_rejects_non_finite_numbers() -> None:
    normalized = normalize_client_type(
        {
            "InsCode": "1001",
            "Buy_N_Value": "Infinity",
            "Sell_N_Value": "NaN",
        }
    )

    assert normalized["natural_buy_value"] is None
    assert normalized["natural_sell_value"] is None
    assert normalized["natural_money_flow"] is None


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


def test_public_option_pair_accepts_grouped_numeric_strings() -> None:
    contracts = normalize_public_option_pairs(
        [
            {
                "insCode_C": "1,001",
                "strikePrice": "12,000",
                "pDrCotVal_UA": "۱۲٬۵۰۰",
                "qTotTran5J_C": "1,234",
                "zTotTran_C": "۱۲۳",
            }
        ]
    )

    assert contracts[0]["ins_code"] == 1001
    assert contracts[0]["strike_price"] == pytest.approx(12000.0)
    assert contracts[0]["underlying_last_price"] == pytest.approx(12500.0)
    assert contracts[0]["trade_volume"] == pytest.approx(1234.0)
    assert contracts[0]["trade_count"] == 123


def test_public_option_pair_skips_missing_side() -> None:
    contracts = normalize_public_option_pairs([{"insCode_C": 101, "strikePrice": 1000}])

    assert len(contracts) == 1
    assert contracts[0]["option_type"] == "call"


def test_public_option_fetchers_tolerate_unexpected_json_shapes(monkeypatch) -> None:
    monkeypatch.setattr(public_options, "_session", lambda: FakePublicSession(["bad", {"insCode_C": 101}]))

    assert fetch_public_option_market_watch() == [{"insCode_C": 101}]

    monkeypatch.setattr(public_options, "_session", lambda: FakePublicSession(["not-a-dict"]))

    assert fetch_public_client_type_latest(1001) is None


def test_public_client_type_latest_skips_non_object_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        public_options,
        "_session",
        lambda: FakePublicSession(
            {
                "clientType": [
                    "bad",
                    {"insCode": 1001, "recDate": 20250101, "buy_N_Value": 10},
                    {"insCode": 1001, "recDate": 20250102, "buy_N_Value": 20},
                ]
            }
        ),
    )

    normalized = fetch_public_client_type_latest(1001)

    assert normalized is not None
    assert normalized["rec_date"] == 20250102
    assert normalized["natural_buy_value"] == pytest.approx(20.0)


def test_public_client_type_many_skips_invalid_json_response(monkeypatch) -> None:
    def fake_latest(ins_code: int):
        if ins_code == 1:
            raise ValueError("bad json")
        return {"ins_code": ins_code, "natural_money_flow": 10}

    monkeypatch.setattr(public_options, "fetch_public_client_type_latest", fake_latest)

    assert fetch_public_client_type_latest_many([1, 2]) == [
        {"ins_code": 2, "natural_money_flow": 10}
    ]


def test_public_option_pair_rejects_non_finite_numbers() -> None:
    contracts = normalize_public_option_pairs(
        [
            {
                "insCode_C": 101,
                "insCode_P": float("inf"),
                "strikePrice": "Infinity",
                "pDrCotVal_UA": "NaN",
                "qTotTran5J_C": "Infinity",
            }
        ]
    )

    assert len(contracts) == 1
    assert contracts[0]["strike_price"] is None
    assert contracts[0]["underlying_last_price"] is None
    assert contracts[0]["trade_volume"] is None


def test_public_option_pair_rejects_negative_instrument_codes() -> None:
    assert normalize_public_option_pairs([{"insCode_C": -1, "strikePrice": 1000}]) == []


def test_public_option_pair_preserves_zero_underlying_last_price() -> None:
    contracts = normalize_public_option_pairs(
        [
            {
                "insCode_P": 102,
                "lVal18AFC_P": "طخود",
                "lVal30_P": "اختیار فروش خودرو",
                "strikePrice": 100,
                "pDrCotVal_UA": 0,
                "pClosing_UA": 120,
                "qTotTran5J_P": 10,
            }
        ]
    )

    assert contracts[0]["underlying_last_price"] == 0
    assert contracts[0]["moneyness"] == "ITM"
    assert contracts[0]["intrinsic_value"] == pytest.approx(100.0)


def test_historical_option_rejects_non_finite_numbers() -> None:
    normalized = _normalize_historical_option(
        {
            "insCode": 101,
            "instrumentID": "101",
            "lVal18AFC": "ضخود",
            "lVal30": "اختیارخ خودرو-1000-1404/03/31",
            "pDrCotVal": "Infinity",
            "pClosing": "NaN",
            "qTotTran5J": "Infinity",
        },
        {
            "خودرو": {
                "insCode": 2001,
                "pDrCotVal": "NaN",
                "pClosing": "Infinity",
            }
        },
        {},
    )

    assert normalized is not None
    assert normalized["last_price"] is None
    assert normalized["closing_price"] is None
    assert normalized["trade_volume"] is None
    assert normalized["underlying_last_price"] is None
    assert normalized["underlying_closing_price"] is None


def test_historical_public_fetchers_tolerate_unexpected_json_shapes(monkeypatch) -> None:
    monkeypatch.setattr(
        historical_options,
        "_session",
        lambda: FakePublicSession(["bad", {"insCode": 101, "lVal30": "x"}]),
    )

    assert historical_options.fetch_instruments_history_in_day("20250614") == [
        {"insCode": 101, "lVal30": "x"}
    ]

    monkeypatch.setattr(historical_options, "_session", lambda: FakePublicSession(["not-a-dict"]))

    assert historical_options.fetch_public_client_type_for_date(1001, "20250614") is None


def test_historical_public_client_type_skips_non_object_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        historical_options,
        "_session",
        lambda: FakePublicSession(
            {
                "clientType": [
                    "bad",
                    {"insCode": 1001, "recDate": 20250613, "buy_N_Value": 10},
                    {"insCode": 1001, "recDate": 20250614, "buy_N_Value": 20},
                ]
            }
        ),
    )

    normalized = historical_options.fetch_public_client_type_for_date(1001, "20250614")

    assert normalized is not None
    assert normalized["rec_date"] == 20250614
    assert normalized["natural_buy_value"] == pytest.approx(20.0)


def test_historical_public_client_type_many_skips_invalid_json_response(monkeypatch) -> None:
    def fake_for_date(ins_code: int, api_date: str):
        if ins_code == 1:
            raise ValueError("bad json")
        return {"ins_code": ins_code, "rec_date": int(api_date), "natural_money_flow": 10}

    monkeypatch.setattr(historical_options, "fetch_public_client_type_for_date", fake_for_date)

    assert historical_options.fetch_public_client_type_for_date_many([1, 2], "20250614") == [
        {"ins_code": 2, "rec_date": 20250614, "natural_money_flow": 10}
    ]


def test_historical_option_preserves_zero_underlying_last_price() -> None:
    normalized = _normalize_historical_option(
        {
            "insCode": 101,
            "instrumentID": "101",
            "lVal18AFC": "طخود",
            "lVal30": "اختیارف خودرو-100-1404/03/31",
            "qTotTran5J": 10,
        },
        {
            "خودرو": {
                "insCode": 2001,
                "pDrCotVal": 0,
                "pClosing": 120,
            }
        },
        {},
    )

    assert normalized is not None
    assert normalized["underlying_last_price"] == 0
    assert normalized["moneyness"] == "ITM"
    assert normalized["intrinsic_value"] == pytest.approx(100.0)


def test_historical_option_accepts_grouped_instrument_codes() -> None:
    normalized = _normalize_historical_option(
        {
            "insCode": "۱٬۰۰۱",
            "instrumentID": "1001",
            "lVal18AFC": "ضخود",
            "lVal30": "اختیارخ خودرو-1000-1404/03/31",
        },
        {},
        {},
    )

    assert normalized is not None
    assert normalized["ins_code"] == 1001


def test_historical_option_rejects_negative_instrument_codes() -> None:
    normalized = _normalize_historical_option(
        {
            "insCode": -1,
            "lVal18AFC": "ضخود",
            "lVal30": "اختیارخ خودرو-1000-1404/03/31",
        },
        {},
        {},
    )

    assert normalized is None


def test_historical_public_client_type_accepts_grouped_rec_date(monkeypatch) -> None:
    monkeypatch.setattr(
        historical_options,
        "_session",
        lambda: FakePublicSession(
            {
                "clientType": [
                    {"insCode": "۱٬۰۰۱", "recDate": "۲۰۲۵۰۶۱۴", "buy_N_Value": "۱۲٬۰۰۰"},
                ]
            }
        ),
    )

    normalized = historical_options.fetch_public_client_type_for_date(1001, "20250614")

    assert normalized is not None
    assert normalized["ins_code"] == 1001
    assert normalized["rec_date"] == 20250614
    assert normalized["natural_buy_value"] == pytest.approx(12000.0)


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

    assert normalized["natural_buy_value"] == pytest.approx(500.0)
    assert normalized["natural_buy_count"] == 3
    assert normalized["natural_money_flow"] == pytest.approx(-400.0)
    assert normalized["legal_buy_value"] == pytest.approx(1_000.0)
    assert normalized["legal_sell_count"] == 1
    assert normalized["legal_money_flow"] == pytest.approx(250.0)


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


def test_historical_option_parser_accepts_spaces_around_separators() -> None:
    parsed = _parse_option_name(
        {
            "lVal18AFC": "ضخود",
            "lVal30": "اختیارخ خودرو - 1000 - 1404/03/31",
        }
    )

    assert parsed == {
        "option_type": "call",
        "underlying_symbol": "خودرو",
        "strike_price": "1000",
        "end_date": 20250621,
    }


def test_historical_option_parser_accepts_persian_digits() -> None:
    parsed = _parse_option_name(
        {
            "lVal18AFC": "ضخود",
            "lVal30": "اختیارخ خودرو-۱۰۰۰-۱۴۰۴/۰۳/۳۱",
        }
    )

    assert parsed == {
        "option_type": "call",
        "underlying_symbol": "خودرو",
        "strike_price": "۱۰۰۰",
        "end_date": 20250621,
    }


@pytest.mark.parametrize("strike_text", ["12,000", "۱۲٬۰۰۰"])
def test_historical_option_parser_accepts_grouped_strike_prices(strike_text: str) -> None:
    parsed = _parse_option_name(
        {
            "lVal18AFC": "ضخود",
            "lVal30": f"اختیارخ خودرو - {strike_text} - 1404/03/31",
        }
    )
    normalized = _normalize_historical_option(
        {
            "insCode": 1001,
            "lVal18AFC": "ضخود",
            "lVal30": f"اختیارخ خودرو - {strike_text} - 1404/03/31",
        },
        {},
        {},
    )

    assert parsed is not None
    assert parsed["strike_price"] == strike_text
    assert normalized is not None
    assert normalized["strike_price"] == pytest.approx(12000.0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1404/03/31", 20250621),
        ("۱۴۰۴/۰۳/۳۱", 20250621),
        ("04/03/31", 20250621),
        ("14040331", 20250621),
        ("۱۴۰۴۰۳۳۱", 20250621),
        ("040331", 20250621),
        ("۰۴۰۳۳۱", 20250621),
        ("1403/12/30", 20250320),
        ("bad", None),
        ("1404/13/01", None),
        ("1404/03/32", None),
        ("1404/07/31", None),
        ("1404/12/30", None),
        ("1404/12/31", None),
    ],
)
def test_jalali_expiry_to_gregorian_int_known_cases(value, expected) -> None:
    assert _jalali_expiry_to_gregorian_int(value) == expected


def test_api_date_removes_dashes_only() -> None:
    assert _api_date("2025-06-14") == "20250614"
    assert _api_date("۲۰۲۵-۰۶-۱۴") == "20250614"
