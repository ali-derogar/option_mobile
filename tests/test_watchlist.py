"""Persistent watchlist, independent catalogue, and scoped pipeline behavior."""

from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.testclient import TestClient

from options import app as app_module
from options.backend import pipeline
from options.backend.api import main
from options.backend.api.data import get_summary, get_watchlist_underlyings
from options.backend.services import watchlist
from options.backend.storage import Storage


def catalog_row(code=2001, **changes):
    return {
        "ins_code": code, "symbol": "خودرو" if code == 2001 else "اهرم",
        "name": "دارایی پایه", "options_trade_volume": 150.0,
        "underlying_trade_volume": 1000.0, "market_date": "20261021", **changes,
    }


def contract(code=1001, underlying=2001, volume=100.0):
    return {
        "ins_code": code, "underlying_ins_code": underlying,
        "symbol": f"ض{code}", "underlying_symbol": "خودرو" if underlying == 2001 else "اهرم",
        "option_type": "call", "trade_volume": volume, "trade_value": volume * 10 if volume is not None else None,
    }


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "options.db")
    yield db
    db.engine.dispose()


@pytest.fixture
def api_client(storage, monkeypatch):
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "catalog_refresh_job", watchlist.CatalogRefresh())
    monkeypatch.setattr(main, "_refresh_status", {**main._refresh_status, "running": False})
    monkeypatch.setattr(main, "_last_refresh_request_at", 0.0)
    storage.set_activated()
    with TestClient(main.app, headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN}) as client:
        yield client


def test_watchlist_starts_empty_even_with_existing_contracts(storage):
    storage.upsert_contracts([contract()])
    storage.insert_contract_snapshot([contract()])
    assert storage.get_watchlist_codes() == []
    assert get_watchlist_underlyings(storage)["items"] == []


def test_watchlist_persists_and_removal_keeps_history(storage):
    storage.replace_underlying_catalog([catalog_row()])
    storage.upsert_contracts([contract()])
    storage.insert_contract_snapshot([contract()])
    watchlist.save_watchlist(storage, [2001, 2001])
    reopened = Storage(storage.db_path)
    try:
        assert reopened.get_watchlist_codes() == [2001]
        assert reopened.get_underlying_catalog()[0]["ins_code"] == "2001"
        reopened.replace_watchlist([])
        assert len(reopened.get_contracts_df()) == 1
        assert len(reopened.get_contract_snapshot_df()) == 1
    finally:
        reopened.engine.dispose()


def test_catalog_refresh_marks_missing_assets_unavailable_and_retains_selection(storage):
    storage.replace_underlying_catalog([catalog_row(), catalog_row(2002)])
    watchlist.save_watchlist(storage, [2001])
    storage.replace_underlying_catalog([catalog_row(2002)])
    assert storage.get_watchlist_codes() == [2001]
    assert storage.get_underlying_catalog()[0]["available"] is False
    watchlist.save_watchlist(storage, [2001])
    watchlist.save_watchlist(storage, [])
    with pytest.raises(ValueError):
        watchlist.save_watchlist(storage, [2001])


def test_watchlist_rejects_unknown_codes_without_partial_changes(storage):
    storage.replace_underlying_catalog([catalog_row()])
    watchlist.save_watchlist(storage, [2001])
    with pytest.raises(ValueError):
        watchlist.save_watchlist(storage, [2001, 9999])
    assert storage.get_watchlist_codes() == [2001]


def test_watchlist_commit_failure_preserves_previous_selection(storage, monkeypatch):
    storage.replace_watchlist([2001])
    session_factory = storage.SessionLocal

    def failing_session():
        session = session_factory()
        session.commit = MagicMock(side_effect=OperationalError("commit", {}, Exception("disk full")))
        return session

    monkeypatch.setattr(storage, "SessionLocal", failing_session)
    with pytest.raises(OperationalError):
        storage.replace_watchlist([2002])
    assert storage.get_watchlist_codes() == [2001]


