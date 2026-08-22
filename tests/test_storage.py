"""Storage tests for numeric persistence and exports."""

from pathlib import Path

import pandas as pd
import pytest

from darush.backend.storage import (
    ClientTypeStats,
    Contract,
    ContractSnapshot,
    MoneyFlowSnapshot,
    OpenInterestSnapshot,
    Storage,
)


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    return Storage(db_path=tmp_path / "options.db", export_dir=tmp_path / "exports")


def contract_payload(**overrides):
    payload = {
        "ins_code": 1001,
        "instrument_id": "OPT001",
        "option_type": "call",
        "symbol": "ضخود",
        "short_name": "ضخود",
        "long_name": "اختیار خرید خودرو",
        "isin": "IRTEST001",
        "buy_open_positions": 500.0,
        "sell_open_positions": 300.0,
        "yesterday_open_positions": 450.0,
        "contract_size": 1000.0,
        "strike_price": 12000.0,
        "underlying_ins_code": 2001,
        "underlying_symbol": "خودرو",
        "underlying_short_name": "خودرو",
        "underlying_last_price": 12500.0,
        "underlying_closing_price": 12400.0,
        "moneyness": "ITM",
        "intrinsic_value": 500.0,
        "begin_date": 20250101,
        "end_date": 20250630,
        "a_factor": 1.0,
        "b_factor": 2.0,
        "c_factor": 3.0,
        "market_name": "بازار مشتقه",
        "sector": "خودرو",
        "last_price": 1210.0,
        "closing_price": 1200.0,
        "price_change": "+10",
        "trade_volume": 10_000.0,
        "trade_value": 120_000_000.0,
        "trade_count": 50,
        "price_min": 1180.0,
        "price_max": 1220.0,
        "instrument_meta": {"source": "test"},
    }
    payload.update(overrides)
    return payload


def client_type_payload(**overrides):
    payload = {
        "ins_code": 1001,
        "rec_date": 20250614,
        "natural_buy_volume": 1000.0,
        "natural_buy_value": 1_000_000.0,
        "natural_buy_count": 10,
        "natural_sell_volume": 800.0,
        "natural_sell_value": 1_250_000.0,
        "natural_sell_count": 8,
        "legal_buy_volume": 5000.0,
        "legal_buy_value": 5_000_000.0,
        "legal_buy_count": 2,
        "legal_sell_volume": 4000.0,
        "legal_sell_value": 4_000_000.0,
        "legal_sell_count": 1,
        "natural_money_flow": -250_000.0,
        "legal_money_flow": 1_000_000.0,
    }
    payload.update(overrides)
    return payload


def test_upsert_contracts_inserts_updates_and_skips_missing_ins_code(storage: Storage) -> None:
    assert storage.upsert_contracts([contract_payload(), contract_payload(ins_code=0)]) == 1
    assert storage.upsert_contracts([contract_payload(strike_price=13000, intrinsic_value=0)]) == 1

    with storage.session() as session:
        rows = session.query(Contract).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.ins_code == 1001
        assert row.strike_price == pytest.approx(13_000.0)
        assert row.intrinsic_value == pytest.approx(0.0)
        assert row.trade_value == pytest.approx(120_000_000.0)
        assert '"source": "test"' in row.instrument_json


def test_insert_snapshot_open_interest_client_type_and_money_flow(storage: Storage) -> None:
    assert storage.insert_contract_snapshot([contract_payload()], snapshot_date="2025-06-14") == 1
    assert storage.insert_open_interest(
        [
            {
                "ins_code": 1001,
                "buy_open_positions": 500,
                "sell_open_positions": 300,
                "yesterday_open_positions": 450,
            }
        ]
    ) == 1
    assert storage.insert_client_type_stats([client_type_payload()]) == 1
    assert storage.insert_money_flow([client_type_payload()]) == 1

    with storage.session() as session:
        snapshot = session.query(ContractSnapshot).one()
        oi = session.query(OpenInterestSnapshot).one()
        client_type = session.query(ClientTypeStats).one()
        money_flow = session.query(MoneyFlowSnapshot).one()

        assert snapshot.snapshot_date == "2025-06-14"
        assert snapshot.strike_price == pytest.approx(12_000.0)
        assert oi.buy_open_positions == pytest.approx(500.0)
        assert oi.sell_open_positions == pytest.approx(300.0)
        assert oi.yesterday_open_positions == pytest.approx(450.0)
        assert client_type.natural_money_flow == pytest.approx(-250_000.0)
        assert client_type.legal_money_flow == pytest.approx(1_000_000.0)
        assert money_flow.natural_money_flow == pytest.approx(-250_000.0)


def test_get_latest_client_type_df_uses_rec_date_for_snapshot_date(storage: Storage) -> None:
    storage.insert_client_type_stats(
        [
            client_type_payload(rec_date=20250613, natural_money_flow=111),
            client_type_payload(rec_date=20250614, natural_money_flow=222),
        ]
    )

    df = storage.get_latest_client_type_df(snapshot_date="2025-06-14")

    assert len(df) == 1
    assert df.iloc[0]["rec_date"] == 20250614
    assert df.iloc[0]["natural_money_flow"] == pytest.approx(222.0)


def test_export_csv_writes_numeric_tables(storage: Storage) -> None:
    storage.upsert_contracts([contract_payload()])
    storage.insert_open_interest(
        [
            {
                "ins_code": 1001,
                "buy_open_positions": 500,
                "sell_open_positions": 300,
                "yesterday_open_positions": 450,
            }
        ]
    )
    storage.insert_client_type_stats([client_type_payload()])

    paths = storage.export_csv(prefix="unit")

    assert set(paths) == {"contracts", "client_type_stats", "money_flow", "open_interest"}
    contracts = pd.read_csv(paths["contracts"])
    client_type = pd.read_csv(paths["client_type_stats"])
    money_flow = pd.read_csv(paths["money_flow"])
    open_interest = pd.read_csv(paths["open_interest"])

    assert contracts.loc[0, "strike_price"] == pytest.approx(12_000.0)
    assert contracts.loc[0, "trade_value"] == pytest.approx(120_000_000.0)
    assert client_type.loc[0, "natural_money_flow"] == pytest.approx(-250_000.0)
    assert money_flow.loc[0, "legal_money_flow"] == pytest.approx(1_000_000.0)
    assert open_interest.loc[0, "buy_open_positions"] == pytest.approx(500.0)


def test_snapshot_date_for_uses_tehran_market_day() -> None:
    from datetime import datetime, timezone

    utc_dt = datetime(2025, 6, 14, 21, 0, tzinfo=timezone.utc)

    assert Storage.snapshot_date_for(utc_dt) == "2025-06-15"
