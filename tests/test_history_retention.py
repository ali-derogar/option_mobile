"""Calendar retention, atomic pruning and post-refresh SQLite maintenance."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from threading import Event
from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import event, insert, select
from starlette.testclient import TestClient

from options.backend import pipeline
from options.backend.api import main
from options.backend.client import TsetmcAPIError
from options.backend.services import expiries, watchlist
from options.backend.services.expiry_selection import ExpirySnapshot
from options.backend.storage import (
    AppState,
    ClientTypeStats,
    Contract,
    ContractSnapshot,
    ExpiryContract,
    MARKET_TZ,
    MoneyFlowSnapshot,
    OpenInterestSnapshot,
    Storage,
    UnderlyingCatalog,
    WatchlistEntry,
)

NOW = datetime(2026, 10, 22, 9, tzinfo=timezone.utc)
HISTORY_MODELS = (ContractSnapshot, OpenInterestSnapshot, MoneyFlowSnapshot, ClientTypeStats)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    db = Storage(tmp_path / "retention.db")
    monkeypatch.setattr(db, "now", lambda: NOW)
    yield db
    db.engine.dispose()


def utc_start(day):
    return datetime.combine(day, time.min, tzinfo=MARKET_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def seed_history(storage, day, code=1):
    timestamp = utc_start(day)
    rec_date = int(day.strftime("%Y%m%d"))
    with storage.session() as session:
        session.add_all([
            ContractSnapshot(ins_code=code, snapshot_date=day.isoformat(), fetched_at=timestamp, updated_at=timestamp),
            OpenInterestSnapshot(ins_code=code, fetched_at=timestamp),
            MoneyFlowSnapshot(ins_code=code, rec_date=rec_date, fetched_at=timestamp),
            ClientTypeStats(ins_code=code, rec_date=rec_date, fetched_at=timestamp),
        ])
        session.commit()


def codes(storage, model):
    with storage.session() as session:
        return list(session.scalars(select(model.ins_code).order_by(model.ins_code)))


@pytest.mark.parametrize("now", [
    NOW,
    datetime(2026, 10, 21, 20, 29, 59, tzinfo=timezone.utc),
    datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc),
    datetime(2027, 1, 1, tzinfo=timezone.utc),
    datetime(2024, 3, 1, tzinfo=timezone.utc),
])
def test_retention_keeps_nineteen_days_and_removes_twenty_days_and_older(storage, monkeypatch, now):
    monkeypatch.setattr(storage, "now", lambda: now)
    today = now.astimezone(MARKET_TZ).date()
    for age in (0, 19, 20, 30):
        seed_history(storage, today - timedelta(days=age), code=age + 1)
    assert storage.cleanup_old_history() == {model.__tablename__: 2 for model in HISTORY_MODELS}
    for model in HISTORY_MODELS:
        assert codes(storage, model) == [1, 20]


def test_fetched_time_cutoff_is_tehran_midnight_not_utc_midnight(storage):
    cutoff = datetime(2026, 10, 2, 20, 30)
    with storage.session() as session:
        for model in (OpenInterestSnapshot, MoneyFlowSnapshot, ClientTypeStats):
            session.add_all([
                model(ins_code=1, fetched_at=cutoff - timedelta(microseconds=1)),
                model(ins_code=2, fetched_at=cutoff),
            ])
        session.commit()
    storage.cleanup_old_history()
    for model in (OpenInterestSnapshot, MoneyFlowSnapshot, ClientTypeStats):
        assert codes(storage, model) == [2]


@pytest.mark.parametrize("model", [MoneyFlowSnapshot, ClientTypeStats])
def test_record_date_takes_priority_and_invalid_dates_fall_back_to_fetch_day(storage, model):
    old = utc_start(date(2026, 9, 22))
    recent = utc_start(date(2026, 10, 3))
    cases = [
        (1, 20260922, recent, False),  # An old day downloaded today is still old.
        (2, 20261003, old, True),
        (3, None, recent, True), (4, None, old, False),
        (5, 20260230, recent, True), (6, 20260230, old, False),
        (7, 20261301, recent, True), (8, 20261301, old, False),
        (9, 0, recent, True), (10, 1, old, False),
    ]
    with storage.session() as session:
        session.add_all(model(ins_code=code, rec_date=rec_date, fetched_at=fetched) for code, rec_date, fetched, _ in cases)
        session.commit()
    result = storage.cleanup_old_history()
    assert result[model.__tablename__] == 5
    assert codes(storage, model) == [code for code, _, _, keep in cases if keep]


def test_cleanup_cutoff_is_fixed_even_if_the_day_changes_during_maintenance(storage, monkeypatch):
    seed_history(storage, date(2026, 10, 3))
    ticks = iter([NOW, NOW + timedelta(days=1)])
    monkeypatch.setattr(storage, "now", lambda: next(ticks))
    assert not any(storage.cleanup_old_history().values())
    for model in HISTORY_MODELS:
        assert codes(storage, model) == [1]


def test_recorded_history_pruning_handles_multiple_batches_without_skipping_rows(storage):
    with storage.session() as session:
        for model in (MoneyFlowSnapshot, ClientTypeStats):
            session.execute(insert(model), [
                {"ins_code": code, "rec_date": 20261002 if code % 2 else 20261003, "fetched_at": NOW}
                for code in range(1, 1208)
            ])
        session.commit()
    result = storage.cleanup_old_history()
    for model in (MoneyFlowSnapshot, ClientTypeStats):
        assert result[model.__tablename__] == 604
        assert codes(storage, model) == list(range(2, 1208, 2))


def test_current_data_and_user_preferences_are_preserved(storage):
    seed_history(storage, date(2026, 9, 22))
    timestamp = utc_start(date(2026, 9, 22))
    with storage.session() as session:
        session.add_all([
            Contract(ins_code=100, symbol="current", fetched_at=timestamp, updated_at=timestamp),
            ExpiryContract(ins_code=101, symbol="expiry", option_type="call", end_date=20270101),
            UnderlyingCatalog(ins_code=200, symbol="asset", fetched_at=timestamp),
            WatchlistEntry(ins_code=200, created_at=timestamp),
            AppState(key="activated", value="1", updated_at=timestamp),
            AppState(key="preferences", value=json.dumps({"theme": "dark"}), updated_at=timestamp),
        ])
        session.commit()
    storage.cleanup_old_history()
    for model, expected in ((Contract, [100]), (ExpiryContract, [101]), (UnderlyingCatalog, [200]), (WatchlistEntry, [200])):
        assert codes(storage, model) == expected
    assert storage.is_activated()
    with storage.session() as session:
        assert json.loads(session.get(AppState, "preferences").value) == {"theme": "dark"}


def test_failure_in_last_delete_rolls_back_all_tables_and_retries_next_time(storage, caplog):
    seed_history(storage, date(2026, 9, 22))
    with storage.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_cleanup BEFORE DELETE ON client_type_stats "
            "BEGIN SELECT RAISE(ABORT, 'forced deletion failure'); END"
        )
    assert storage.cleanup_old_history() is None
    for model in HISTORY_MODELS:
        assert codes(storage, model) == [1]
    with storage.session() as session:
        assert session.get(AppState, "history_retention") is None
    assert "History cleanup failed" in caplog.text
    with storage.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER fail_cleanup")
    assert storage.cleanup_old_history() == {model.__tablename__: 1 for model in HISTORY_MODELS}


def test_empty_maintenance_state_does_not_prevent_cleanup(storage):
    seed_history(storage, date(2026, 9, 22))
    with storage.session() as session:
        session.add(AppState(key="history_retention", value=None, updated_at=NOW))
        session.commit()
    assert storage.cleanup_old_history() == {model.__tablename__: 1 for model in HISTORY_MODELS}
    for model in HISTORY_MODELS:
        assert codes(storage, model) == []


@pytest.mark.parametrize("transaction", ["BEGIN IMMEDIATE", "BEGIN"])
def test_sqlite_lock_leaves_history_intact_and_cleanup_recovers(storage, monkeypatch, caplog, transaction):
    seed_history(storage, date(2026, 9, 22))
    vacuum = MagicMock(wraps=storage._vacuum_history)
    monkeypatch.setattr(storage, "_vacuum_history", vacuum)
    with sqlite3.connect(storage.db_path) as blocker:
        blocker.execute(transaction)
        # A shared reader allows DELETE but blocks COMMIT; a reserved writer
        # blocks DELETE itself. Both failure points must release pooled locks.
        blocker.execute("SELECT count(*) FROM contract_snapshots").fetchone()
        try:
            assert storage.cleanup_old_history() is None
            for model in HISTORY_MODELS:
                assert blocker.execute(f"SELECT count(*) FROM {model.__tablename__}").fetchone() == (1,)
            vacuum.assert_not_called()
        finally:
            blocker.rollback()
    assert "database is locked" in caplog.text
    with sqlite3.connect(storage.db_path, timeout=0) as independent_reader:
        for model in HISTORY_MODELS:
            assert independent_reader.execute(f"SELECT count(*) FROM {model.__tablename__}").fetchone() == (1,)
    assert storage.cleanup_old_history() == {model.__tablename__: 1 for model in HISTORY_MODELS}
    vacuum.assert_called_once_with()


def test_compaction_failure_is_retried_after_restart_even_without_more_deletions(storage, monkeypatch, caplog):
    seed_history(storage, date(2026, 9, 22))
    seed_history(storage, date(2026, 10, 22), code=2)
    monkeypatch.setattr(storage, "_vacuum_history", MagicMock(side_effect=RuntimeError("disk unavailable")))
    assert sum(storage.cleanup_old_history().values()) == 4
    for model in HISTORY_MODELS:
        assert codes(storage, model) == [2]
    assert "History compaction failed" in caplog.text
    reopened = Storage(storage.db_path)
    try:
        monkeypatch.setattr(reopened, "now", lambda: NOW)
        vacuum = MagicMock(wraps=reopened._vacuum_history)
        monkeypatch.setattr(reopened, "_vacuum_history", vacuum)
        assert not any(reopened.cleanup_old_history().values())
        vacuum.assert_called_once_with()
        with reopened.session() as session:
            assert session.get(AppState, "history_retention").value == "clean"
        reopened.cleanup_old_history()
        vacuum.assert_called_once_with()
    finally:
        reopened.engine.dispose()


def test_compaction_runs_outside_transaction_and_without_deletions_is_skipped(storage, monkeypatch):
    vacuum = MagicMock(wraps=storage._vacuum_history)
    monkeypatch.setattr(storage, "_vacuum_history", vacuum)
    storage.cleanup_old_history()
    vacuum.assert_not_called()
    inspected = []

    def check_transaction(connection, cursor, statement, params, context, many):
        if statement == "VACUUM":
            inspected.append(connection.connection.dbapi_connection.in_transaction)

    event.listen(storage.engine, "before_cursor_execute", check_transaction)
    try:
        seed_history(storage, date(2026, 9, 22))
        storage.cleanup_old_history()
    finally:
        event.remove(storage.engine, "before_cursor_execute", check_transaction)
    vacuum.assert_called_once_with()
    assert inspected == [False]
    with storage.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_compaction_does_not_clear_newer_pending_work_from_another_process(storage, monkeypatch):
    seed_history(storage, date(2026, 9, 22))
    original_vacuum = storage._vacuum_history

    def overlapping_prune():
        original_vacuum()
        # Model a second process committing its prune between our VACUUM and
        # acknowledgement. That process's compaction still needs to be retried.
        seed_history(storage, date(2026, 9, 22), code=2)
        storage._prune_history(date(2026, 10, 3))

    monkeypatch.setattr(storage, "_vacuum_history", overlapping_prune)
    storage.cleanup_old_history()
    with storage.session() as session:
        assert session.get(AppState, "history_retention").value.startswith("pending_vacuum:")
    retry = MagicMock(wraps=original_vacuum)
    monkeypatch.setattr(storage, "_vacuum_history", retry)
    assert not any(storage.cleanup_old_history().values())
    retry.assert_called_once_with()


def test_reopening_never_resurrects_pruned_history_and_explicit_import_stays_available(storage):
    old = date(2026, 9, 22)
    seed_history(storage, old)
    with storage.session() as session:
        session.add(Contract(ins_code=100, fetched_at=utc_start(old), updated_at=utc_start(old)))
        session.commit()
    storage.cleanup_old_history()
    reopened = Storage(storage.db_path)
    try:
        assert reopened.get_available_snapshot_dates() == []
        assert codes(reopened, Contract) == [100]
        # Reading history is still allowed to download an old day explicitly.
        reopened.insert_contract_snapshot([{"ins_code": 100}], snapshot_date=old.isoformat())
        assert reopened.get_available_snapshot_dates() == [old.isoformat()]
        reopened.now = lambda: NOW
        reopened.cleanup_old_history()
        assert reopened.get_available_snapshot_dates() == []
    finally:
        reopened.engine.dispose()


def test_opening_and_reading_do_not_prune_existing_history(storage):
    seed_history(storage, date(2026, 9, 22))
    reopened = Storage(storage.db_path)
    try:
        assert reopened.get_available_snapshot_dates() == ["2026-09-22"]
        assert not reopened.get_contract_snapshot_df("2026-09-22").empty
        for model in HISTORY_MODELS:
            assert codes(reopened, model) == [1]
    finally:
        reopened.engine.dispose()


def test_compaction_reduces_a_large_database_and_keeps_it_healthy(storage):
    with storage.session() as session:
        session.execute(insert(ContractSnapshot), [
            {"ins_code": code, "snapshot_date": "2026-09-22", "fetched_at": NOW,
             "updated_at": NOW, "instrument_json": json.dumps({"description": "x" * 4096})}
            for code in range(1, 1501)
        ])
        session.commit()
    seed_history(storage, date(2026, 10, 22), code=2000)
    before = storage.db_path.stat().st_size
    result = storage.cleanup_old_history()
    assert result["contract_snapshots"] == 1500
    assert storage.db_path.stat().st_size < before / 2
    with sqlite3.connect(storage.db_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    for model in HISTORY_MODELS:
        assert codes(storage, model) == [2000]


def test_maintenance_serializes_storage_instances_and_preserves_concurrent_new_data(storage, monkeypatch):
    seed_history(storage, date(2026, 9, 22))
    other = Storage(storage.db_path)
    monkeypatch.setattr(other, "now", lambda: NOW)
    entered, release, second_started, pruning = (Event() for _ in range(4))
    original_vacuum = storage._vacuum_history

    def vacuum():
        entered.set()
        assert release.wait(3)
        original_vacuum()

    original_prune = other._prune_history

    def prune(day):
        pruning.set()
        return original_prune(day)

    def second():
        second_started.set()
        return other.cleanup_old_history()

    monkeypatch.setattr(storage, "_vacuum_history", vacuum)
    monkeypatch.setattr(other, "_prune_history", prune)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first_job = workers.submit(storage.cleanup_old_history)
            try:
                assert entered.wait(3)
                second_job = workers.submit(second)
                assert second_started.wait(3)
                assert not pruning.wait(0.1)
                seed_history(storage, date(2026, 10, 22), code=2)
            finally:
                release.set()
            assert sum(first_job.result(timeout=3).values()) == 4
            assert not any(second_job.result(timeout=3).values())
        for model in HISTORY_MODELS:
            assert codes(storage, model) == [2]
    finally:
        other.engine.dispose()


def configure_pipeline(monkeypatch, storage, source="authenticated", empty=False):
    client = MagicMock()
    monkeypatch.setattr(pipeline, "Storage", lambda: storage)
    monkeypatch.setattr(pipeline, "validate_credentials", MagicMock(
        side_effect=ValueError("missing credentials") if source == "missing_credentials" else None,
    ))
    if source == "login_failure":
        client.login.side_effect = TsetmcAPIError("login failed")
    monkeypatch.setattr(pipeline, "TsetmcClient", lambda: client)
    monkeypatch.setattr(pipeline, "fetch_all_options", lambda *_: [] if empty else [{"InsCode": 1001, "UAInsCode": 2001}])
    monkeypatch.setattr(pipeline, "fetch_instruments", lambda *_, **kwargs: [])
    monkeypatch.setattr(pipeline, "fetch_trade_last_day", lambda *_, **kwargs: [])
    monkeypatch.setattr(pipeline, "_fetch_direct_instrument_info", lambda *_: {})
    monkeypatch.setattr(pipeline, "fetch_public_option_market_watch", lambda: [] if empty else [{
        "insCode_C": 1001, "uaInsCode": 2001, "endDate": 20261023,
    }])


@pytest.mark.parametrize("source", ["authenticated", "missing_credentials", "login_failure"])
@pytest.mark.parametrize("empty", [False, True])
def test_pipeline_prunes_once_only_after_successful_nonempty_storage(storage, monkeypatch, source, empty):
    seed_history(storage, date(2026, 9, 22))
    configure_pipeline(monkeypatch, storage, source, empty)
    cleanup = MagicMock(wraps=storage.cleanup_old_history)
    monkeypatch.setattr(storage, "cleanup_old_history", cleanup)
    result = pipeline.run_pipeline(skip_client_type=True)
    assert result["options"] == (0 if empty else 1)
    assert cleanup.call_count == (0 if empty else 1)
    assert storage.get_available_snapshot_dates() == (["2026-09-22"] if empty else ["2026-10-22"])


def test_failed_pipeline_does_not_prune(storage, monkeypatch):
    seed_history(storage, date(2026, 9, 22))
    configure_pipeline(monkeypatch, storage)
    monkeypatch.setattr(pipeline, "fetch_all_options", MagicMock(side_effect=requests.Timeout()))
    cleanup = MagicMock(wraps=storage.cleanup_old_history)
    monkeypatch.setattr(storage, "cleanup_old_history", cleanup)
    with pytest.raises(requests.Timeout):
        pipeline.run_pipeline(skip_client_type=True)
    cleanup.assert_not_called()
    assert storage.get_available_snapshot_dates() == ["2026-09-22"]


@pytest.mark.parametrize("kind", ["catalog", "expiries"])
@pytest.mark.parametrize("failed", [False, True])
def test_background_jobs_prune_after_commit_and_before_completion(storage, monkeypatch, kind, failed):
    seed_history(storage, date(2026, 9, 22))
    if kind == "catalog":
        rows = [{"ins_code": 2001, "symbol": "asset"}]
        monkeypatch.setattr(watchlist, "fetch_catalog", MagicMock(
            side_effect=requests.Timeout() if failed else None, return_value=(rows, 0),
        ))
        job = watchlist.CatalogRefresh()
    else:
        rows = [{"ins_code": 1001, "symbol": "option", "option_type": "call", "end_date": 20261023}]
        monkeypatch.setattr(expiries, "fetch_expiry_contracts", MagicMock(
            side_effect=requests.Timeout() if failed else None, return_value=ExpirySnapshot(rows),
        ))
        job = expiries.ExpiryRefresh()

    def cleanup():
        assert job.status()["running"] is True
        assert codes(storage, UnderlyingCatalog if kind == "catalog" else ExpiryContract)
        return original_cleanup()

    original_cleanup = storage.cleanup_old_history
    cleanup_mock = MagicMock(side_effect=cleanup)
    monkeypatch.setattr(storage, "cleanup_old_history", cleanup_mock)
    job.begin()
    job.run(storage)
    assert job.status()["running"] is False
    assert bool(job.status()["last_error"]) is failed
    assert cleanup_mock.call_count == (0 if failed else 1)
    assert storage.get_available_snapshot_dates() == (["2026-09-22"] if failed else [])


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_api_success_and_cooldown_preserve_public_response_and_cleanup_once(storage, monkeypatch, cleanup_fails):
    seed_history(storage, date(2026, 9, 22))
    configure_pipeline(monkeypatch, storage)
    monkeypatch.setattr(pipeline, "fetch_public_client_type_current_many", lambda *_: [])
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "_last_refresh_request_at", 0.0)
    monkeypatch.setattr(main, "_refresh_status", {"running": False})
    storage.set_activated()
    storage.replace_watchlist([2001])
    cleanup = MagicMock(wraps=storage.cleanup_old_history)
    monkeypatch.setattr(storage, "cleanup_old_history", cleanup)
    if cleanup_fails:
        with storage.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER fail_cleanup BEFORE DELETE ON client_type_stats "
                "BEGIN SELECT RAISE(ABORT, 'forced deletion failure'); END"
            )
    with TestClient(main.app, headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN}) as client:
        response = client.post("/api/refresh")
        assert response.status_code == 200
        assert response.json() == {"status": "started"}
        assert client.get("/api/dates").json()["items"] == (["2026-10-22", "2026-09-22"] if cleanup_fails else ["2026-10-22"])
        status = client.get("/api/refresh/status").json()
        assert status["last_error"] is None
        assert status["last_result"] == {"options": 1, "client_type_stats": 0, "money_flow": 0, "open_interest": 1}
        assert client.post("/api/refresh").json() == {"status": "cooldown"}
    cleanup.assert_called_once_with()
