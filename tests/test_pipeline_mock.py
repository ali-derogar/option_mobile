"""Mock end-to-end pipeline test (no live API credentials required)."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from options.backend.client import TsetmcAPIError
from options.backend.pipeline import run_pipeline
from options.backend.storage import Storage


def test_pipeline_with_mock_api() -> None:
    mock_options = [
        {
            "InsCode": 1001,
            "InstrumentID": "OPT001",
            "BuyOP": 500,
            "SellOP": 300,
            "YesterdayOP": 450,
            "ContractSize": 1000,
            "StrikePrice": 12000,
            "UAInsCode": 2001,
            "BeginDate": 20250101,
            "EndDate": 20250630,
            "AFactor": 1.0,
            "BFactor": 1.0,
            "CFactor": 1.0,
        }
    ]
    mock_instruments = [
        {
            "InsCode": 1001,
            "CValMne": "ضخود1230",
            "LVal18": "اختیارخ",
            "LVal30": "اختیار خرید خودرو",
            "CIsin": "IRTEST001",
            "YMarNSC": "بازار مشتقه",
            "CGdSVal": "خودرو",
        }
    ]
    mock_client_type = [
        {
            "ins_code": 1001,
            "natural_buy_volume": 5000,
            "natural_sell_volume": 4000,
            "natural_buy_count": 2,
            "natural_sell_count": 1,
            "legal_buy_volume": 1000,
            "legal_sell_volume": 800,
            "legal_buy_count": 10,
            "legal_sell_count": 8,
        }
    ]
    mock_trades = [
        {
            "InsCode": 1001,
            "DEven": 20250614,
            "LVal18AFC": "اختیارخ",
            "LVal30": "اختیار خرید",
            "ZTotTran": 50,
            "QTotTran5J": 10000,
            "QTotCap": 120_000_000,
            "PClosing": 1200,
            "PDrCotVal": 1210,
            "PriceChange": "+10",
            "PriceMin": 1180,
            "PriceMax": 1220,
            "PriceFirst": 1190,
            "PriceYesterday": 1200,
        }
    ]

    client = MagicMock()
    client.login.return_value = "mock-token"

    def mock_call(endpoint_key, json_body=None):
        mapping = {
            "option": mock_options,
            "instrument": mock_instruments,
            "trade_last_day": mock_trades,
        }
        return mapping.get(endpoint_key, [])

    client.call = mock_call

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage):
            with patch("options.backend.pipeline._fetch_direct_instrument_info", return_value={}), patch(
                "options.backend.pipeline.fetch_public_client_type_current_many",
                return_value=mock_client_type,
            ):
                result = run_pipeline(limit=None, skip_client_type=False, delay_between_calls=0)

    assert result["options"] == 1
    assert result["client_type"] == 1
    assert result["client_type_stats"] == 1
    assert result["money_flow"] == 0
    assert result["open_interest"] == 1


def test_pipeline_limit_and_skip_client_type_preserve_stored_numbers() -> None:
    mock_options = [
        {
            "InsCode": 1001,
            "InstrumentID": "CALL001",
            "BuyOP": 500,
            "SellOP": 300,
            "YesterdayOP": 450,
            "ContractSize": 1000,
            "StrikePrice": 12000,
            "UAInsCode": 2001,
            "BeginDate": 20250101,
            "EndDate": 20250630,
        },
        {
            "InsCode": 1002,
            "InstrumentID": "PUT001",
            "BuyOP": 700,
            "SellOP": 600,
            "YesterdayOP": 650,
            "ContractSize": 1000,
            "StrikePrice": 15000,
            "UAInsCode": 2001,
            "BeginDate": 20250101,
            "EndDate": 20250630,
        },
    ]
    mock_instruments = [
        {
            "InsCode": 1001,
            "CValMne": "ضخود1230",
            "LVal18": "ضخود1230",
            "LVal30": "اختیار خرید خودرو",
            "CGdSVal": "خودرو",
        },
        {
            "InsCode": 1002,
            "CValMne": "طخود1230",
            "LVal18": "طخود1230",
            "LVal30": "اختیار فروش خودرو",
            "CGdSVal": "خودرو",
        },
        {
            "InsCode": 2001,
            "CValMne": "خودرو",
            "LVal18": "خودرو",
            "LVal30": "ایران خودرو",
        },
    ]
    mock_trades = [
        {
            "InsCode": 1001,
            "DEven": 20250614,
            "ZTotTran": 50,
            "QTotTran5J": 10000,
            "QTotCap": 120_000_000,
            "PClosing": 1200,
            "PDrCotVal": 1210,
            "PriceChange": "+10",
            "PriceMin": 1180,
            "PriceMax": 1220,
        },
        {
            "InsCode": 1002,
            "DEven": 20250614,
            "ZTotTran": 10,
            "QTotTran5J": 200,
            "QTotCap": 5_000_000,
            "PClosing": 100,
            "PDrCotVal": 110,
            "PriceChange": "+10",
            "PriceMin": 90,
            "PriceMax": 120,
        },
        {
            "InsCode": 2001,
            "DEven": 20250614,
            "ZTotTran": 1000,
            "QTotTran5J": 1_000_000,
            "QTotCap": 12_500_000_000,
            "PClosing": 12400,
            "PDrCotVal": 12500,
            "PriceChange": "+100",
            "PriceMin": 12000,
            "PriceMax": 12600,
        },
    ]

    client = MagicMock()
    client.login.return_value = "mock-token"
    client.call.side_effect = lambda endpoint_key, json_body=None: {
        "option": mock_options,
        "instrument": mock_instruments,
        "trade_last_day": mock_trades,
        "client_type_by_ins": [],
    }.get(endpoint_key, [])

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage):
            with patch("options.backend.pipeline._fetch_direct_instrument_info", return_value={}):
                result = run_pipeline(limit=1, skip_client_type=True, delay_between_calls=0)

        contracts = storage.get_contracts_df()
        client_type = storage.get_latest_client_type_df()

    assert result["options"] == 1
    assert result["client_type"] == 0
    assert result["client_type_stats"] == 0
    assert result["money_flow"] == 0
    assert result["open_interest"] == 1
    assert len(contracts) == 1
    row = contracts.iloc[0]
    assert row["ins_code"] == 1001
    assert row["strike_price"] == 12000
    assert row["trade_volume"] == 10000
    assert row["trade_value"] == 120_000_000
    assert row["underlying_last_price"] == 12500
    assert row["moneyness"] == "ITM"
    assert row["intrinsic_value"] == 500
    assert client_type.empty
    assert client.call.call_args_list


def test_pipeline_limit_preserves_input_order() -> None:
    mock_options = [
        {
            "InsCode": ins_code,
            "InstrumentID": f"OPT{ins_code}",
            "BuyOP": 1,
            "SellOP": 1,
            "YesterdayOP": 1,
            "ContractSize": 1000,
            "StrikePrice": 1000,
        }
        for ins_code in (101, 102, 103, 104)
    ]

    client = MagicMock()
    client.login.return_value = "mock-token"
    client.call.side_effect = lambda endpoint_key, json_body=None: {
        "option": mock_options,
        "instrument": [],
        "trade_last_day": [],
        "client_type_by_ins": [],
    }.get(endpoint_key, [])

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage):
            with patch("options.backend.pipeline._fetch_direct_instrument_info", return_value={}):
                result = run_pipeline(limit=2, skip_client_type=True, delay_between_calls=0)

        contracts = storage.get_contracts_df()

    assert result["options"] == 2
    assert contracts["ins_code"].tolist() == [101, 102]


def test_pipeline_authenticated_path_prefers_direct_instrument_metadata() -> None:
    mock_options = [
        {
            "InsCode": 5800031174225610,
            "InstrumentID": "OPT5800031174225610",
            "BuyOP": 500,
            "SellOP": 300,
            "YesterdayOP": 450,
            "ContractSize": 1000,
            "StrikePrice": 18630,
            "UAInsCode": 35425587644337450,
            "BeginDate": 20250101,
            "EndDate": 20260930,
        }
    ]
    mock_instruments = [
        {
            "InsCode": 5800031174225610,
            "CValMne": "ضملي7070",
            "LVal18": "ضملي7070",
            "LVal30": "اختيارخ فملي-18630-1405/07/08",
        },
        {
            "InsCode": 35425587644337450,
            "CValMne": "فملي",
            "LVal18": "فملي",
        },
    ]
    direct_instrument_info = {
        5800031174225610: {
            "lVal18AFC": "ضملي7070",
            "lVal30": "اختيارخ فملي-26000-1405/07/08",
            "instrumentID": "IRO9FOLD7071",
        }
    }

    client = MagicMock()
    client.login.return_value = "mock-token"
    client.call.side_effect = lambda endpoint_key, json_body=None: {
        "option": mock_options,
        "instrument": mock_instruments,
        "trade_last_day": [],
        "client_type_by_ins": [],
    }.get(endpoint_key, [])

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline._fetch_direct_instrument_info",
            return_value=direct_instrument_info,
        ):
            result = run_pipeline(skip_client_type=True, delay_between_calls=0)

        contracts = storage.get_contracts_df()

    row = contracts.iloc[0]
    assert result["options"] == 1
    assert row["ins_code"] == 5800031174225610
    assert row["symbol"] == "ضملي7070"
    assert row["long_name"] == "اختيارخ فملي-26000-1405/07/08"
    assert row["strike_price"] == 26000
    assert row["end_date"] == 20260930
    assert row["instrument_id"] == "IRO9FOLD7071"


def test_pipeline_rejects_non_positive_limit() -> None:
    with patch("options.backend.pipeline.validate_credentials"):
        try:
            run_pipeline(limit=0, skip_client_type=True, delay_between_calls=0)
        except ValueError as exc:
            assert str(exc) == "limit must be positive"
        else:
            raise AssertionError("run_pipeline accepted a non-positive limit")


def test_pipeline_falls_back_to_public_cdn_when_login_fails() -> None:
    public_rows = [
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
    public_client_type = [
        {
            "ins_code": 101,
            "rec_date": 20250614,
            "natural_money_flow": 250,
            "legal_money_flow": -400,
        }
    ]
    client = MagicMock()
    client.login.side_effect = TsetmcAPIError("login failed")
    progress_events = []

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline.fetch_public_option_market_watch",
            return_value=public_rows,
        ), patch(
            "options.backend.pipeline.fetch_public_instrument_info_many",
            return_value={},
        ), patch(
            "options.backend.pipeline.fetch_public_client_type_current_many",
            return_value=public_client_type,
        ):
            result = run_pipeline(
                skip_client_type=False,
                delay_between_calls=0,
                progress_callback=progress_events.append,
            )

        contracts = storage.get_contracts_df()
        client_type = storage.get_latest_client_type_df()

    assert result["source"] == "public_cdn"
    assert result["options"] == 2
    assert result["client_type"] == 1
    assert result["client_type_stats"] == 1
    assert result["money_flow"] == 1
    assert result["open_interest"] == 2
    assert len(contracts) == 2
    assert sorted(contracts["ins_code"].tolist()) == [101, 102]
    assert contracts.loc[contracts["ins_code"] == 101, "intrinsic_value"].iloc[0] == 200
    assert len(client_type) == 1
    assert client_type.iloc[0]["natural_money_flow"] == 250
    assert [event["stage"] for event in progress_events][:2] == ["login", "login_failed"]


def test_pipeline_public_fallback_skip_client_type_does_not_fetch_client_type() -> None:
    client = MagicMock()
    client.login.side_effect = TsetmcAPIError("login failed")

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline.fetch_public_option_market_watch",
            return_value=[],
        ), patch(
            "options.backend.pipeline.fetch_public_instrument_info_many",
            return_value={},
        ), patch(
            "options.backend.pipeline.fetch_public_client_type_current_many"
        ) as fetch_client_type:
            result = run_pipeline(skip_client_type=True, delay_between_calls=0)

    assert result["source"] == "public_cdn"
    assert result["options"] == 0
    assert result["client_type"] == 0
    assert result["client_type_stats"] == 0
    assert result["money_flow"] == 0
    assert result["open_interest"] == 0
    assert result["warning"] is None
    fetch_client_type.assert_not_called()


def test_pipeline_public_fallback_when_credentials_are_missing() -> None:
    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials", side_effect=ValueError("missing")), patch(
            "options.backend.pipeline.TsetmcClient"
        ) as client_cls, patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline.fetch_public_option_market_watch",
            return_value=[],
        ), patch(
            "options.backend.pipeline.fetch_public_instrument_info_many",
            return_value={},
        ), patch(
            "options.backend.pipeline.fetch_public_client_type_current_many"
        ) as fetch_client_type:
            result = run_pipeline(skip_client_type=True, delay_between_calls=0)

    assert result["source"] == "public_cdn"
    assert result["options"] == 0
    assert result["open_interest"] == 0
    client_cls.assert_not_called()
    fetch_client_type.assert_not_called()


def test_pipeline_public_fallback_warns_when_client_type_rows_are_not_stored() -> None:
    public_rows = [
        {
            "insCode_C": 101,
            "lVal18AFC_C": "ضخود",
            "lVal30_C": "اختیار خرید خودرو",
            "strikePrice": 1000,
        }
    ]
    client = MagicMock()
    client.login.side_effect = TsetmcAPIError("login failed")

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline.fetch_public_option_market_watch",
            return_value=public_rows,
        ), patch(
            "options.backend.pipeline.fetch_public_instrument_info_many",
            return_value={},
        ), patch(
            "options.backend.pipeline.fetch_public_client_type_current_many",
            return_value=[{"ins_code": "bad", "natural_money_flow": 10}],
        ):
            result = run_pipeline(skip_client_type=False, delay_between_calls=0)

    assert result["options"] == 1
    assert result["client_type_stats"] == 0
    assert result["money_flow"] == 0
    assert result["warning"]


def test_pipeline_public_fallback_enriches_contract_metadata_from_direct_instrument_info() -> None:
    public_rows = [
        {
            "insCode_C": 101,
            "lVal18AFC_C": "ضملی7070",
            "lVal30_C": "اختیارخ فملی-18630-1405/07/08",
            "strikePrice": 18630,
            "uaInsCode": 2001,
            "pDrCotVal_UA": 25000,
            "pClosing_UA": 25000,
            "pDrCotVal_C": 1200,
            "pClosing_C": 1200,
        }
    ]
    instrument_info = {
        101: {
            "lVal18AFC": "ضملی7070",
            "lVal30": "اختیارخ فملی-26000-1405/07/08",
            "instrumentID": "IROPT101",
        }
    }
    client = MagicMock()
    client.login.side_effect = TsetmcAPIError("login failed")

    with TemporaryDirectory() as tmp:
        storage = Storage(db_path=Path(tmp) / "test_options.db")
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline.fetch_public_option_market_watch",
            return_value=public_rows,
        ), patch(
            "options.backend.pipeline.fetch_public_instrument_info_many",
            return_value=instrument_info,
        ), patch(
            "options.backend.pipeline.fetch_public_client_type_current_many",
            return_value=[],
        ):
            result = run_pipeline(skip_client_type=False, delay_between_calls=0)

        contracts = storage.get_contracts_df()

    row = contracts.iloc[0]
    assert result["options"] == 1
    assert row["long_name"] == "اختیارخ فملی-26000-1405/07/08"
    assert row["strike_price"] == 26000
    assert row["end_date"] == 20260930
    assert row["instrument_id"] == "IROPT101"


if __name__ == "__main__":
    test_pipeline_with_mock_api()
    print("Mock pipeline passed.")
