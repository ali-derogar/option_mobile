"""Whole-market option sentiment: arithmetic, isolation, acquisition and API."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from unittest.mock import MagicMock
import json
from pathlib import Path

import pytest
from sqlalchemy import event
from starlette.testclient import TestClient

from options import app as app_module
from options.backend.analysis.option_sentiment import analyze_strikes
from options.backend.api import main
from options.backend.services import option_sentiment as service
from options.backend.services import historical_options
from options.backend.services.public_options import normalize_public_option_pairs
from options.backend.storage import Storage

DAY = "2026-10-21"
STAMP = "2026-10-22T08:00:00+00:00"


def contract(code="12345678901234567", **changes):
    return {"ins_code": code, "underlying_ins_code": "2001", "underlying_symbol": "خودرو",
            "symbol": "ضخود", "option_type": "call", "strike_price": 1000, "end_date": 20261209,
            "buy_open_positions": 50, "sell_open_positions": 50, "yesterday_open_positions": 40,
            "trade_value": 100_000_000, "underlying_last_price": 1100, "contract_size": 1000, **changes}


def sample():
    return [contract(), contract("12345678901234568", end_date=20270113, buy_open_positions=20, yesterday_open_positions=30),
            contract("12345678901234569", option_type="put", symbol="طخود", buy_open_positions=30, yesterday_open_positions=40, trade_value=50_000_000),
            contract("12345678901234570", strike_price=1200, buy_open_positions=10, yesterday_open_positions=0),
            contract("67241904692647336", underlying_ins_code="41625340598198551", underlying_symbol="بساما", trade_value=200_000_000)]


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "sentiment.db")
    yield db
    db.engine.dispose()


@pytest.fixture
def api_client(storage, monkeypatch):
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "option_sentiment_refresh_job", service.OptionSentimentRefresh())
    storage.set_activated()
    with TestClient(main.app, headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN}) as client:
        yield client


def test_strike_aggregation_dedup_signed_changes_and_three_scopes():
    rows = sample()
    result = analyze_strikes(rows + [rows[0]], "2001")
    assert [r["strike_price"] for r in result["strikes"]] == [1000, 1200]
    first = result["strikes"][0]
    assert (first["call_oi"], first["put_oi"], first["call_change"], first["put_change"]) == (70, 30, 0, -10)
    assert result["strikes"][1]["call_change"] == 10
    assert result["atm_strike"] == 1000  # equal distance chooses the lower strike
    assert result["peaks"]["call"]["value"] == 70
    assert result["summaries"]["market"]["total"]["value"] == 550_000_000
    assert result["summaries"]["underlying"]["total"]["value"] == 350_000_000
    assert result["summaries"]["selection"]["call_put_ratio"] == 6
    selected = analyze_strikes(rows, "2001", "20261209")
    assert selected["strikes"][0]["call_oi"] == 50
    assert selected["summaries"]["selection"]["total"]["value"] == 250_000_000
    assert selected["summaries"]["market"] == result["summaries"]["market"]


def test_missing_is_not_zero_and_partial_metrics_do_not_claim_valid_ratio():
    rows = [contract(buy_open_positions=None, sell_open_positions=None, trade_value=None),
            contract("2", buy_open_positions=0, yesterday_open_positions=0, trade_value=0)]
    result = analyze_strikes(rows, "2001")
    assert result["strikes"][0]["call_oi"] == 0
    assert result["strikes"][0]["coverage"]["call_oi"] == {"value": 0, "known": 1, "total": 2, "complete": False}
    assert result["strikes"][0]["put_oi"] == 0  # no put contract is an actual zero
    assert result["summaries"]["selection"]["ratio_state"] == "unavailable"
    missing = analyze_strikes([rows[0]], "2001")
    assert missing["strikes"][0]["call_oi"] is None
    assert missing["strikes"][0]["call_change"] is None


def test_no_snapshot_does_not_report_zero_turnover_for_unknown_scopes(storage):
    for day in (None, DAY):
        result = service.get_payload(storage, "2001", "all", day)
        for summary in result["summaries"].values():
            for name in ("call", "put", "total"):
                assert summary[name]["value"] is None
                assert not summary[name]["complete"]


def test_local_archive_does_not_claim_zero_for_a_different_asset(storage):
    storage.insert_contract_snapshot([contract()], snapshot_date=DAY)
    result = service.get_payload(storage, "missing", "all", DAY)
    assert result["summaries"]["underlying"]["total"]["value"] is None
    existing = service.get_payload(storage, "2001", "all", DAY)
    assert existing["summaries"]["underlying"]["call"]["value"] == 100_000_000
    assert existing["summaries"]["underlying"]["put"]["value"] == 0


def test_peak_identifies_incomplete_comparison_even_when_its_own_strike_is_complete():
    result = analyze_strikes([contract(), contract("2", strike_price=1200, buy_open_positions=None, sell_open_positions=None)], "2001")
    peak = result["peaks"]["call"]
    assert peak["value"] == 50
    assert peak["complete"]  # the winning strike itself has full data
    assert not peak["scope_complete"]  # another strike could have a larger unknown OI


@pytest.mark.parametrize("buy,sell,previous,expected,change", [(None, 30, 40, 30, -10), (0, 20, 0, 0, 0), (20, 20, None, 20, None)])
def test_oi_counts_one_side_and_requires_official_previous(buy, sell, previous, expected, change):
    item = analyze_strikes([contract(buy_open_positions=buy, sell_open_positions=sell, yesterday_open_positions=previous)], "2001")["strikes"][0]
    assert item["call_oi"] == expected
    assert item["call_change"] == change


@pytest.mark.parametrize("value,state", [(100, "infinite"), (0, "unavailable"), (None, "unavailable")])
def test_zero_denominator_json_safe(value, state):
    summary = analyze_strikes([contract(trade_value=value)], "2001")["summaries"]["selection"]
    assert summary["ratio_state"] == state
    assert summary["call_put_ratio"] is None


def test_unknown_type_invalid_strike_and_missing_spot_are_visible():
    result = analyze_strikes([contract(strike_price=None, underlying_last_price=None), contract("2", option_type="unknown", symbol="?")], "2001")
    assert result["atm_strike"] is None
    assert result["spot_price"] is None
    assert result["quality"]["invalid_strike_count"] == 1
    assert result["quality"]["unknown_type_count"] == 1
    assert result["summaries"]["market"]["ratio_state"] == "unavailable"


def test_spot_outside_strikes_zero_price_and_expiry_reset():
    assert analyze_strikes([contract(underlying_last_price=0)], "2001")["spot_price"] == 0
    assert analyze_strikes([contract(underlying_last_price=5000)], "2001")["atm_strike"] == 1000
    assert analyze_strikes(sample(), "2001", "20260101")["expiry"] == "all"
    assert analyze_strikes(sample(), "unknown")["strikes"] == []


def test_bad_expiry_is_not_offered_and_zero_underlying_code_uses_symbol():
    result = analyze_strikes([contract(end_date=0, underlying_ins_code=0)], "خودرو")
    assert result["underlying_key"] == "خودرو"
    assert result["expiries"] == []


def test_known_underlying_code_and_symbol_only_contracts_share_one_asset():
    rows = [contract(underlying_symbol="کیان"), contract("2", underlying_ins_code=None, underlying_symbol="كيان")]
    result = analyze_strikes(rows, "۲٬۰۰۱")
    assert result["underlying_key"] == "2001"
    assert len(result["underlyings"]) == 1
    assert result["quality"]["selected_contract_count"] == 2
    assert result["strikes"][0]["call_oi"] == 100


def test_ambiguous_symbol_only_contracts_cannot_be_merged_into_a_known_asset():
    rows = [contract(), contract("2", underlying_ins_code="2002"), contract("3", underlying_ins_code=None)]
    assert analyze_strikes(rows, "2001")["quality"]["selected_contract_count"] == 1
    assert analyze_strikes(rows, "2002")["quality"]["selected_contract_count"] == 1


def test_conflicting_underlying_prices_are_not_resolved_by_input_order():
    rows = [contract(), contract("2", underlying_last_price=9000)]
    for ordered in (rows, rows[::-1]):
        result = analyze_strikes(ordered, "2001")
        assert result["spot_price"] is None
        assert result["atm_strike"] is None
        assert result["quality"]["spot_price_conflict"] is True


def test_known_total_turnover_does_not_drop_contracts_with_unknown_option_type():
    result = analyze_strikes([contract(trade_value=100), contract("2", option_type=None, symbol="?", trade_value=50)], "2001")
    total = result["summaries"]["market"]["total"]
    assert total["value"] == 150
    assert total["known"] == total["total"] == 2
    assert total["complete"] is True
    assert result["summaries"]["market"]["call"]["complete"] is False
    assert result["summaries"]["market"]["ratio_state"] == "unavailable"


@pytest.mark.parametrize("codes", [["²"], ["12345678901234567", "٢"]])
def test_unsupported_numeric_identifier_characters_do_not_crash_analysis(codes):
    result = analyze_strikes([contract(code) for code in codes], "2001")
    assert result["quality"]["contract_count"] == (0 if codes == ["²"] else 2)


def test_finite_turnover_values_cannot_produce_non_finite_json_totals_or_ratios():
    for rows in ([contract(trade_value=1e308), contract("2", trade_value=1e308)],
                 [contract(trade_value=1e308), contract("2", option_type="put", trade_value=1e-308)]):
        result = analyze_strikes(rows, "2001")
        json.dumps(result, allow_nan=False)
        assert result["summaries"]["market"]["call_put_ratio"] is None


def test_historical_response_rejects_an_explicit_wrong_session(monkeypatch):
    monkeypatch.setattr(historical_options, "fetch_instruments_history_in_day", lambda day: [{"dEven": 20261019}])
    with pytest.raises(ValueError, match="date"):
        historical_options.fetch_public_option_history_contracts("2026-10-20")


def test_snapshot_atomic_failure_restart_and_no_watchlist_mutation(storage):
    storage.replace_watchlist([2001])
    storage.replace_option_sentiment_snapshot(DAY, sample(), STAMP, "public_cdn")
    def reject(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("UPDATE option_sentiment_snapshots"):
            raise RuntimeError("disk error")
    event.listen(storage.engine, "before_cursor_execute", reject)
    try:
        with pytest.raises(RuntimeError):
            storage.replace_option_sentiment_snapshot(DAY, [contract(trade_value=1)], STAMP, "public_cdn")
    finally:
        event.remove(storage.engine, "before_cursor_execute", reject)
    reopened = Storage(storage.db_path)
    try:
        assert len(reopened.get_option_sentiment_snapshot()["contracts"]) == 5
        assert reopened.get_watchlist_codes() == [2001]
        assert reopened.get_contracts_df().empty
        assert "option_sentiment_snapshots" in app_module.USER_DATA_TABLES
        assert app_module._database_score(storage.db_path) > 0
    finally:
        reopened.engine.dispose()


def test_history_cannot_replace_live_oi_and_latest_ignores_history(storage):
    storage.replace_option_sentiment_snapshot(DAY, sample(), STAMP, "public_cdn")
    storage.replace_option_sentiment_snapshot(DAY, [contract(buy_open_positions=None)], STAMP, "public_history")
    storage.replace_option_sentiment_snapshot("2026-10-22", [contract()], STAMP, "public_history")
    assert storage.get_option_sentiment_snapshot()["data_date"] == DAY
    assert len(storage.get_option_sentiment_snapshot(DAY)["contracts"]) == 5


def test_retention_uses_tehran_days(storage, monkeypatch):
    monkeypatch.setattr(storage, "now", lambda: datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc))
    for age in (0, 19, 20):
        day = (date(2026, 10, 22) - timedelta(days=age)).isoformat()
        storage.replace_option_sentiment_snapshot(day, [contract()], STAMP, "public_cdn")
    assert storage.cleanup_old_history()["option_sentiment_snapshots"] == 1
    assert storage.get_option_sentiment_dates() == ["2026-10-22", "2026-10-03"]


def test_market_date_requires_both_exchanges_and_actual_date(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value.json.return_value = {"marketOverview": {"marketActivityDEven": 20261021}}
    monkeypatch.setattr(service, "_session", lambda: session)
    assert service.fetch_market_date() == DAY
    assert {c.args[0] for c in session.get.call_args_list} == {service.MARKET_OVERVIEW_URL.format(market=m) for m in (1, 2)}
    def response(url, **kwargs):
        r = MagicMock()
        r.json.return_value = {"marketOverview": {"marketActivityDEven": 20261021 if url.endswith("/1") else 20261020}}
        return r
    session.get.side_effect = response
    with pytest.raises(ValueError, match="inconsistent"):
        service.fetch_market_date()


def test_live_acquisition_reuses_normalization_corrects_metadata_and_keeps_zero_volume(monkeypatch):
    feed = [{"insCode_C": "12345678901234567", "insCode_P": "67241904692647336", "uaInsCode": "41625340598198551",
             "lval30_UA": "بساما", "endDate": 20270113, "strikePrice": 1000, "oP_C": 10, "oP_P": 0,
             "yesterdayOP_C": 20, "yesterdayOP_P": 0, "qTotCap_C": 100, "qTotCap_P": 0, "qTotTran5J_P": 0}]
    monkeypatch.setattr(service, "fetch_market_date", lambda: DAY)
    monkeypatch.setattr(service, "fetch_public_option_market_watch", lambda: feed + feed)
    monkeypatch.setattr(service, "fetch_public_instrument_info_many", lambda codes: {12345678901234567: {"lVal30": "اختیارخ بساما-1200-1405/09/18"}})
    result = service.fetch_current_snapshot()
    assert len(result["contracts"]) == 2
    assert result["contracts"][0]["strike_price"] == 1200
    assert result["contracts"][0]["ins_code"] == "12345678901234567"
    assert result["contracts"][1]["buy_open_positions"] == 0
    assert result["data_date"] == DAY


def test_date_change_or_empty_feed_never_publishes(storage, monkeypatch):
    storage.replace_option_sentiment_snapshot(DAY, sample(), STAMP, "public_cdn")
    monkeypatch.setattr(service, "fetch_market_date", lambda: DAY)
    monkeypatch.setattr(service, "fetch_public_option_market_watch", lambda: [])
    job = service.OptionSentimentRefresh()
    assert job.begin()["status"] == "started"
    job.run(storage)
    assert job.status()["last_error"]
    assert len(storage.get_option_sentiment_snapshot()["contracts"]) == 5


def test_market_session_change_during_metadata_is_rejected(monkeypatch):
    dates = iter([DAY, "2026-10-22"])
    monkeypatch.setattr(service, "fetch_market_date", lambda: next(dates))
    monkeypatch.setattr(service, "fetch_public_option_market_watch", lambda: [{"insCode_C": "1", "uaInsCode": "2001", "endDate": 20261209}])
    monkeypatch.setattr(service, "fetch_public_instrument_info_many", lambda codes: {})
    with pytest.raises(ValueError, match="changed"):
        service.fetch_current_snapshot()


def test_history_overlays_only_same_date_local_oi_without_client_type(storage, monkeypatch):
    storage.insert_contract_snapshot([contract(buy_open_positions=70)], snapshot_date=DAY)
    storage.insert_contract_snapshot([contract(buy_open_positions=999)], snapshot_date="2026-10-20")
    monkeypatch.setattr(service, "fetch_public_option_history_contracts", lambda *args: [contract(buy_open_positions=None, sell_open_positions=None, yesterday_open_positions=None, trade_value=222)])
    forbidden = MagicMock(side_effect=AssertionError("unnecessary client type"))
    monkeypatch.setattr(historical_options, "fetch_public_client_type_for_date_many", forbidden)
    result = service.fetch_historical_snapshot(storage, DAY)
    assert result["contracts"][0]["buy_open_positions"] == 70
    assert result["contracts"][0]["trade_value"] == 222
    forbidden.assert_not_called()


@pytest.mark.parametrize("has_historical_underlying", [True, False])
def test_history_uses_current_metadata_without_copying_current_prices_or_oi(storage, monkeypatch, has_historical_underlying):
    storage.replace_option_sentiment_snapshot(DAY, [contract(buy_open_positions=999, underlying_last_price=9000)], STAMP, "public_cdn")
    rows = [{"insCode": 12345678901234567, "lVal18AFC": "ضخود", "lVal30": "اختیارخ خودرو-1000-1405/09/18",
             "dEven": 0, "pDrCotVal": 25, "qTotCap": 222}]
    if has_historical_underlying:
        rows.append({"insCode": 2001, "lVal18AFC": "خودرو", "pDrCotVal": 800, "pClosing": 790})
    monkeypatch.setattr(historical_options, "fetch_instruments_history_in_day", lambda day: rows)
    result = service.fetch_historical_snapshot(storage, "2026-10-20")
    analysis = analyze_strikes(result["contracts"], "2001")
    assert analysis["underlying_key"] == "2001"
    assert analysis["spot_price"] == (800 if has_historical_underlying else None)
    assert analysis["strikes"][0]["call_oi"] is None
    assert analysis["strikes"][0]["call_change"] is None
    assert analysis["summaries"]["selection"]["call"]["value"] == 222
    assert storage.get_option_sentiment_snapshot()["contracts"][0]["buy_open_positions"] == 999


def test_refresh_coalescing_and_cooldown(monkeypatch):
    monkeypatch.setattr(service.time, "monotonic", lambda: 100)
    job = service.OptionSentimentRefresh()
    assert job.begin()["status"] == "started"
    assert job.begin(DAY)["status"] == "already_running"
    job._status["running"] = False
    assert job.begin()["status"] == "cooldown"
    monkeypatch.setattr(service.time, "monotonic", lambda: 160)
    assert job.begin()["status"] == "started"


def test_api_whole_market_and_historical_missing_oi(api_client, storage):
    storage.replace_watchlist([2001])
    storage.replace_option_sentiment_snapshot(DAY, sample(), STAMP, "public_cdn")
    response = api_client.get("/api/option-sentiment?underlying_key=2001&expiry=20261209")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["underlyings"]) == 2
    assert payload["data_date"] == DAY
    assert payload["summaries"]["market"]["total"]["value"] == 550_000_000
    assert storage.get_watchlist_codes() == [2001]
    old = "2026-10-20"
    storage.replace_option_sentiment_snapshot(old, [contract(buy_open_positions=None, sell_open_positions=None, yesterday_open_positions=None)], STAMP, "public_history")
    payload = api_client.get(f"/api/option-sentiment?date={old}&underlying_key=2001").json()
    assert payload["quality"]["oi"]["known"] == 0
    assert payload["summaries"]["selection"]["call"]["value"] == 100_000_000


@pytest.mark.parametrize("query", ["date=bad", "date=2099-01-01", "expiry=bad", "expiry=20260229"])
def test_api_rejects_invalid_filters(api_client, query):
    assert api_client.get(f"/api/option-sentiment?{query}").status_code == 400


def test_refresh_api_background_and_history_cache(api_client, storage, monkeypatch):
    monkeypatch.setattr(service, "fetch_current_snapshot", lambda: {"data_date": DAY, "received_at": STAMP, "source": "public_cdn", "warning": None, "contracts": sample()})
    assert api_client.post("/api/option-sentiment/refresh", json={}).status_code == 202
    assert api_client.get("/api/option-sentiment").json()["cached"]
    assert api_client.post("/api/option-sentiment/refresh", json={}).json()["status"] == "cooldown"
    assert api_client.post("/api/option-sentiment/refresh", json={"date": DAY}).json()["status"] == "cached"
    assert api_client.post("/api/option-sentiment/refresh", json={"date": 123}).status_code == 400


@pytest.mark.parametrize("method,path", [("get", "/api/option-sentiment"), ("post", "/api/option-sentiment/refresh")])
def test_routes_obey_security_and_activation(api_client, storage, method, path):
    with TestClient(main.app) as anonymous:
        assert getattr(anonymous, method)(path).status_code == 403
    storage.set_activated(False)
    assert getattr(api_client, method)(path).status_code == 423


def test_slow_analysis_does_not_block_health(api_client, monkeypatch):
    entered, release = Event(), Event()
    original = main.get_option_sentiment_payload
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(main, "get_option_sentiment_payload", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(api_client.get, "/api/option-sentiment")
        try:
            assert entered.wait(2)
            assert pool.submit(api_client.get, "/api/health").result(timeout=2).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=2).status_code == 200


def test_fixed_official_market_response_matches_all_expiries_and_each_strike():
    fixture = json.loads((Path(__file__).parent / "fixtures/option_sentiment_real_market.json").read_text())
    contracts = normalize_public_option_pairs(fixture["rows"])
    key = str(fixture["rows"][0]["uaInsCode"])
    for expiry, expected in fixture["expected"].items():
        result = analyze_strikes(contracts, key, expiry)
        assert [{k: row[k] for k in ("strike_price", "call_oi", "put_oi", "call_change", "put_change")} for row in result["strikes"]] == expected["strikes"]
        summary = result["summaries"]["selection"]
        assert summary["call"]["value"] == expected["call_value"]
        assert summary["put"]["value"] == expected["put_value"]