def test_catalog_database_failure_does_not_partially_replace_previous_catalog(storage):
    storage.replace_underlying_catalog([catalog_row(), catalog_row(2002)])
    storage.replace_watchlist([2001])
    previous = storage.get_underlying_catalog()
    with pytest.raises(IntegrityError):
        storage.replace_underlying_catalog([catalog_row(options_trade_volume=999), catalog_row(2003, symbol=None)])
    assert storage.get_underlying_catalog() == previous
    assert storage.get_watchlist_codes() == [2001]


def test_catalog_aggregation_deduplicates_and_preserves_missing_and_zero():
    rows = watchlist.build_catalog([
        contract(), contract(), contract(1002, volume=50),
        contract(1003, 2002, None), contract(1004, 2003, 0),
    ])
    indexed = {row["ins_code"]: row for row in rows}
    assert indexed[2001]["options_trade_volume"] == 150
    assert indexed[2002]["options_trade_volume"] is None
    assert indexed[2003]["options_trade_volume"] == 0


def test_catalog_volume_failure_is_partial(monkeypatch):
    monkeypatch.setattr(watchlist, "fetch_public_option_market_watch", lambda: [])
    monkeypatch.setattr(watchlist, "normalize_public_option_pairs", lambda _: [contract(), contract(1002, 2002)])
    def volume(code):
        if code == 2001:
            raise requests.Timeout()
        return 0, "20261021"
    monkeypatch.setattr(watchlist, "fetch_underlying_volume", volume)
    rows, failures = watchlist.fetch_catalog()
    indexed = {row["ins_code"]: row for row in rows}
    assert failures == 1
    assert indexed[2001]["underlying_trade_volume"] is None
    assert indexed[2002]["underlying_trade_volume"] == 0


@pytest.mark.parametrize("volume, expected", [(0, 0), (None, None), ("nan", None), (-1, None)])
def test_underlying_volume_normalizes_public_response(monkeypatch, volume, expected):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value.json.return_value = {"closingPriceInfo": {"qTotTran5J": volume, "dEven": 20261021}}
    monkeypatch.setattr(watchlist.requests, "Session", lambda: session)
    assert watchlist.fetch_underlying_volume(2001) == (expected, "20261021")


