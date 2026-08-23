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
            "Sell_N_Value": 800_000,
            "Sell_I_Value": 4_000_000,
            "Sell_Count_ClientN": 8,
            "Sell_Count_ClientI": 1,
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
            "client_type_by_ins": mock_client_type,
        }
        return mapping.get(endpoint_key, [])

    client.call = mock_call

    with TemporaryDirectory() as tmp:
        storage = Storage(
            db_path=Path(tmp) / "test_options.db",
            export_dir=Path(tmp) / "exports",
        )
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage):
            result = run_pipeline(limit=None, skip_client_type=False, delay_between_calls=0)

    assert result["options"] == 1
    assert result["client_type"] == 1
    assert result["exports"]


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
        storage = Storage(
            db_path=Path(tmp) / "test_options.db",
            export_dir=Path(tmp) / "exports",
        )
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage):
            result = run_pipeline(limit=1, skip_client_type=True, delay_between_calls=0)

        contracts = storage.get_contracts_df()
        client_type = storage.get_latest_client_type_df()

    assert result["options"] == 1
    assert result["client_type"] == 0
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
        storage = Storage(
            db_path=Path(tmp) / "test_options.db",
            export_dir=Path(tmp) / "exports",
        )
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline.fetch_public_option_market_watch",
            return_value=public_rows,
        ), patch(
            "options.backend.pipeline.fetch_public_client_type_latest_many",
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
        storage = Storage(
            db_path=Path(tmp) / "test_options.db",
            export_dir=Path(tmp) / "exports",
        )
        with patch("options.backend.pipeline.validate_credentials"), patch(
            "options.backend.pipeline.TsetmcClient", return_value=client
        ), patch("options.backend.pipeline.Storage", return_value=storage), patch(
            "options.backend.pipeline.fetch_public_option_market_watch",
            return_value=[],
        ), patch(
            "options.backend.pipeline.fetch_public_client_type_latest_many"
        ) as fetch_client_type:
            result = run_pipeline(skip_client_type=True, delay_between_calls=0)

    assert result["source"] == "public_cdn"
    assert result["options"] == 0
    assert result["client_type"] == 0
    fetch_client_type.assert_not_called()


if __name__ == "__main__":
    test_pipeline_with_mock_api()
    print("Mock pipeline passed.")
