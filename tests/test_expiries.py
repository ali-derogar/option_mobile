"""Expiry snapshots, manual refresh isolation, dates, and API regressions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient

from options import app as app_module
from options.backend.api import main
from options.backend.services import expiries
from options.backend.storage import Storage


def contract(code=1001, end_date=20261022, **changes):
    return {
        "ins_code": code, "symbol": f"ض{code}", "underlying_ins_code": 2001,
        "underlying_symbol": "خودرو", "option_type": "call",
        "strike_price": 1000.0, "end_date": end_date, **changes,
    }


def market_row(code=1001, end_date=20261022, **changes):
    return {
        "insCode_C": str(code), "lVal18AFC_C": f"ض{code}", "endDate": end_date,
        "uaInsCode": "2001", "lval30_UA": "خودرو", "strikePrice": 1000,
        "qTotTran5J_C": 0, **changes,
    }


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "options.db")
    yield db
    db.engine.dispose()


@pytest.fixture
def api_client(storage, monkeypatch):
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "expiry_refresh_job", expiries.ExpiryRefresh())
    storage.set_activated()
    with TestClient(main.app, headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN}) as client:
        yield client


@pytest.mark.parametrize("value", [20261021, "20261021", 20240229])
def test_valid_gregorian_expiry(value):
    assert expiries.valid_expiry(value)


@pytest.mark.parametrize("value", [None, 0, True, 20260229, 20261301, 20260431, 14050729, "2026-10-21", "۲۰۲۶۱۰۲۱", float("nan")])
def test_invalid_expiry(value):
    assert not expiries.valid_expiry(value)


def test_fetch_includes_both_sides_farabo_zero_volume_and_deduplicates(monkeypatch):
    feed = [market_row(), market_row(), market_row(
        24139063101391669, insCode_P="67241904692647336", lVal18AFC_P="طبساما922",
        uaInsCode="41625340598198551", lval30_UA="بساما",
    )]
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: feed)
    info = MagicMock(return_value={})
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", info)
    rows, warning = expiries.fetch_expiry_contracts()
    assert len(rows) == 3
    assert {row["option_type"] for row in rows} == {"call", "put"}
    assert {row["underlying_symbol"] for row in rows} == {"خودرو", "بساما"}
    assert len(info.call_args.args[0]) == 3
    assert warning and "3" in warning


def test_metadata_correction_happens_for_entire_market_before_limiting(storage, monkeypatch):
    feed = [market_row(code, 20271021) for code in range(1, 62)]
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: feed)
    info = MagicMock(return_value={61: {"lVal30": "اختیارخ فملی-26000-1405/07/08"}})
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", info)
    rows, warning = expiries.fetch_expiry_contracts()
    storage.replace_expiry_contracts(rows, warning)
    snapshot = storage.get_expiry_snapshot(date(2026, 9, 29))
    assert len(info.call_args.args[0]) == 61
    assert snapshot["total"] == 61
    assert len(snapshot["items"]) == 50
    assert snapshot["items"][0]["ins_code"] == "61"
    assert snapshot["items"][0]["end_date"] == 20260930
    assert snapshot["items"][0]["strike_price"] == 26000


def test_metadata_outage_uses_valid_feed_with_warning(monkeypatch):
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row()])
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", MagicMock(side_effect=requests.ConnectionError()))
    rows, warning = expiries.fetch_expiry_contracts()
    assert rows[0]["end_date"] == 20261022
    assert warning


def test_invalid_dates_are_discarded_with_warning(monkeypatch):
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row(), market_row(1002, 20260229)])
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", lambda _: {})
    rows, warning = expiries.fetch_expiry_contracts()
    assert len(rows) == 1
    assert "نامعتبر" in warning


def test_nearest_fifty_are_sorted_and_include_today(storage):
    storage.replace_expiry_contracts([
        *[contract(code) for code in range(61, 0, -1)],
        contract(100, 20261020), contract(101, 20261021),
    ])
    snapshot = storage.get_expiry_snapshot(date(2026, 10, 21))
    assert snapshot["total"] == 62
    assert len(snapshot["items"]) == 50
    assert [row["ins_code"] for row in snapshot["items"]] == ["101", *map(str, range(1, 50))]
    assert snapshot["nearest_days"] == 0


@pytest.mark.parametrize("days", [10, 6, 5, 2, 1, 0])
def test_calendar_day_count(storage, days):
    today = date(2026, 10, 21)
    end_date = int((today + timedelta(days=days)).strftime("%Y%m%d"))
    storage.replace_expiry_contracts([contract(end_date=end_date)])
    assert storage.get_expiry_snapshot(today)["nearest_days"] == days


def test_next_expiry_is_available_even_after_first_fifty_expire(storage):
    storage.replace_expiry_contracts([*[contract(code, 20261021) for code in range(1, 61)], contract(70, 20261025)])
    assert storage.get_expiry_snapshot(date(2026, 10, 21))["nearest_days"] == 0
    tomorrow = storage.get_expiry_snapshot(date(2026, 10, 22))
    assert tomorrow["total"] == 1
    assert tomorrow["nearest_days"] == 3
    assert tomorrow["items"][0]["ins_code"] == "70"


def test_snapshot_and_warning_persist_after_restart(storage):
    storage.replace_expiry_contracts([contract(12345678901234567)], "اطلاعات تکمیلی ناقص است")
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    reopened = Storage(storage.db_path)
    try:
        assert reopened.get_expiry_snapshot(date(2026, 10, 21)) == previous
        assert previous["items"][0]["ins_code"] == "12345678901234567"
    finally:
        reopened.engine.dispose()


def test_failed_snapshot_transaction_preserves_rows_and_metadata(storage):
    storage.replace_expiry_contracts([contract()], "هشدار قبلی")
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    with pytest.raises(IntegrityError):
        storage.replace_expiry_contracts([contract(1002), contract(1003, symbol=None)])
    assert storage.get_expiry_snapshot(date(2026, 10, 21)) == previous


@pytest.mark.parametrize("feed", [[], [market_row(end_date=None)], [market_row(end_date=20260229)]])
def test_empty_or_invalid_feed_never_erases_previous_snapshot(storage, monkeypatch, feed):
    storage.replace_expiry_contracts([contract()])
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: feed)
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", lambda _: {})
    job = expiries.ExpiryRefresh()
    assert job.begin() == "started"
    job.run(storage)
    assert not job.status()["running"]
    assert job.status()["last_error"]
    assert storage.get_expiry_snapshot(date(2026, 10, 21)) == previous


def test_network_failure_preserves_snapshot_and_does_not_expose_exception(storage, monkeypatch):
    storage.replace_expiry_contracts([contract()])
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", MagicMock(side_effect=requests.Timeout("private URL")))
    job = expiries.ExpiryRefresh()
    job.begin()
    job.run(storage)
    assert "private URL" not in job.status()["last_error"]
    assert storage.get_expiry_snapshot(date(2026, 10, 21)) == previous


def test_refresh_does_not_change_watchlist_or_dashboard_contracts(storage, monkeypatch):
    storage.replace_watchlist([999])
    storage.upsert_contracts([contract(555)])
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda: ([contract()], None))
    job = expiries.ExpiryRefresh()
    job.begin()
    job.run(storage)
    assert not job.status()["last_error"]
    assert storage.get_watchlist_codes() == [999]
    assert storage.get_contracts_df()["ins_code"].tolist() == [555]


def test_reservation_prevents_concurrent_refreshes():
    job = expiries.ExpiryRefresh()
    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(lambda _: job.begin(), range(12)))
    assert results.count("started") == 1
    assert results.count("already_running") == 11


def test_cooldown_is_sixty_seconds_from_start(storage, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(expiries.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda: ([contract()], None))
    job = expiries.ExpiryRefresh()
    assert job.begin() == "started"
    job.run(storage)
    clock[0] += 59
    assert job.begin() == "cooldown"
    clock[0] += 1
    assert job.begin() == "started"


def test_initial_snapshot_has_no_badge_or_network_fetch(api_client, monkeypatch):
    fetch = MagicMock(side_effect=AssertionError("GET must not fetch the market"))
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", fetch)
    response = api_client.get("/api/expiries?date=2000-01-01").json()
    assert response["items"] == []
    assert response["fetched_at"] is None
    assert response["nearest_days"] is None
    assert not response["refresh"]["running"]
    fetch.assert_not_called()


def test_api_manual_refresh_works_with_empty_watchlist(api_client, storage, monkeypatch):
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda: ([contract(end_date=20991022)], None))
    assert storage.get_watchlist_codes() == []
    assert api_client.post("/api/expiries/refresh").status_code == 202
    snapshot = api_client.get("/api/expiries").json()
    assert snapshot["total"] == 1
    assert snapshot["fetched_at"]
    assert not snapshot["refresh"]["running"]
    assert api_client.post("/api/expiries/refresh").json()["status"] == "cooldown"


def test_api_reports_already_running(api_client):
    main.expiry_refresh_job.begin()
    assert api_client.post("/api/expiries/refresh").json()["status"] == "already_running"


@pytest.mark.parametrize("hour, minute, expected_date, days", [(20, 29, "2026-10-21", 1), (20, 31, "2026-10-22", 0)])
def test_api_uses_tehran_midnight_and_ignores_historical_filter(api_client, storage, monkeypatch, hour, minute, expected_date, days):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, hour, minute, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    storage.replace_expiry_contracts([contract()])
    response = api_client.get("/api/expiries?date=2000-01-01").json()
    assert response["as_of_date"] == expected_date
    assert response["nearest_days"] == days


def test_no_future_expiry_keeps_successful_update_metadata(storage):
    storage.replace_expiry_contracts([contract(end_date=20261020)])
    response = storage.get_expiry_snapshot(date(2026, 10, 21))
    assert response["fetched_at"]
    assert response["nearest_days"] is None
    assert response["items"] == []


@pytest.mark.parametrize("method, path", [("get", "/api/expiries"), ("post", "/api/expiries/refresh")])
def test_expiry_routes_use_existing_security_and_activation(api_client, storage, method, path):
    assert getattr(api_client, method)(path, headers={main.LOCAL_API_HEADER: "invalid"}).status_code == 403
    assert getattr(api_client, method)(path, headers={"origin": "https://outside.test"}).status_code == 403
    storage.set_activated(False)
    assert getattr(api_client, method)(path).status_code == 423


def test_snapshot_is_included_in_android_data_detection(storage):
    storage.replace_expiry_contracts([contract()])
    assert "expiry_contracts" in app_module.USER_DATA_TABLES
    assert app_module._database_score(storage.db_path) >= 2


def test_refresh_completion_during_read_keeps_polling(api_client, storage, monkeypatch):
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda: ([contract()], None))
    main.expiry_refresh_job.begin()

    def finish_and_return_previous(today):
        main.expiry_refresh_job.run(storage)
        return previous

    monkeypatch.setattr(storage, "get_expiry_snapshot", finish_and_return_previous)
    response = api_client.get("/api/expiries").json()
    assert response["fetched_at"] is None
    assert response["refresh"]["running"] is True
    assert main.expiry_refresh_job.status()["running"] is False


def test_snapshot_read_does_not_mix_metadata_and_rows_during_replacement(storage):
    storage.replace_expiry_contracts([contract()], "هشدار قبلی")
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    reading = Event()
    release = Event()
    writing = Event()

    def coordinate_reads(connection, cursor, statement, params, context, many):
        if "FROM app_state" in statement and not reading.is_set():
            reading.set()
            assert release.wait(5)
        if statement.startswith("DELETE FROM expiry_contracts"):
            writing.set()

    event.listen(storage.engine, "after_cursor_execute", coordinate_reads)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            reader = workers.submit(storage.get_expiry_snapshot, date(2026, 10, 21))
            try:
                assert reading.wait(2)
                writer = workers.submit(storage.replace_expiry_contracts, [contract(1002)], "هشدار جدید")
                assert writing.wait(2)
            finally:
                release.set()
            assert reader.result(timeout=3) == previous
            writer.result(timeout=3)
    finally:
        event.remove(storage.engine, "after_cursor_execute", coordinate_reads)
    assert storage.get_expiry_snapshot(date(2026, 10, 21))["items"][0]["ins_code"] == "1002"


def test_slow_expiry_read_does_not_block_health_requests(api_client, storage, monkeypatch):
    entered = Event()
    release = Event()

    def slow_snapshot(today):
        entered.set()
        assert release.wait(5)
        return {"items": [], "total": 0}

    monkeypatch.setattr(storage, "get_expiry_snapshot", slow_snapshot)
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(api_client.get, "/api/expiries")
        try:
            assert entered.wait(2)
            health = workers.submit(api_client.get, "/api/health")
            assert health.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=2).status_code == 200
