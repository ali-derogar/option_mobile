"""Expiry snapshots, manual refresh isolation, dates, and API regressions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.testclient import TestClient

from options import app as app_module
from options.backend.api import main
from options.backend.services import expiries, expiry_selection, public_options
from options.backend.services.expiry_selection import (
    ExpiryFilters,
    ExpirySnapshot,
    select_expiries,
)
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


def priced_contract(code=1001, end_date=20261021, **changes):
    return contract(code, end_date, **{
        "ask_price": 100, "ask_volume": 2, "underlying_last_price": 1120,
        "underlying_closing_price": 1120,
        "trade_volume": 30, **changes,
    })


def priced_snapshot(rows, received_at="2026-10-21T09:00:00+00:00"):
    return ExpirySnapshot(rows, market_received_at=received_at)


@pytest.fixture(autouse=True)
def isolate_daily_price_network(monkeypatch):
    """Expiry tests only use explicitly supplied daily price fixtures."""
    monkeypatch.setattr(expiries, "fetch_public_daily_price_ranges", lambda codes, today: {})


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


def test_fetch_includes_both_sides_farabo_zero_volume_and_deduplicates(monkeypatch, caplog):
    caplog.set_level("INFO", logger=expiries.__name__)
    feed = [market_row(), market_row(), market_row(
        24139063101391669, insCode_P="67241904692647336", lVal18AFC_P="طبساما922",
        uaInsCode="41625340598198551", lval30_UA="بساما",
    )]
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: feed)
    info = MagicMock(return_value={})
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", info)
    snapshot = expiries.fetch_expiry_contracts()
    rows, warning = snapshot.rows, snapshot.warning
    assert len(rows) == 3
    assert {row["option_type"] for row in rows} == {"call", "put"}
    assert {row["underlying_symbol"] for row in rows} == {"خودرو", "بساما"}
    assert len(info.call_args.args[0]) == 3
    assert warning is None
    assert "metadata unavailable for 3 expiry contracts" in caplog.text


def test_metadata_correction_happens_for_entire_market_before_limiting(storage, monkeypatch):
    feed = [market_row(code, 20271021) for code in range(1, 62)]
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: feed)
    info = MagicMock(return_value={61: {"lVal30": "اختیارخ فملی-26000-1405/07/08"}})
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", info)
    snapshot = expiries.fetch_expiry_contracts()
    rows, warning = snapshot.rows, snapshot.warning
    storage.replace_expiry_contracts(rows, warning)
    snapshot = storage.get_expiry_snapshot(date(2026, 9, 29))
    assert [code for call in info.call_args_list for code in call.args[0]] == list(range(1, 62))
    assert all(len(call.args[0]) <= 50 for call in info.call_args_list)
    assert snapshot["total"] == 61
    assert len(snapshot["items"]) == 50
    assert snapshot["items"][0]["ins_code"] == "61"
    assert snapshot["items"][0]["end_date"] == 20260930
    assert snapshot["items"][0]["strike_price"] == 26000


def test_metadata_outage_uses_valid_feed_and_only_logs_diagnostics(monkeypatch, caplog):
    caplog.set_level("INFO", logger=expiries.__name__)
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row()])
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", MagicMock(side_effect=requests.ConnectionError()))
    snapshot = expiries.fetch_expiry_contracts()
    rows, warning = snapshot.rows, snapshot.warning
    assert rows[0]["end_date"] == 20261022
    assert warning is None
    assert "metadata unavailable for 1 expiry contracts" in caplog.text


def test_invalid_dates_are_discarded_with_warning(monkeypatch):
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row(), market_row(1002, 20260229)])
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", lambda _, **kwargs: {})
    snapshot = expiries.fetch_expiry_contracts()
    rows, warning = snapshot.rows, snapshot.warning
    assert len(rows) == 1
    assert "نامعتبر" in warning


def test_daily_ranges_are_requested_only_for_corrected_calls_expiring_today(storage, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(expiries, "datetime", FrozenDatetime)
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [
        market_row(1, 20261022, insCode_P="2", pMeOf_C=100, qTitMeOf_C=2, pClosing_UA=1120),
        market_row(3, 20261023),
    ])
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", lambda codes, **kwargs: {
        1: {"lVal30": "اختیارخ خودرو-1000-1405/07/29"},
    })
    daily = MagicMock(return_value={1: {"price_min": 80, "price_max": 160, "price_range_date": 20261021}})
    monkeypatch.setattr(expiries, "fetch_public_daily_price_ranges", daily)
    snapshot = expiries.fetch_expiry_contracts()
    daily.assert_called_once_with([1], date(2026, 10, 21))
    storage.replace_expiry_contracts(snapshot.rows, market_received_at=snapshot.market_received_at)
    row = storage.get_expiry_snapshot(date(2026, 10, 21))["items"][0]
    assert row["price_min"] == 80
    assert row["price_max"] == 160
    assert row["price_range_date"] == 20261021
    assert row["gross_profit_per_unit"] == 20
    reopened = Storage(storage.db_path)
    try:
        assert reopened.get_expiry_snapshot(date(2026, 10, 21))["items"][0] == row
    finally:
        reopened.engine.dispose()


def test_daily_ranges_do_not_mutate_previously_published_previews(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(expiries, "datetime", FrozenDatetime)
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row(1, 20261021)])
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", lambda codes, **kwargs: {})
    monkeypatch.setattr(expiries, "fetch_public_daily_price_ranges", lambda codes, today: {
        1: {"price_min": 80, "price_max": 160, "price_range_date": 20261021},
    })
    previews = []
    snapshot = expiries.fetch_expiry_contracts(lambda value, processed, total: previews.append(value))
    assert [preview.rows[0]["price_min"] for preview in previews] == [None, None, 80]
    assert snapshot.rows[0]["price_min"] == 80


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


def test_metadata_fallback_notice_is_hidden_for_saved_and_partial_snapshots(api_client, storage):
    notice = "مشخصات تکمیلی 8 قرارداد دریافت نشد؛ از اطلاعات معتبر خوراک بازار استفاده شد."
    other_warning = "1 قرارداد با تاریخ سررسید نامعتبر کنار گذاشته شد."
    rows = [contract(end_date=20991022)]
    storage.replace_expiry_contracts(rows, f"{notice} {other_warning}")
    saved = api_client.get("/api/expiries").json()
    assert saved["warning"] == other_warning
    assert saved["total"] == 1
    main.expiry_refresh_job.begin()
    main.expiry_refresh_job._publish(ExpirySnapshot(rows, notice), 1, 1)
    preview = api_client.get("/api/expiries").json()
    assert preview["partial"] is True
    assert preview["warning"] is None
    assert preview["total"] == 1


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
    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", lambda _, **kwargs: {})
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
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda **kwargs: ExpirySnapshot([contract()]))
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
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda **kwargs: ExpirySnapshot([contract()]))
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
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda **kwargs: ExpirySnapshot([contract(end_date=20991022)]))
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


@pytest.mark.parametrize("partial", [False, True], ids=["saved", "in-progress"])
def test_expiry_status_supports_android_python310_date_parsing(api_client, storage, monkeypatch, partial):
    class AndroidDate(date):
        @classmethod
        def fromisoformat(cls, value):
            # Python 3.10 accepts YYYY-MM-DD, but not compact market dates.
            if len(value) != 10 or value[4] != "-" or value[7] != "-":
                raise ValueError(f"Invalid isoformat string: {value!r}")
            return super().fromisoformat(value)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(expiry_selection, "date", AndroidDate)
    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    rows = [contract(1, 20261021), contract(2, 20261022), contract(3, 20261101)]
    storage.replace_expiry_contracts(rows)
    if partial:
        main.expiry_refresh_job.begin()
        main.expiry_refresh_job._publish(ExpirySnapshot(rows), 1, len(rows))

    response = api_client.get("/api/expiries")

    assert response.status_code == 200
    result = response.json()
    assert result["partial"] is partial
    assert result["refresh"]["running"] is partial
    assert result["as_of_date"] == "2026-10-21"
    assert result["nearest_days"] == 0
    assert [(row["ins_code"], row["days_remaining"]) for row in result["items"]] == [("1", 0), ("2", 1), ("3", 11)]


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
    monkeypatch.setattr(expiries, "fetch_expiry_contracts", lambda **kwargs: ExpirySnapshot([contract()]))
    main.expiry_refresh_job.begin()

    def finish_and_return_previous(today, **kwargs):
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

    def slow_snapshot(today, **kwargs):
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


def test_metadata_callback_publishes_before_the_slowest_request_finishes(monkeypatch):
    first_ready = Event()
    release = Event()
    results = {}

    def fetch_one(code):
        if code == 2:
            assert release.wait(5)
            raise requests.Timeout("slow contract")
        return {"lVal18AFC": "ضخود"}

    def consume(code, info):
        results[code] = info
        if code == 1:
            first_ready.set()

    monkeypatch.setattr(public_options, "TSETMC_PUBLIC_CLIENT_TYPE_WORKERS", 2)
    monkeypatch.setattr(public_options, "fetch_public_instrument_info", fetch_one)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(public_options.fetch_public_instrument_info_many, [1, 2], on_result=consume)
        try:
            assert first_ready.wait(2)
            assert not pending.done()
            assert results == {1: {"lVal18AFC": "ضخود"}}
        finally:
            release.set()
        assert pending.result(timeout=2) == {1: {"lVal18AFC": "ضخود"}}
    assert results[2] is None


def test_feed_and_each_metadata_result_are_published_in_order(monkeypatch):
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row(1), market_row(2, None)])
    progress = []

    def fetch_batch(codes, *, on_result):
        # Missing dates are checked before known expiries.
        assert codes == [2, 1]
        on_result(2, {"lVal30": "اختیارخ خودرو-26000-1405/08/01"})
        on_result(1, None)
        return {2: {"lVal30": "اختیارخ خودرو-26000-1405/08/01"}}

    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", fetch_batch)
    snapshot = expiries.fetch_expiry_contracts(lambda *values: progress.append(values))
    rows, warning = snapshot.rows, snapshot.warning
    assert [event[1] for event in progress] == [0, 1, 2]
    assert [len(event[0].rows) for event in progress] == [1, 2, 2]
    assert all(event[2] == 2 for event in progress)
    assert len(rows) == 2
    assert warning is None


def test_api_displays_incremental_corrections_before_refresh_completion(api_client, storage, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    storage.replace_expiry_contracts([contract(99, 20991022)])
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row(1, 20271022), market_row(2, 20271022)])
    first_ready = Event()
    release = Event()

    def fetch_batch(codes, *, on_result):
        info = {"lVal30": "اختیارخ خودرو-26000-1405/08/01"}
        on_result(1, info)
        first_ready.set()
        assert release.wait(5)
        on_result(2, None)
        return {1: info}

    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", fetch_batch)
    assert main.expiry_refresh_job.begin() == "started"
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(main.expiry_refresh_job.run, storage)
        try:
            assert first_ready.wait(2)
            snapshot = api_client.get("/api/expiries").json()
            assert snapshot["partial"] is True
            assert snapshot["refresh"]["running"] is True
            assert snapshot["refresh"]["processed"] == 1
            assert snapshot["refresh"]["total"] == 2
            assert snapshot["items"][0]["ins_code"] == "1"
            assert snapshot["items"][0]["strike_price"] == 26000
            assert snapshot["items"][0]["end_date"] == 20261023
            assert snapshot["fetched_at"] == previous["fetched_at"]
            assert snapshot["preview_updated_at"]
            assert storage.get_expiry_snapshot(date(2026, 10, 21)) == previous
            assert not pending.done()
        finally:
            release.set()
        pending.result(timeout=2)
    complete = api_client.get("/api/expiries").json()
    assert complete["partial"] is False
    assert complete["refresh"]["stage"] == "complete"
    assert complete["refresh"]["processed"] == complete["refresh"]["total"] == 2
    assert {item["ins_code"] for item in complete["items"]} == {"1", "2"}
    assert complete["fetched_at"] != previous["fetched_at"]


def test_partial_preview_failure_restores_last_complete_snapshot(storage, monkeypatch):
    storage.replace_expiry_contracts([contract(99)])
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    job = expiries.ExpiryRefresh()

    def fail_after_progress(*, progress_callback):
        progress_callback(ExpirySnapshot([contract(1)]), 1, 2)
        assert job.view(date(2026, 10, 21))["items"][0]["ins_code"] == "1"
        raise RuntimeError("interrupted processing")

    monkeypatch.setattr(expiries, "fetch_expiry_contracts", fail_after_progress)
    job.begin()
    job.run(storage)
    assert job.view(date(2026, 10, 21))["partial"] is False
    assert job.status()["stage"] == "failed"
    assert job.status()["last_error"]
    assert storage.get_expiry_snapshot(date(2026, 10, 21)) == previous


def test_preview_recomputes_days_on_midnight_and_keeps_nearest_fifty():
    job = expiries.ExpiryRefresh()
    job.begin()
    rows = [*[contract(code, 20261021) for code in range(1, 61)], contract(70, 20261025)]
    job._publish(ExpirySnapshot(rows), 1, len(rows))
    today = job.view(date(2026, 10, 21))
    assert len(today["items"]) == 50
    assert today["total"] == 61
    assert today["nearest_days"] == 0
    tomorrow = job.view(date(2026, 10, 22))
    assert tomorrow["total"] == 1
    assert tomorrow["nearest_days"] == 3
    assert tomorrow["items"][0]["ins_code"] == "70"


def test_new_refresh_clears_previous_progress_and_preview(storage, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(expiries.time, "monotonic", lambda: clock[0])

    def fetch(*, progress_callback):
        progress_callback(ExpirySnapshot([contract()]), 1, 1)
        return ExpirySnapshot([contract()])

    monkeypatch.setattr(expiries, "fetch_expiry_contracts", fetch)
    job = expiries.ExpiryRefresh()
    job.begin()
    job.run(storage)
    assert job.status()["processed"] == 1
    assert job.status()["finished_at"]
    clock[0] += 60
    assert job.begin() == "started"
    view = job.view(date(2026, 10, 21))
    assert view["partial"] is False
    assert view["refresh"]["stage"] == "market"
    assert view["refresh"]["processed"] == view["refresh"]["total"] == 0
    assert view["refresh"]["finished_at"] is None


@pytest.mark.parametrize("underlying, cash, profit, expected", [
    (1120, 120, 20, 20), (1100, 100, 0, 0),
    (1050, 50, -50, -50), (1000, 0, -100, -100), (950, 0, -100, -100),
])
def test_arbitrage_uses_closing_price_and_best_ask(underlying, cash, profit, expected):
    snapshot = priced_snapshot([priced_contract(underlying_closing_price=underlying)])
    row = select_expiries(snapshot, date(2026, 10, 21))["items"][0]
    assert row["intrinsic_gap_percent"] == pytest.approx(expected)
    assert row["estimated_cash_value_per_unit"] == cash
    assert row["gross_profit_per_unit"] == profit
    assert row["is_arbitrage_candidate"] is (profit > 0)
    # General analytics retain the last-trade basis, independently of settlement.
    assert row["intrinsic_value"] == 120
    assert row["moneyness"] == "ITM"


@pytest.mark.parametrize("field", ["strike_price", "underlying_closing_price", "ask_price", "ask_volume"])
@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), True, "bad"])
def test_invalid_pricing_inputs_never_produce_score(field, value):
    row = select_expiries(priced_snapshot([priced_contract(**{field: value})]), date(2026, 10, 21))["items"][0]
    assert row["intrinsic_gap_percent"] is None
    assert row["estimated_cash_value_per_unit"] is None
    assert row["gross_profit_per_unit"] is None
    assert row["is_arbitrage_candidate"] is False


@pytest.mark.parametrize("changes", [
    {"option_type": "put", "underlying_last_price": 880, "underlying_closing_price": 880},
    {"option_type": None}, {"end_date": 20261022},
])
def test_non_calls_and_future_expiries_have_no_arbitrage_estimate(changes):
    row = select_expiries(priced_snapshot([priced_contract(**changes)]), date(2026, 10, 21))["items"][0]
    assert row["intrinsic_gap_percent"] is None
    assert row["estimated_cash_value_per_unit"] is None
    assert row["gross_profit_per_unit"] is None
    assert row["is_arbitrage_candidate"] is False


def test_last_price_is_not_required_or_used_as_a_closing_price_fallback():
    snapshot = priced_snapshot([
        priced_contract(1, underlying_last_price=None),
        priced_contract(2, underlying_last_price=10000, underlying_closing_price=None),
    ])
    rows = select_expiries(snapshot, date(2026, 10, 21))["items"]
    assert rows[0]["is_arbitrage_candidate"] is True
    assert rows[0]["intrinsic_value"] is None
    assert rows[1]["is_arbitrage_candidate"] is False
    assert rows[1]["intrinsic_gap_percent"] is None


def test_arbitrage_filter_excludes_nonpositive_profits_and_other_contracts():
    snapshot = priced_snapshot([
        priced_contract(1), priced_contract(2, ask_price=120), priced_contract(3, ask_price=121),
        priced_contract(4, option_type="put", underlying_closing_price=800),
        priced_contract(5, 20261022, ask_price=1), priced_contract(6, 20261020),
        priced_contract(7, ask_volume=0), priced_contract(8, underlying_closing_price=None),
    ])
    result = select_expiries(snapshot, date(2026, 10, 21), ExpiryFilters(arbitrage_only=True, min_score=-100))
    assert [row["ins_code"] for row in result["items"]] == ["1"]
    assert result["total"] == 1
    assert result["total_upcoming"] == 7
    assert result["nearest_days"] == 0


@pytest.mark.parametrize("range_date, low, high, expected", [
    (20261021, 80, 160, (80, 160)), (20261021, 100, 100, (100, 100)),
    (20261020, 80, 160, (None, None)), (None, 80, 160, (None, None)),
    (20261021, 0, 160, (None, None)), (20261021, 180, 160, (None, None)),
    (20261021, float("nan"), 160, (None, None)),
])
def test_day_ranges_are_validated_and_never_replace_the_current_ask(range_date, low, high, expected):
    snapshot = priced_snapshot([priced_contract(
        price_min=low, price_max=high, price_range_date=range_date,
    )])
    row = select_expiries(snapshot, date(2026, 10, 21))["items"][0]
    assert (row["price_min"], row["price_max"]) == expected
    assert row["gross_profit_per_unit"] == 20
    assert row["intrinsic_gap_percent"] == 20


def test_historical_cheap_trade_does_not_create_a_current_arbitrage_candidate():
    snapshot = priced_snapshot([priced_contract(
        ask_price=150, price_min=80, price_max=200, price_range_date=20261021,
    )])
    row = select_expiries(snapshot, date(2026, 10, 21))["items"][0]
    assert row["price_min"] == 80
    assert row["gross_profit_per_unit"] == -30
    assert row["is_arbitrage_candidate"] is False


def test_ranking_only_changes_today_and_uses_unrounded_scores():
    rows = [
        priced_contract(1, ask_price=101), priced_contract(2, ask_price=100),
        priced_contract(3, ask_price=100), priced_contract(4, ask_price=None),
        priced_contract(5, ask_price=150),
        priced_contract(6, 20261022, ask_price=150), priced_contract(7, 20261022, ask_price=1),
        priced_contract(8, ask_price=99.99999999),
    ]
    selected = select_expiries(priced_snapshot(rows), date(2026, 10, 21))
    assert [row["ins_code"] for row in selected["items"]] == ["8", "2", "3", "1", "5", "4", "6", "7"]


def test_filtering_and_ranking_use_entire_market_before_limit():
    rows = [priced_contract(code, ask_price=1000) for code in range(1, 101)]
    rows += [priced_contract(code, ask_price=60, underlying_ins_code=2002, underlying_symbol="فملی") for code in range(101, 161)]
    snapshot = priced_snapshot(rows)
    selected = select_expiries(snapshot, date(2026, 10, 21), ExpiryFilters(
        min_score=50, underlying_ins_code=2002, arbitrage_only=True,
    ))
    assert selected["total"] == 60
    assert selected["total_upcoming"] == 160
    assert len(selected["items"]) == 50
    assert [row["ins_code"] for row in selected["items"]] == list(map(str, range(101, 151)))
    assert {row["symbol"] for row in selected["underlyings"]} == {"خودرو", "فملی"}


@pytest.mark.parametrize("filters, expected", [
    (ExpiryFilters(underlying_ins_code=2002), [2]),
    (ExpiryFilters(option_type="put"), [2]),
    (ExpiryFilters(min_days=1), [2, 3]),
    (ExpiryFilters(max_days=1), [1, 2]),
    (ExpiryFilters(moneyness="OTM"), [2]),
    (ExpiryFilters(moneyness="ATM"), [3]),
    (ExpiryFilters(min_score=0), [1]),
    (ExpiryFilters(min_volume=20), [1, 2]),
    (ExpiryFilters(arbitrage_only=True), [1]),
    (ExpiryFilters(arbitrage_only=True, option_type="put"), []),
    (ExpiryFilters(arbitrage_only=True, min_days=1), []),
    (ExpiryFilters(option_type="put", min_days=1, max_days=1, min_volume=20), [2]),
])
def test_individual_and_combined_filters(filters, expected):
    snapshot = priced_snapshot([
        priced_contract(1),
        priced_contract(2, 20261022, option_type="put", underlying_ins_code=2002),
        priced_contract(3, 20261023, underlying_last_price=1000, trade_volume=0),
        priced_contract(4, 20261020),
    ])
    result = select_expiries(snapshot, date(2026, 10, 21), filters)
    assert [row["ins_code"] for row in result["items"]] == list(map(str, expected))
    assert result["nearest_days"] == 0


@pytest.mark.parametrize("received_at, current", [
    (None, False), ("invalid", False), ("2026-10-22T01:00:00", False),
    ("2026-10-21T20:29:59Z", False), ("2026-10-21T20:30:00Z", True),
])
def test_price_freshness_uses_tehran_day(received_at, current):
    result = select_expiries(priced_snapshot([priced_contract(end_date=20261022)], received_at), date(2026, 10, 22))
    assert result["pricing_current"] is current
    assert (result["items"][0]["intrinsic_gap_percent"] is not None) is current
    assert result["items"][0]["is_arbitrage_candidate"] is current
    assert result["nearest_days"] == 0


@pytest.mark.parametrize("filters", [ExpiryFilters(min_score=0), ExpiryFilters(arbitrage_only=True)])
def test_stale_prices_disable_pricing_filters_without_losing_global_badge(filters):
    snapshot = priced_snapshot([priced_contract()], "2026-10-20T09:00:00Z")
    result = select_expiries(snapshot, date(2026, 10, 21), filters)
    assert result["items"] == []
    assert result["total_upcoming"] == 1
    assert result["nearest_days"] == 0


def test_shared_selection_deduplicates_and_drops_invalid_dates():
    result = select_expiries(priced_snapshot([
        priced_contract(1), priced_contract(1, ask_price=50), priced_contract(2, 20260229),
        priced_contract(3, None), priced_contract(4, 20261020),
    ]), date(2026, 10, 21))
    assert result["total_upcoming"] == 1
    assert result["items"][0]["intrinsic_gap_percent"] == pytest.approx(140)


def test_price_snapshot_survives_restart_and_failed_replacement(storage):
    storage.replace_expiry_contracts([priced_contract()], "original", market_received_at="2026-10-21T09:00:00Z")
    expected = storage.get_expiry_snapshot(date(2026, 10, 21))
    reopened = Storage(storage.db_path)
    try:
        assert reopened.get_expiry_snapshot(date(2026, 10, 21)) == expected
        with pytest.raises(IntegrityError):
            reopened.replace_expiry_contracts([priced_contract(2, symbol=None)], market_received_at="2026-10-22T09:00:00Z")
        assert reopened.get_expiry_snapshot(date(2026, 10, 21)) == expected
    finally:
        reopened.engine.dispose()


def test_legacy_expiry_table_is_migrated_without_erasing_rows(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE expiry_contracts (ins_code INTEGER PRIMARY KEY, symbol TEXT NOT NULL, "
                   "underlying_ins_code INTEGER, underlying_symbol TEXT, option_type TEXT NOT NULL, "
                   "strike_price FLOAT, end_date INTEGER NOT NULL)")
        db.execute("INSERT INTO expiry_contracts VALUES (1, 'ضخود', 2001, 'خودرو', 'call', 1000, 20261021)")
    migrated = Storage(path)
    try:
        result = migrated.get_expiry_snapshot(date(2026, 10, 21))
        assert result["items"][0]["symbol"] == "ضخود"
        assert result["items"][0]["ask_price"] is None
        assert result["items"][0]["underlying_closing_price"] is None
        assert result["items"][0]["price_min"] is None
        assert result["items"][0]["price_max"] is None
        assert result["items"][0]["intrinsic_gap_percent"] is None
        assert result["items"][0]["is_arbitrage_candidate"] is False
        migrated.replace_expiry_contracts([priced_contract()], market_received_at="2026-10-21T09:00:00Z")
        assert migrated.get_expiry_snapshot(date(2026, 10, 21))["items"][0]["intrinsic_gap_percent"] == pytest.approx(20)
    finally:
        migrated.engine.dispose()


def test_previous_priced_schema_requires_new_closing_price_after_migration(storage):
    import sqlite3

    storage.replace_expiry_contracts([priced_contract()], "previous warning", market_received_at="2026-10-21T09:00:00Z")
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    storage.engine.dispose()
    with sqlite3.connect(storage.db_path) as db:
        db.execute("ALTER TABLE expiry_contracts DROP COLUMN underlying_closing_price")
    migrated = Storage(storage.db_path)
    try:
        result = migrated.get_expiry_snapshot(date(2026, 10, 21))
        row = result["items"][0]
        assert row["ask_price"] == 100
        assert row["underlying_last_price"] == 1120
        assert row["underlying_closing_price"] is None
        assert row["gross_profit_per_unit"] is None
        assert row["is_arbitrage_candidate"] is False
        assert result["fetched_at"] == previous["fetched_at"]
        assert result["market_received_at"] == previous["market_received_at"]
        assert result["warning"] == "previous warning"
        migrated.replace_expiry_contracts([priced_contract()], market_received_at="2026-10-21T09:00:00Z")
        assert migrated.get_expiry_snapshot(date(2026, 10, 21))["items"][0]["is_arbitrage_candidate"] is True
    finally:
        migrated.engine.dispose()


@pytest.mark.parametrize("query", [
    "option_type=bad", "moneyness=unknown", "min_days=-1", "max_days=-1", "min_volume=-1",
    "min_days=3&max_days=2", "min_score=NaN", "min_score=inf", "min_score=bad", "min_score=1e999",
    "underlying_ins_code=0", "underlying_ins_code=x", "min_days=1.5", "min_volume=nan",
    "underlying_ins_code=9223372036854775808",
    "arbitrage_only=1", "arbitrage_only=yes", "arbitrage_only=TRUE", "arbitrage_only=bad",
    "arbitrage_only=",
])
def test_invalid_api_filters_return_400(api_client, query):
    assert api_client.get(f"/api/expiries?{query}").status_code == 400


def test_api_filters_accept_persian_and_arabic_digits(api_client, storage, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    storage.replace_expiry_contracts([priced_contract(), priced_contract(2, option_type="put")],
                                    market_received_at="2026-10-21T09:00:00Z")
    response = api_client.get("/api/expiries", params={
        "underlying_ins_code": "۲٬۰۰۱", "min_days": "٠", "max_days": "۰", "min_volume": "٢٠",
        "min_score": "۱۹٫۵", "option_type": "call", "moneyness": "ITM",
    })
    assert response.status_code == 200
    payload = response.json()
    assert [row["ins_code"] for row in payload["items"]] == ["1001"]
    assert payload["total_upcoming"] == 2
    assert payload["nearest_days"] == 0


@pytest.mark.parametrize("query, expected", [
    ("", [1, 2, 3, 4]), ("arbitrage_only=false", [1, 2, 3, 4]),
    ("arbitrage_only=true", [1]), ("arbitrage_only=true&option_type=put", []),
    ("arbitrage_only=true&min_days=1", []), ("arbitrage_only=true&min_volume=31", []),
    ("arbitrage_only=true&min_score=21", []), ("arbitrage_only=true&underlying_ins_code=2002", []),
])
def test_api_arbitrage_filter_composes_with_other_filters(api_client, storage, monkeypatch, query, expected):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    storage.replace_expiry_contracts([
        priced_contract(1), priced_contract(2, ask_price=120),
        priced_contract(3, option_type="put", underlying_closing_price=800),
        priced_contract(4, 20261022, ask_price=1),
    ], market_received_at="2026-10-21T09:00:00Z")
    response = api_client.get(f"/api/expiries?{query}")
    assert response.status_code == 200
    payload = response.json()
    assert [row["ins_code"] for row in payload["items"]] == list(map(str, expected))
    assert payload["total_upcoming"] == 4
    assert payload["nearest_days"] == 0
    if payload["items"]:
        assert payload["items"][0]["estimated_cash_value_per_unit"] == 120
        assert payload["items"][0]["gross_profit_per_unit"] == 20
        assert payload["items"][0]["is_arbitrage_candidate"] is True


def test_progress_and_persisted_snapshot_share_filters_and_feed_timestamp(storage, monkeypatch):
    clock = [datetime(2026, 10, 21, 20, 29, tzinfo=timezone.utc)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(expiries, "datetime", FrozenDatetime)
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row(
        1, 20261022, pMeOf_C=100, qTitMeOf_C=2, pDrCotVal_UA=1200, pClosing_UA=1120, qTotTran5J_C=30,
    )])

    def fetch_metadata(codes, *, on_result):
        clock[0] += timedelta(minutes=2)
        info = {"lVal30": "اختیارخ خودرو-1050-1405/07/30"}
        on_result(1, info)
        return {1: info}

    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", fetch_metadata)
    previews = []
    snapshot = expiries.fetch_expiry_contracts(lambda value, processed, total: previews.append(value))
    assert snapshot.market_received_at == "2026-10-21T20:29:00+00:00"
    assert all(value.market_received_at == snapshot.market_received_at for value in previews)
    assert snapshot.rows[0]["strike_price"] == 1050
    assert snapshot.rows[0]["end_date"] == 20261022
    assert snapshot.rows[0]["underlying_closing_price"] == 1120
    storage.replace_expiry_contracts(snapshot.rows, snapshot.warning, market_received_at=snapshot.market_received_at)
    job = expiries.ExpiryRefresh()
    job.begin()
    job._publish(snapshot, 1, 1)
    for today in [date(2026, 10, 21), date(2026, 10, 22)]:
        for filters in [ExpiryFilters(), ExpiryFilters(min_score=0), ExpiryFilters(option_type="put"),
                        ExpiryFilters(arbitrage_only=True)]:
            preview = job.view(today, filters)
            complete = storage.get_expiry_snapshot(today, filters=filters)
            assert {key: preview[key] for key in complete if key != "fetched_at"} == {
                key: value for key, value in complete.items() if key != "fetched_at"
            }
    assert storage.get_expiry_snapshot(date(2026, 10, 22))["pricing_current"] is False


def test_metadata_correction_updates_rank_and_filter_before_final_commit(api_client, storage, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    monkeypatch.setattr(expiries, "datetime", FrozenDatetime)
    storage.replace_expiry_contracts([priced_contract(99, 20261023)], market_received_at="2026-10-21T09:00:00Z")
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    feed = [market_row(code, 20261021, pMeOf_C=100, qTitMeOf_C=1, pDrCotVal_UA=1200, pClosing_UA=1120)
            for code in range(1, 62)]
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: feed)
    feed_ready, correct_metadata, correction_ready, finish = (Event() for _ in range(4))

    def fetch_batch(codes, *, on_result):
        if codes[0] == 1:
            feed_ready.set()
            assert correct_metadata.wait(5)
        info = {}
        for code in codes:
            info[code] = {"lVal30": "اختیارخ خودرو-900-1405/07/29"} if code == 61 else {"lVal18AFC": f"ض{code}"}
            on_result(code, info[code])
        if 61 in codes:
            correction_ready.set()
            assert finish.wait(5)
        return info

    monkeypatch.setattr(expiries, "fetch_public_instrument_info_many", fetch_batch)
    main.expiry_refresh_job.begin()
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(main.expiry_refresh_job.run, storage)
        try:
            assert feed_ready.wait(2)
            initial = api_client.get("/api/expiries").json()
            assert initial["partial"] is True
            assert [row["ins_code"] for row in initial["items"]] == list(map(str, range(1, 51)))
            assert api_client.get("/api/expiries?min_score=50").json()["items"] == []
            correct_metadata.set()
            assert correction_ready.wait(2)
            corrected = api_client.get("/api/expiries?min_score=50").json()
            assert corrected["partial"] is True
            assert corrected["total"] == 1
            assert corrected["total_upcoming"] == 61
            assert corrected["nearest_days"] == 0
            assert corrected["items"][0]["ins_code"] == "61"
            assert corrected["items"][0]["strike_price"] == 900
            assert corrected["items"][0]["intrinsic_gap_percent"] == pytest.approx(120)
            assert corrected["items"][0]["underlying_closing_price"] == 1120
            assert corrected["items"][0]["gross_profit_per_unit"] == 120
            assert corrected["items"][0]["is_arbitrage_candidate"] is True
            assert api_client.get("/api/expiries?arbitrage_only=true&min_score=50").json()["items"] == corrected["items"]
            assert api_client.get("/api/expiries").json()["items"][0]["ins_code"] == "61"
            assert storage.get_expiry_snapshot(date(2026, 10, 21)) == previous
        finally:
            correct_metadata.set()
            finish.set()
        pending.result(timeout=2)
    complete = api_client.get("/api/expiries?min_score=50").json()
    assert complete["partial"] is False
    assert complete["items"] == corrected["items"]
    assert complete["total"] == corrected["total"]
    assert complete["market_received_at"] == corrected["market_received_at"]


def test_null_instrument_info_preserves_feed_and_logs_missing_metadata(monkeypatch, caplog):
    caplog.set_level("INFO", logger=expiries.__name__)
    monkeypatch.setattr(expiries, "fetch_public_option_market_watch", lambda: [market_row()])
    response = MagicMock()
    response.json.return_value = {"instrumentInfo": None}
    session = MagicMock()
    session.get.return_value = response
    monkeypatch.setattr(public_options, "_session", lambda: session)
    snapshot = expiries.fetch_expiry_contracts()
    assert snapshot.rows[0]["end_date"] == 20261022
    assert snapshot.rows[0]["strike_price"] == 1000
    assert snapshot.warning is None
    assert "metadata unavailable for 1 expiry contracts" in caplog.text


def test_database_failure_discards_preview_and_allows_a_later_success(api_client, storage, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    clock = [100.0]
    monkeypatch.setattr(expiries.time, "monotonic", lambda: clock[0])
    storage.replace_expiry_contracts([priced_contract(99)], "هشدار قبلی", market_received_at="2026-10-21T09:00:00Z")
    previous = storage.get_expiry_snapshot(date(2026, 10, 21))
    next_snapshot = priced_snapshot([priced_contract(1001)])

    def fetch(*, progress_callback):
        progress_callback(next_snapshot, 1, 1)
        return next_snapshot

    monkeypatch.setattr(expiries, "fetch_expiry_contracts", fetch)

    def fail_insert(connection, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO expiry_contracts"):
            preview = main.expiry_refresh_job.view(date(2026, 10, 21))
            assert preview["partial"] is True
            assert preview["refresh"]["stage"] == "saving"
            assert preview["items"][0]["ins_code"] == "1001"
            raise OperationalError(statement, params, RuntimeError("private database path: disk full"))

    event.listen(storage.engine, "before_cursor_execute", fail_insert)
    try:
        assert api_client.post("/api/expiries/refresh").status_code == 202
    finally:
        event.remove(storage.engine, "before_cursor_execute", fail_insert)
    failed = api_client.get("/api/expiries").json()
    assert failed["partial"] is False
    assert failed["refresh"]["running"] is False
    assert failed["refresh"]["stage"] == "failed"
    assert failed["refresh"]["last_error"]
    assert "private" not in failed["refresh"]["last_error"]
    assert failed["items"] == previous["items"]
    assert failed["fetched_at"] == previous["fetched_at"]
    assert storage.get_expiry_snapshot(date(2026, 10, 21)) == previous

    clock[0] += 60
    assert api_client.post("/api/expiries/refresh").status_code == 202
    recovered = api_client.get("/api/expiries").json()
    assert recovered["partial"] is False
    assert recovered["refresh"]["stage"] == "complete"
    assert recovered["refresh"]["last_error"] is None
    assert [row["ins_code"] for row in recovered["items"]] == ["1001"]
    assert recovered["warning"] is None