def test_underlying_volume_rejects_an_explicit_different_instrument(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value.json.return_value = {"closingPriceInfo": {
        "insCode": "2002", "qTotTran5J": 999, "dEven": 20261021,
    }}
    monkeypatch.setattr(watchlist.requests, "Session", lambda: session)
    with pytest.raises(ValueError):
        watchlist.fetch_underlying_volume(2001)


@pytest.mark.parametrize("market_date", [0, "20260230", "not-a-date"])
def test_catalog_does_not_publish_invalid_trade_dates(monkeypatch, market_date):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value.json.return_value = {"closingPriceInfo": {
        "insCode": "0", "qTotTran5J": "۱۲٬۳۴۵", "dEven": market_date,
    }}
    monkeypatch.setattr(watchlist.requests, "Session", lambda: session)
    assert watchlist.fetch_underlying_volume(2001) == (12345, None)


def test_catalog_total_volume_requires_every_unique_contract_to_have_a_value():
    rows = watchlist.build_catalog([contract(), contract(1002, volume=None)])
    assert rows[0]["options_trade_volume"] is None


def test_catalog_uses_the_same_last_duplicate_values_as_the_main_pipeline():
    rows = watchlist.build_catalog([contract(volume=100), contract(volume=200)])
    assert rows[0]["options_trade_volume"] == 200


def test_failed_catalog_refresh_preserves_previous_data(storage, monkeypatch):
    storage.replace_underlying_catalog([catalog_row()])
    storage.replace_watchlist([2001])
    old = storage.get_underlying_catalog()
    monkeypatch.setattr(watchlist, "fetch_catalog", MagicMock(side_effect=requests.Timeout()))
    job = watchlist.CatalogRefresh()
    assert job.begin()
    assert not job.begin()
    job.run(storage)
    assert job.status()["running"] is False
    assert job.status()["last_error"]
    assert storage.get_underlying_catalog() == old
    assert storage.get_watchlist_codes() == [2001]


def test_catalog_refresh_does_not_store_contracts_or_change_watchlist(api_client, storage, monkeypatch):
    monkeypatch.setattr(watchlist, "fetch_catalog", lambda: ([catalog_row()], 0))
    response = api_client.post("/api/underlyings/catalog/refresh")
    assert response.status_code == 202
    assert storage.get_watchlist_codes() == []
    assert storage.get_contracts_df().empty
    assert storage.get_available_snapshot_dates() == []
    payload = api_client.get("/api/underlyings/catalog").json()
    assert payload["items"][0]["ins_code"] == "2001"
    assert payload["fetched_at"]
    assert payload["refresh"]["running"] is False


def test_watchlist_api_supports_save_read_and_clear(api_client, storage):
    storage.replace_underlying_catalog([catalog_row()])
    assert api_client.get("/api/watchlist").json() == {"ins_codes": []}
    response = api_client.put("/api/watchlist", json={"ins_codes": ["۲٬۰۰۱", "2001"]})
    assert response.status_code == 200
    assert response.json() == {"ins_codes": ["2001"]}
    assert api_client.get("/api/watchlist").json() == {"ins_codes": ["2001"]}
    assert api_client.put("/api/watchlist", json={"ins_codes": []}).json() == {"ins_codes": []}


@pytest.mark.parametrize("payload", [None, {}, {"ins_codes": "2001"}, {"ins_codes": [True]},
    {"ins_codes": [0]}, {"ins_codes": [-1]}, {"ins_codes": [1.5]}, {"ins_codes": ["wat"]},
    {"ins_codes": ["9" * 5000]}, {"ins_codes": [str(2**63)]}, {"ins_codes": ["9999"]}])
def test_watchlist_api_rejects_invalid_payloads(api_client, payload):
    assert api_client.put("/api/watchlist", json=payload).status_code == 400


def test_watchlist_body_size_is_bounded(api_client):
    assert api_client.put("/api/watchlist", content=b"x" * (main.MAX_API_JSON_BODY_BYTES + 1)).status_code == 413


def test_watchlist_cannot_change_during_refresh(api_client, storage):
    storage.replace_underlying_catalog([catalog_row()])
    main._refresh_status["running"] = True
    assert api_client.put("/api/watchlist", json={"ins_codes": ["2001"]}).status_code == 409
    assert storage.get_watchlist_codes() == []


def test_empty_watchlist_does_not_start_refresh_or_history_import(api_client, monkeypatch):
    run = MagicMock()
    history = MagicMock()
    monkeypatch.setattr(main, "run_pipeline", run)
    monkeypatch.setattr(main, "_ensure_snapshot_date", history)
    assert api_client.post("/api/refresh").json() == {"status": "no_symbols"}
    assert api_client.get("/api/underlyings").json()["items"] == []
    assert api_client.get("/api/summary").json()["contract_count"] == 0
    run.assert_not_called()
    history.assert_not_called()
    assert main._last_refresh_request_at == 0


def test_refresh_captures_saved_selection_not_search_or_body(api_client, storage, monkeypatch):
    storage.replace_underlying_catalog([catalog_row()])
    storage.replace_watchlist([2001])
    run = MagicMock(return_value={"options": 1})
    monkeypatch.setattr(main, "run_pipeline", run)
    assert api_client.post("/api/refresh?q=اهرم", json={"ins_codes": ["2002"]}).json()["status"] == "started"
    assert run.call_args.kwargs["underlying_ins_codes"] == {2001}


def test_dashboard_shows_only_selection_with_placeholder_and_scoped_summary(api_client, storage):
    storage.replace_underlying_catalog([catalog_row(), catalog_row(2002), catalog_row(2003)])
    storage.replace_watchlist([2001, 2003])
    storage.upsert_contracts([contract(), contract(1002, 2002, 999)])
    result = api_client.get("/api/underlyings").json()
    indexed = {row["underlying_key"]: row for row in result["items"]}
    assert set(indexed) == {"2001", "2003"}
    assert indexed["2003"]["data_status"] == "needs_refresh"
    summary = api_client.get("/api/summary").json()
    assert summary["underlying_count"] == 2
    assert summary["contract_count"] == 1
    assert summary["total_trade_volume"] == 100
    assert api_client.get("/api/underlyings?q=خودرو").json()["total"] == 1
    assert api_client.get("/api/contracts").json()["total"] == 1


def test_scoped_data_uses_requested_historical_date(storage):
    storage.replace_underlying_catalog([catalog_row(), catalog_row(2002)])
    storage.replace_watchlist([2001])
    storage.insert_contract_snapshot([contract(volume=20), contract(1002, 2002, 900)], "2026-10-19")
    storage.insert_contract_snapshot([contract(volume=50)], "2026-10-20")
    assert get_watchlist_underlyings(storage, snapshot_date="2026-10-19")["items"][0]["trade_volume"] == 20
    assert get_summary(storage, snapshot_date="2026-10-19", underlying_ins_codes={2001})["total_trade_volume"] == 20


def test_refreshing_one_underlying_preserves_other_cached_snapshots(storage, monkeypatch):
    from datetime import datetime, timezone

    storage.replace_underlying_catalog([catalog_row(), catalog_row(2002)])
    storage.replace_watchlist([2001, 2002])
    monkeypatch.setattr(storage, "now", lambda: datetime(2026, 10, 20, 8, tzinfo=timezone.utc))
    storage.insert_contract_snapshot([contract(volume=20), contract(1002, 2002, 40)], "2026-10-20")
    monkeypatch.setattr(storage, "now", lambda: datetime(2026, 10, 20, 9, tzinfo=timezone.utc))
    storage.insert_contract_snapshot([contract(1002, 2002, 70)], "2026-10-20")

    items = get_watchlist_underlyings(storage, snapshot_date="2026-10-20")["items"]
    assert {item["underlying_key"]: item.get("trade_volume") for item in items} == {"2001": 20, "2002": 70}
    assert get_summary(storage, snapshot_date="2026-10-20", underlying_ins_codes={2001, 2002})["total_trade_volume"] == 90


def test_latest_underlying_batch_does_not_restore_removed_contracts(storage, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(storage, "now", lambda: datetime(2026, 10, 20, 8, tzinfo=timezone.utc))
    storage.insert_contract_snapshot([contract(), contract(1002)], "2026-10-20")
    monkeypatch.setattr(storage, "now", lambda: datetime(2026, 10, 20, 9, tzinfo=timezone.utc))
    storage.insert_contract_snapshot([contract(volume=70)], "2026-10-20")
    rows = storage.get_contract_snapshot_df("2026-10-20")
    assert rows["ins_code"].tolist() == [1001]
    assert rows["trade_volume"].tolist() == [70]


def test_missing_volume_is_reported_as_partial_catalog_failure(monkeypatch):
    monkeypatch.setattr(watchlist, "fetch_public_option_market_watch", lambda: [])
    monkeypatch.setattr(watchlist, "normalize_public_option_pairs", lambda _: [contract()])
    monkeypatch.setattr(watchlist, "fetch_underlying_volume", lambda _: (None, "20261021"))
    rows, failures = watchlist.fetch_catalog()
    assert rows[0]["underlying_trade_volume"] is None
    assert failures == 1


@pytest.mark.parametrize("snapshot", [False, True])
def test_scoping_preserves_large_ids_next_to_missing_underlying(storage, snapshot):
    large_code = 73100249549639355
    storage.replace_underlying_catalog([catalog_row(large_code)])
    storage.replace_watchlist([large_code])
    rows = [contract(1001, large_code), {**contract(1002, None), "underlying_symbol": "نامشخص"}]
    if snapshot:
        storage.insert_contract_snapshot(rows, "2026-10-20")
    else:
        storage.upsert_contracts(rows)
    item = get_watchlist_underlyings(storage, snapshot_date="2026-10-20" if snapshot else None)["items"][0]
    assert item["underlying_key"] == str(large_code)
    assert item["data_status"] == "ready"
    assert item["trade_volume"] == 100


@pytest.mark.parametrize("path, method", [("/api/watchlist", "get"), ("/api/watchlist", "put"),
    ("/api/underlyings/catalog", "get"), ("/api/underlyings/catalog/refresh", "post")])
def test_watchlist_endpoints_require_token_and_activation(storage, monkeypatch, path, method):
    monkeypatch.setattr(main, "storage", storage)
    with TestClient(main.app) as client:
        assert getattr(client, method)(path).status_code == 403
        assert getattr(client, method)(path, headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN}).status_code == 423


def test_symbols_page_uses_dashboard_session_and_security_headers(api_client):
    response = api_client.get(f"/dashboard/{main.DASHBOARD_SESSION_ID}/symbols")
    assert response.status_code == 200
    assert "symbols.js" in response.text
    assert "common.js" in response.text
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert api_client.get("/dashboard/wrong/symbols").status_code == 404


@pytest.mark.parametrize("source", ["authenticated", "public"])
def test_pipeline_scopes_details_snapshots_and_client_type(storage, monkeypatch, source):
    monkeypatch.setattr(pipeline, "Storage", lambda: storage)
    monkeypatch.setattr(pipeline, "validate_credentials", MagicMock(side_effect=ValueError() if source == "public" else None))
    client = MagicMock()
    client.call.return_value = []
    monkeypatch.setattr(pipeline, "TsetmcClient", lambda: client)
    monkeypatch.setattr(pipeline, "fetch_all_options", lambda _: [
        {"InsCode": 1002, "UAInsCode": 2002}, {"InsCode": 1001, "UAInsCode": 2001},
    ])
    monkeypatch.setattr(pipeline, "fetch_public_option_market_watch", lambda: [])
    monkeypatch.setattr(pipeline, "normalize_public_option_pairs", lambda _: [contract(1002, 2002), contract()])
    details = MagicMock(return_value={})
    clients = MagicMock(return_value=[])
    monkeypatch.setattr(pipeline, "_fetch_direct_instrument_info", details)
    monkeypatch.setattr(pipeline, "fetch_public_client_type_current_many", clients)
    result = pipeline.run_pipeline(underlying_ins_codes={2001}, limit=1)
    assert result["options"] == 1
    details.assert_called_once_with([1001])
    clients.assert_called_once_with([1001], 0.1)
    assert storage.get_contracts_df()["ins_code"].tolist() == [1001]
    assert storage.get_contract_snapshot_df()["ins_code"].tolist() == [1001]


def test_empty_pipeline_scope_skips_credentials_and_external_calls(monkeypatch):
    credentials = MagicMock()
    fetch = MagicMock()
    monkeypatch.setattr(pipeline, "validate_credentials", credentials)
    monkeypatch.setattr(pipeline, "fetch_public_option_market_watch", fetch)
    assert pipeline.run_pipeline(underlying_ins_codes=set())["options"] == 0
    credentials.assert_not_called()
    fetch.assert_not_called()


@pytest.mark.parametrize("table", ["watchlist", "underlying_catalog"])
def test_migration_recognizes_new_user_data_tables(storage, table):
    storage.replace_underlying_catalog([catalog_row()])
    if table == "watchlist":
        storage.replace_watchlist([2001])
    assert table in app_module.USER_DATA_TABLES
    assert app_module._database_score(storage.db_path) > 0
