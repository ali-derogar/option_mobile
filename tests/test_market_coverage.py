"""Regression coverage for the full options market, including Farabourse."""

from unittest.mock import MagicMock

import pytest

from options.backend import pipeline
from options.backend.config import TSETMC_REQUEST_TIMEOUT
from options.backend.services import public_options, watchlist
from options.backend.storage import Storage

BASAMA_CODE = 41625340598198551
BASAMA_CALL = 24139063101391669
BASAMA_PUT = 67241904692647336
OTHER_FARABOURSE_CODE = 2003


@pytest.fixture
def market_feed(monkeypatch):
    bourse = [{
        "uaInsCode": "2001", "lval30_UA": "خودرو", "insCode_C": "101",
        "lVal18AFC_C": "ضخود", "qTotTran5J_C": 100,
    }]
    farabourse = [
        {
            "uaInsCode": str(BASAMA_CODE), "lval30_UA": "بساما",
            "insCode_C": str(BASAMA_CALL), "insCode_P": str(BASAMA_PUT),
            "lVal18AFC_C": "ضبساما922", "lVal18AFC_P": "طبساما922",
            "qTotTran5J_C": 0, "qTotTran5J_P": 0,
        },
        {
            "uaInsCode": str(OTHER_FARABOURSE_CODE), "lval30_UA": "فزر",
            "insCode_C": "301", "lVal18AFC_C": "ضفزر", "qTotTran5J_C": 20,
        },
    ]
    session = MagicMock()

    def get(url, **kwargs):
        market = url.rsplit("/", 1)[-1]
        response = MagicMock()
        response.json.return_value = {"instrumentOptMarketWatch": {"0": bourse + farabourse, "1": bourse, "2": farabourse}[market]}
        return response

    session.get.side_effect = get
    monkeypatch.setattr(public_options, "_session", lambda: session)
    return session


def test_option_watch_requests_all_markets_including_basama(market_feed):
    rows = public_options.fetch_public_option_market_watch()
    assert {row["lval30_UA"] for row in rows} == {"خودرو", "بساما", "فزر"}
    market_feed.get.assert_called_once_with(
        "https://cdn.tsetmc.com/api/Instrument/GetInstrumentOptionMarketWatch/0",
        timeout=TSETMC_REQUEST_TIMEOUT,
    )


def test_catalog_includes_farabo_assets_even_when_their_trade_volume_is_zero(market_feed, monkeypatch):
    monkeypatch.setattr(watchlist, "fetch_underlying_volume", lambda _: (0, "20261021"))
    rows, failures = watchlist.fetch_catalog()
    indexed = {row["symbol"]: row for row in rows}
    assert set(indexed) == {"خودرو", "بساما", "فزر"}
    assert indexed["بساما"]["ins_code"] == BASAMA_CODE
    assert indexed["بساما"]["options_trade_volume"] == 0
    assert indexed["بساما"]["underlying_trade_volume"] == 0
    assert failures == 0


def test_full_catalog_refresh_expands_existing_bourse_cache_without_losing_selection(
    market_feed, monkeypatch, tmp_path,
):
    storage = Storage(tmp_path / "options.db")
    monkeypatch.setattr(watchlist, "fetch_underlying_volume", lambda _: (0, "20261021"))
    try:
        storage.replace_underlying_catalog([{
            "ins_code": 2001, "symbol": "خودرو", "options_trade_volume": 100,
        }])
        storage.replace_watchlist([2001])
        job = watchlist.CatalogRefresh()
        assert job.begin()
        job.run(storage)
        assert job.status()["last_error"] is None
        indexed = {row["ins_code"]: row for row in storage.get_underlying_catalog()}
        assert set(indexed) == {"2001", str(BASAMA_CODE), str(OTHER_FARABOURSE_CODE)}
        assert all(row["available"] for row in indexed.values())
        assert indexed[str(BASAMA_CODE)]["options_trade_volume"] == 0
        assert storage.get_watchlist_codes() == [2001]
        watchlist.save_watchlist(storage, [2001, BASAMA_CODE])
        assert storage.get_watchlist_codes() == [2001, BASAMA_CODE]
    finally:
        storage.engine.dispose()


@pytest.mark.parametrize("selected, expected_contracts", [
    ({BASAMA_CODE}, {BASAMA_CALL, BASAMA_PUT}),
    ({2001, BASAMA_CODE, OTHER_FARABOURSE_CODE}, {101, BASAMA_CALL, BASAMA_PUT, 301}),
])
def test_refresh_stores_selected_contracts_from_both_markets(
    market_feed, monkeypatch, tmp_path, selected, expected_contracts,
):
    storage = Storage(tmp_path / "options.db")
    monkeypatch.setattr(pipeline, "Storage", lambda: storage)
    monkeypatch.setattr(pipeline, "validate_credentials", MagicMock(side_effect=ValueError("no credentials")))
    details = MagicMock(return_value={})
    clients = MagicMock(return_value=[])
    monkeypatch.setattr(pipeline, "_fetch_direct_instrument_info", details)
    monkeypatch.setattr(pipeline, "fetch_public_client_type_current_many", clients)
    try:
        result = pipeline.run_pipeline(underlying_ins_codes=selected)
        assert result["options"] == len(expected_contracts)
        assert set(storage.get_contracts_df()["ins_code"]) == expected_contracts
        assert set(storage.get_contract_snapshot_df()["ins_code"]) == expected_contracts
        assert set(details.call_args.args[0]) == expected_contracts
        assert set(clients.call_args.args[0]) == expected_contracts
    finally:
        storage.engine.dispose()
