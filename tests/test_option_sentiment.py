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
from options.backend.analysis.option_sentiment import analyze_participant_flow, analyze_strikes, contract_scope
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
    monkeypatch.setattr(service, "fetch_public_live_quotes", lambda codes, day: {
        code: {"trade_value": 0, "trade_volume": 0} for code in codes})
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


@pytest.mark.parametrize("metadata_outage", [False, True])
def test_missing_supplementary_metadata_is_logged_without_a_public_warning(monkeypatch, caplog, metadata_outage):
    monkeypatch.setattr(service, "fetch_public_live_quotes", lambda codes, day: {
        code: {"trade_value": 0, "trade_volume": 0} for code in codes})
    caplog.set_level("INFO", logger=service.__name__)
    feed = [{"insCode_C": "1", "insCode_P": "2", "uaInsCode": "2001", "lval30_UA": "خودرو",
             "endDate": 20261209, "strikePrice": 1000, "oP_C": 50, "oP_P": 20}]
    monkeypatch.setattr(service, "fetch_market_date", lambda: DAY)
    monkeypatch.setattr(service, "fetch_public_option_market_watch", lambda: feed)
    metadata = MagicMock(side_effect=RuntimeError("metadata unavailable")) if metadata_outage else MagicMock(return_value={})
    monkeypatch.setattr(service, "fetch_public_instrument_info_many", metadata)

    result = service.fetch_current_snapshot()

    assert result["warning"] is None
    assert result["market_complete"] is True
    assert len(result["contracts"]) == 2
    assert result["contracts"][0]["buy_open_positions"] == 50
    assert "metadata unavailable for 2 option sentiment contracts" in caplog.text


def test_invalid_contract_warning_survives_metadata_fallback(monkeypatch, caplog):
    monkeypatch.setattr(service, "fetch_public_live_quotes", lambda codes, day: {
        code: {"trade_value": 0, "trade_volume": 0} for code in codes})
    caplog.set_level("INFO", logger=service.__name__)
    feed = [{"insCode_C": str(code), "uaInsCode": "2001", "endDate": expiry, "strikePrice": 1000}
            for code, expiry in [(1, 20261209), (2, 20260229)]]
    monkeypatch.setattr(service, "fetch_market_date", lambda: DAY)
    monkeypatch.setattr(service, "fetch_public_option_market_watch", lambda: feed)
    monkeypatch.setattr(service, "fetch_public_instrument_info_many", lambda codes: {})

    result = service.fetch_current_snapshot()

    assert "نامعتبر" in result["warning"]
    assert "مشخصات تکمیلی" not in result["warning"]
    assert result["market_complete"] is False
    assert len(result["contracts"]) == 1
    assert "metadata unavailable for 2 option sentiment contracts" in caplog.text


@pytest.mark.parametrize("notice", [
    "مشخصات تکمیلی 23 قرارداد دریافت نشد؛ اطلاعات معتبر خوراک بازار استفاده شد.",
    "مشخصات تکمیلی 8 قرارداد دریافت نشد؛ از اطلاعات معتبر خوراک بازار استفاده شد.",
    "مشخصات تکمیلی ۲۳ قرارداد دریافت نشد؛ اطلاعات معتبر خوراک بازار استفاده شد.",
])
@pytest.mark.parametrize("data_warning", [None, "پوشش بازار ناقص است."])
def test_saved_metadata_diagnostic_is_hidden_without_losing_data_quality_warnings(api_client, storage, notice, data_warning):
    saved_warning = " ".join(filter(None, [notice, data_warning]))
    storage.replace_option_sentiment_snapshot(DAY, sample(), STAMP, "public_cdn", saved_warning,
                                              market_complete=data_warning is None)

    response = api_client.get("/api/option-sentiment?underlying_key=2001").json()

    assert response["warning"] == data_warning
    assert response["quality"]["selected_contract_count"] > 0
    if data_warning:
        assert response["summaries"]["market"]["total"]["complete"] is False
    assert storage.get_option_sentiment_snapshot()["warning"] == saved_warning


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
    monkeypatch.setattr(service, "fetch_current_snapshot", lambda: {"data_date": DAY, "received_at": STAMP, "source": "public_cdn", "warning": None, "contracts": [{**row, "trade_date": DAY} for row in sample()]})
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


@pytest.mark.parametrize("fixture_name", [
    "option_sentiment_real_market.json",
    "option_sentiment_real_market_20261025.json",
])
def test_fixed_official_market_response_matches_all_expiries_and_each_strike(fixture_name):
    fixture = json.loads((Path(__file__).parent / "fixtures" / fixture_name).read_text())
    contracts = normalize_public_option_pairs(fixture["rows"])
    key = str(fixture["rows"][0]["uaInsCode"])
    for expiry, expected in fixture["expected"].items():
        result = analyze_strikes(contracts, key, expiry)
        assert [{k: row[k] for k in ("strike_price", "call_oi", "put_oi", "call_change", "put_change")} for row in result["strikes"]] == expected["strikes"]
        summary = result["summaries"]["selection"]
        assert summary["call"]["value"] == expected["call_value"]
        assert summary["put"]["value"] == expected["put_value"]


def test_activity_matrix_preserves_expiries_types_and_selection_without_double_counting():
    rows = sample()
    result = analyze_strikes(rows + [rows[0]], '2001')
    matrix = result['activity']
    assert matrix['strikes'] == [1000, 1200]
    assert matrix['expiries'] == ['20261209', '20270113']
    assert len(matrix['cells']) == 3  # an absent strike/expiry is not a zero-valued contract
    cells = {(r['strike_price'], r['expiry']): r for r in matrix['cells']}
    first = cells[1000, '20261209']
    assert first['call']['value'] == 100_000_000
    assert first['put']['value'] == 50_000_000
    assert first['total']['value'] == 150_000_000
    assert sum(r['total']['value'] for r in matrix['cells']) == result['summaries']['underlying']['total']['value']
    selected = analyze_strikes(rows, '2001', '20261209')['activity']
    assert selected['expiries'] == ['20261209']
    assert len(selected['cells']) == 2
    assert analyze_strikes(rows, 'missing')['activity']['cells'] == []


def test_activity_matrix_distinguishes_missing_zero_partial_and_unknown_type():
    rows = [contract(trade_value=None), contract('2', trade_value=0),
            contract('3', option_type='unknown', symbol='?', trade_value=10),
            contract('4', strike_price=None), contract('5', end_date=0)]
    cell = analyze_strikes(rows, '2001')['activity']['cells'][0]
    assert cell['call']['value'] == 0 and not cell['call']['complete']
    assert cell['total']['value'] == 10 and not cell['total']['complete']
    assert cell['put']['value'] is None and not cell['put']['complete']
    matrix = analyze_strikes([contract(trade_value=0)], '2001')['activity']
    assert matrix['cells'][0]['total']['complete']
    assert matrix['cells'][0]['total']['value'] == 0


def test_flow_is_signed_buy_minus_sell_per_group_and_contract_type_not_oi():
    rows = contract_scope(sample(), '2001', '20261209')['selection']
    participants = [
        {'ins_code': rows[0]['ins_code'], 'natural_buy_value': 90, 'natural_sell_value': 40,
         'legal_buy_value': 30, 'legal_sell_value': 80},
        {'ins_code': rows[1]['ins_code'], 'natural_buy_value': 10, 'natural_sell_value': 70,
         'legal_buy_value': 70, 'legal_sell_value': 10},
        {'ins_code': rows[2]['ins_code'], 'natural_buy_value': 0, 'natural_sell_value': 0,
         'legal_buy_value': 0, 'legal_sell_value': 0},
    ]
    result = analyze_participant_flow(rows + [contract('99', option_type='unknown', symbol='?')], participants)
    natural = result['audiences']['natural']['value']
    legal = result['audiences']['legal']['value']
    assert [(r['call_flow'], r['put_flow']) for r in natural['strikes']] == [(50, -60), (0, 0)]
    assert [(r['call_flow'], r['put_flow']) for r in legal['strikes']] == [(-50, 60), (0, 0)]
    assert natural['coverage']['complete']
    assert natural['coverage']['value'] == -10


def test_flow_missing_sides_partial_groups_and_overflow_cannot_become_zero_or_signal():
    rows = [contract(), contract('2'), contract('3', option_type='put')]
    participants = [
        {'ins_code': rows[0]['ins_code'], 'natural_buy_value': 50, 'natural_sell_value': 10},
        {'ins_code': '2', 'natural_buy_value': 20},
        {'ins_code': '3', 'natural_buy_value': 10, 'natural_sell_value': 70},
    ]
    result = analyze_participant_flow(rows, participants)['audiences']
    strike = result['natural']['value']['strikes'][0]
    assert strike['call_flow'] is None
    assert strike['coverage']['call_flow']['known'] == 1
    assert strike['coverage']['call_flow']['total'] == 2
    assert strike['put_flow'] == -60
    assert result['natural']['value']['coverage']['value'] is None
    assert result['legal']['value']['strikes'][0]['put_flow'] is None
    overflow = analyze_participant_flow(rows[:2], [
        {'ins_code': r['ins_code'], 'natural_buy_value': 1e308, 'natural_sell_value': 0} for r in rows[:2]
    ])
    assert overflow['audiences']['natural']['value']['strikes'][0]['call_flow'] is None
    json.dumps(overflow, allow_nan=False)


@pytest.mark.parametrize('volume,value,expected', [(0, 0, 0), (None, 0, None), (0, None, None), (1, 0, None)])
def test_only_confirmed_non_trading_contracts_infer_zero_flow(volume, value, expected):
    result = analyze_participant_flow([contract(trade_volume=volume, trade_value=value)], [])
    assert result['audiences']['natural']['value']['strikes'][0]['call_flow'] == expected


def test_flow_acquisition_is_scoped_date_checked_and_uses_same_day_local_fallback(storage, monkeypatch):
    storage.replace_option_sentiment_snapshot(DAY, sample(), STAMP, 'public_cdn')
    storage.insert_client_type_stats([
        {'ins_code': 12345678901234569, 'rec_date': 20261021, 'natural_buy_value': 10, 'natural_sell_value': 30,
         'legal_buy_value': 49_999_990, 'legal_sell_value': 49_999_970},
        {'ins_code': 12345678901234570, 'rec_date': 20261020, 'natural_buy_value': 999, 'natural_sell_value': 0},
    ])
    requested = []
    def remote(codes, day, **kwargs):
        requested.append((codes, day, kwargs))
        return [
            {'ins_code': 12345678901234567, 'rec_date': 20261021, 'natural_buy_value': 80, 'natural_sell_value': 10,
             'legal_buy_value': 99_999_920, 'legal_sell_value': 99_999_990},
            {'ins_code': 12345678901234570, 'rec_date': 20261020, 'natural_buy_value': 999, 'natural_sell_value': 0},
            {'ins_code': 67241904692647336, 'rec_date': 20261021, 'natural_buy_value': 999, 'natural_sell_value': 0},
        ]
    monkeypatch.setattr(service, 'fetch_public_client_type_for_date_many', remote)
    result = service.get_flow_payload(storage, 'خودرو', '20261209', DAY)
    assert requested == [([12345678901234567, 12345678901234569, 12345678901234570], '20261021', {'timeout': 8})]
    assert result['data_date'] == DAY and result['underlying_key'] == '2001'
    assert result['expiry'] == '20261209'
    strikes = result['audiences']['natural']['value']['strikes']
    assert strikes[0]['call_flow'] == 70
    assert strikes[0]['put_flow'] == -20
    assert strikes[1]['call_flow'] is None
    assert result['audiences']['natural']['value']['coverage']['known'] == 2
    assert storage.get_watchlist_codes() == []


def test_flow_api_requires_explicit_valid_date_and_retains_activation_gate(api_client, storage, monkeypatch):
    storage.replace_option_sentiment_snapshot(DAY, sample(), STAMP, 'public_cdn')
    monkeypatch.setattr(service, 'fetch_public_client_type_for_date_many', lambda *args, **kwargs: [])
    for query in ('', 'date=bad', f'date={DAY}&expiry=bad'):
        assert api_client.get('/api/option-sentiment/flow?' + query).status_code == 400
    response = api_client.get(f'/api/option-sentiment/flow?date={DAY}&underlying_key=2001')
    assert response.status_code == 200
    assert response.json()['audiences']['natural']['value']['strikes'][0]['call_flow'] is None
    storage.set_activated(False)
    assert api_client.get(f'/api/option-sentiment/flow?date={DAY}').status_code == 423


def test_live_flow_accepts_only_volume_matched_current_records_and_does_not_invent_cash(storage, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 21, 12, tzinfo=tz)
    monkeypatch.setattr(service, 'datetime', Clock)
    monkeypatch.setattr(service, 'fetch_market_date', lambda: DAY)
    rows = [contract(trade_volume=30), contract('2', strike_price=1200, trade_volume=30)]
    storage.replace_option_sentiment_snapshot(DAY, rows, STAMP, 'public_cdn')
    def current(codes, **kwargs):
        assert codes == [2, 12345678901234567]
        assert kwargs['timeout'] == 8
        return [
            {'ins_code': 12345678901234567, 'natural_buy_volume': 20, 'natural_sell_volume': 10,
             'legal_buy_volume': 10, 'legal_sell_volume': 20},
            {'ins_code': 2, 'natural_buy_volume': 99, 'natural_sell_volume': 10,
             'legal_buy_volume': 10, 'legal_sell_volume': 99},
        ]
    monkeypatch.setattr(service, 'fetch_public_client_type_current_many', current)
    dated_cash = MagicMock(return_value=[])
    monkeypatch.setattr(service, 'fetch_public_client_type_for_date_many', dated_cash)
    result = service.get_flow_payload(storage, '2001', 'all', DAY)['audiences']
    assert result['natural']['volume']['strikes'][0]['call_flow'] == 10
    assert result['legal']['volume']['strikes'][0]['call_flow'] == -10
    assert result['natural']['volume']['strikes'][1]['call_flow'] is None
    assert result['natural']['value']['strikes'][0]['call_flow'] is None
    dated_cash.assert_called_once_with([2, 12345678901234567], '20261021', timeout=8)


def test_session_rollover_discards_undated_current_participant_data(storage, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 21, 12, tzinfo=tz)
    monkeypatch.setattr(service, 'datetime', Clock)
    dates = iter([DAY, '2026-10-22'])
    monkeypatch.setattr(service, 'fetch_market_date', lambda: next(dates))
    storage.replace_option_sentiment_snapshot(DAY, [contract(trade_volume=30)], STAMP, 'public_cdn')
    monkeypatch.setattr(service, 'fetch_public_client_type_current_many', lambda *a, **k: [
        {'ins_code': 12345678901234567, 'natural_buy_volume': 20, 'natural_sell_volume': 10,
         'legal_buy_volume': 10, 'legal_sell_volume': 20}])
    result = service.get_flow_payload(storage, '2001', 'all', DAY)
    assert result['audiences']['natural']['volume']['strikes'][0]['call_flow'] is None


def test_flow_fetch_runs_off_event_loop(api_client, monkeypatch):
    entered, release = Event(), Event()
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return {'audiences': {}}
    monkeypatch.setattr(main, 'get_flow_payload', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(api_client.get, f'/api/option-sentiment/flow?date={DAY}')
        assert entered.wait(2)
        try:
            quick = pool.submit(api_client.get, '/api/activation/status')
            assert quick.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=2).status_code == 200


def test_live_quotes_replace_stale_chain_turnover_and_spot_without_copying_zero_placeholders(monkeypatch):
    feed = [{'insCode_C': '1', 'insCode_P': '2', 'uaInsCode': '2001', 'endDate': 20261209,
             'strikePrice': 1000, 'oP_C': 50, 'oP_P': 20, 'qTotCap_C': 99999, 'qTotTran5J_C': 999,
             'qTotCap_P': 8888, 'qTotTran5J_P': 88, 'pDrCotVal_UA': 9000}]
    monkeypatch.setattr(service, 'fetch_market_date', lambda: DAY)
    monkeypatch.setattr(service, 'fetch_public_option_market_watch', lambda: feed)
    monkeypatch.setattr(service, 'fetch_public_instrument_info_many', lambda codes: {})
    monkeypatch.setattr(service, 'fetch_public_live_quotes', lambda codes, day: {
        1: {'trade_value': 1234, 'trade_volume': 12},
        2001: {'last_price': 1100, 'closing_price': 1110},
    })
    result = service.fetch_current_snapshot()
    first, missing = result['contracts']
    assert first['trade_value'] == 1234 and first['trade_volume'] == 12
    assert first['underlying_last_price'] == 1100 and first['underlying_closing_price'] == 1110
    assert first['buy_open_positions'] == 50
    assert first['trade_date'] == DAY
    assert missing['trade_value'] is None and missing['trade_volume'] is None
    assert missing['trade_date'] is None


@pytest.mark.parametrize('wrong_day,wrong_volume', [(False, False), (True, False), (False, True)])
def test_bulk_live_quote_validation_uses_compact_fields_and_rejects_wrong_sessions(monkeypatch, wrong_day, wrong_volume):
    from options.backend.services import public_options
    session = MagicMock()
    session.__enter__.return_value = session
    def get(url, **kwargs):
        response = MagicMock()
        if url == public_options.LIVE_MARKET_WATCH_URL:
            assert kwargs['params']['paperTypes[8]'] == 9
            response.json.return_value = {'marketwatch': [
                {'insCode': '12345678901234567', 'qtj': 12, 'qtc': 1234, 'pdv': 10, 'pcl': 11,
                 'qTotTran5J': 0, 'qTotCap': 0, 'pDrCotVal': 0, 'pClosing': 0}]}
        else:
            assert url.endswith('/12345678901234567')
            response.json.return_value = {'closingPriceInfo': {
                'dEven': 20261020 if wrong_day else 20261021, 'qTotTran5J': 13 if wrong_volume else 12, 'qTotCap': 1234}}
        return response
    session.get.side_effect = get
    monkeypatch.setattr(public_options, '_session', lambda: session)
    if wrong_day or wrong_volume:
        with pytest.raises(ValueError):
            public_options.fetch_public_live_quotes([12345678901234567], DAY)
    else:
        assert public_options.fetch_public_live_quotes([12345678901234567], DAY) == {
            12345678901234567: {'trade_value': 1234, 'trade_volume': 12, 'last_price': 10, 'closing_price': 11}}


def test_legacy_undated_zero_activity_is_not_assumed_current_and_heatmap_waits_for_verified_data(storage, monkeypatch):
    storage.replace_option_sentiment_snapshot(DAY, [contract(trade_volume=0, trade_value=0)], STAMP, 'public_cdn')
    requested = MagicMock(return_value=[])
    monkeypatch.setattr(service, 'fetch_public_client_type_for_date_many', requested)
    result = service.get_flow_payload(storage, '2001', 'all', DAY)
    assert result['audiences']['natural']['volume']['strikes'][0]['call_flow'] is None
    requested.assert_called_once()
    assert service.get_payload(storage, '2001', 'all', DAY)['activity']['date_verified'] is False
    storage.replace_option_sentiment_snapshot(DAY, [contract(trade_volume=0, trade_value=0, trade_date=DAY)], STAMP, 'public_cdn')
    assert service.get_payload(storage, '2001', 'all', DAY)['activity']['date_verified'] is True
    result = service.get_flow_payload(storage, '2001', 'all', DAY)
    assert result['audiences']['natural']['volume']['strikes'][0]['call_flow'] == 0


@pytest.mark.parametrize('total,size,scale', [(100, 1000, 1), (100_000, 1000, 1000), (999, 1000, None), (100_000, None, None)])
def test_participant_cash_is_scaled_only_when_both_sides_match_premium_turnover(total, size, scale):
    participant = {'natural_buy_value': 80, 'natural_sell_value': 30,
                   'legal_buy_value': 20, 'legal_sell_value': 70, 'natural_buy_volume': 8}
    result = service._align_participant_cash(participant, contract(trade_value=total, contract_size=size))
    assert result['natural_buy_value'] == (80 * scale if scale else None)
    assert result['natural_sell_value'] == (30 * scale if scale else None)
    assert result['natural_buy_volume'] == 8
    if scale:
        assert result['natural_buy_value'] + result['legal_buy_value'] == total
        assert result['natural_sell_value'] + result['legal_sell_value'] == total


def test_participant_cash_does_not_combine_partial_side_values_or_allow_overflow():
    result = service._align_participant_cash({'natural_buy_value': 100, 'natural_sell_value': 20}, contract())
    assert result['natural_buy_value'] is None and result['natural_sell_value'] is None
    result = service._align_participant_cash({f'{g}_{s}_value': 1e308 for g in ('natural', 'legal') for s in ('buy', 'sell')}, contract(trade_value=1e308))
    json.dumps(result, allow_nan=False)
    assert result['natural_buy_value'] is None


def test_real_public_option_history_cash_reconciles_size_and_signed_volume():
    fixture = json.loads((Path(__file__).parent / 'fixtures/option_sentiment_cash_real_20261024.json').read_text())
    row = contract(fixture['ins_code'], trade_volume=fixture['trade_volume'], trade_value=fixture['trade_value'], contract_size=fixture['contract_size'])
    participant = service._align_participant_cash({'ins_code': fixture['ins_code'], **fixture['participant']}, row)
    result = analyze_participant_flow([row], [participant])['audiences']
    assert result['natural']['value']['strikes'][0]['call_flow'] == fixture['expected_natural_net_rial']
    assert result['legal']['value']['strikes'][0]['call_flow'] == -fixture['expected_natural_net_rial']
    assert result['natural']['volume']['strikes'][0]['call_flow'] == fixture['expected_natural_net_contracts']
    assert result['legal']['volume']['strikes'][0]['call_flow'] == -fixture['expected_natural_net_contracts']


@pytest.mark.parametrize('record_source', ['local', 'remote'])
@pytest.mark.parametrize('market_volume,expected', [(0, 0), (30, 10), (100, None)])
def test_participant_volume_reconciliation_rejects_stale_same_day_counts(storage, monkeypatch, record_source, market_volume, expected):
    row = contract(trade_volume=market_volume, trade_value=0, trade_date=DAY)
    participant = {'ins_code': int(row['ins_code']), 'rec_date': 20261021,
                   'natural_buy_volume': 20, 'natural_sell_volume': 10,
                   'legal_buy_volume': 10, 'legal_sell_volume': 20}
    storage.replace_option_sentiment_snapshot(DAY, [row], STAMP, 'public_cdn')
    if record_source == 'local':
        storage.insert_client_type_stats([participant])
    monkeypatch.setattr(service, 'fetch_public_client_type_for_date_many',
                        lambda *args, **kwargs: [participant] if record_source == 'remote' else [])
    result = service.get_flow_payload(storage, '2001', 'all', DAY)['audiences']
    assert result['natural']['volume']['strikes'][0]['call_flow'] == expected
    assert result['legal']['volume']['strikes'][0]['call_flow'] == (-expected if expected is not None else None)


def test_legacy_history_refresh_repairs_activity_and_preserves_archived_oi(api_client, storage, monkeypatch):
    storage.replace_option_sentiment_snapshot(DAY, [contract(buy_open_positions=70, trade_value=999)], STAMP, 'public_cdn')
    monkeypatch.setattr(service, 'fetch_public_option_history_contracts', lambda *args: [
        contract(buy_open_positions=None, sell_open_positions=None, yesterday_open_positions=None,
                 trade_value=222, trade_volume=12, underlying_last_price=1200)])
    assert api_client.get(f'/api/option-sentiment?date={DAY}').json()['activity']['date_verified'] is False
    response = api_client.post('/api/option-sentiment/refresh', json={'date': DAY})
    assert response.status_code == 202
    snapshot = storage.get_option_sentiment_snapshot(DAY)
    assert snapshot['source'] == 'public_cdn'
    assert snapshot['contracts'][0]['buy_open_positions'] == 70
    assert snapshot['contracts'][0]['yesterday_open_positions'] == 40
    assert snapshot['contracts'][0]['trade_value'] == 222
    assert snapshot['contracts'][0]['underlying_last_price'] == 1200
    assert snapshot['contracts'][0]['trade_date'] == DAY
    assert api_client.get(f'/api/option-sentiment?date={DAY}').json()['activity']['date_verified'] is True
    assert api_client.post('/api/option-sentiment/refresh', json={'date': DAY}).json()['status'] == 'cached'


def test_flow_response_keeps_the_snapshot_version_captured_before_remote_fetch(storage, monkeypatch):
    row = contract(trade_volume=30, trade_value=0, trade_date=DAY)
    storage.replace_option_sentiment_snapshot(DAY, [row], STAMP, 'public_cdn')
    newer_stamp = '2026-10-22T08:05:00+00:00'
    def remote(*args, **kwargs):
        storage.replace_option_sentiment_snapshot(DAY, [contract(trade_volume=100)], newer_stamp, 'public_cdn')
        return [{'ins_code': int(row['ins_code']), 'rec_date': 20261021,
                 'natural_buy_volume': 20, 'natural_sell_volume': 10,
                 'legal_buy_volume': 10, 'legal_sell_volume': 20}]
    monkeypatch.setattr(service, 'fetch_public_client_type_for_date_many', remote)
    result = service.get_flow_payload(storage, '2001', 'all', DAY)
    assert result['received_at'] == STAMP
    assert result['audiences']['natural']['volume']['strikes'][0]['call_flow'] == 10
    assert storage.get_option_sentiment_snapshot(DAY)['received_at'] == newer_stamp


def test_legacy_history_repair_retains_positions_for_contracts_missing_from_history(storage, monkeypatch):
    storage.replace_option_sentiment_snapshot(DAY, [contract(buy_open_positions=70), contract('2', buy_open_positions=20)],
                                              STAMP, 'public_cdn', 'پوشش بازار ناقص است.', market_complete=False)
    monkeypatch.setattr(service, 'fetch_public_option_history_contracts', lambda *args: [
        contract(buy_open_positions=None, sell_open_positions=None, yesterday_open_positions=None, trade_value=222)])
    result = service.fetch_historical_snapshot(storage, DAY)
    rows = {row['ins_code']: row for row in result['contracts']}
    assert rows['12345678901234567']['buy_open_positions'] == 70
    assert rows['2']['buy_open_positions'] == 20
    assert rows['2']['trade_value'] is None and rows['2']['trade_volume'] is None
    assert rows['2']['underlying_last_price'] is None and rows['2']['trade_date'] is None
    assert result['market_complete'] is False
    assert result['warning'] == 'پوشش بازار ناقص است.'


def test_failed_legacy_history_repair_preserves_the_previous_snapshot(storage, monkeypatch):
    storage.replace_option_sentiment_snapshot(DAY, [contract(buy_open_positions=70, trade_value=999)], STAMP, 'public_cdn')
    original = storage.get_option_sentiment_snapshot(DAY)
    monkeypatch.setattr(service, 'fetch_public_option_history_contracts', lambda *args: [])
    job = service.OptionSentimentRefresh()
    assert job.begin(DAY)['status'] == 'started'
    job.run(storage, DAY)
    assert job.status()['last_error']
    assert storage.get_option_sentiment_snapshot(DAY) == original


def test_inferred_non_trading_zeros_do_not_count_as_observed_cash_for_active_contracts():
    rows = [contract(str(i + 1), strike_price=1000 + i, trade_volume=0 if i < 29 else 30,
                     trade_value=0 if i < 29 else 100_000_000) for i in range(72)]
    zero_codes = {str(i + 1) for i in range(29)}
    value = analyze_participant_flow(rows, [], known_zero_codes=zero_codes)['audiences']['natural']['value']
    assert value['coverage']['known'] == 29
    assert value['coverage']['total'] == 72
    assert value['traded_coverage']['known'] == 0
    assert value['traded_coverage']['total'] == 43
    assert not value['traded_coverage']['complete']


@pytest.mark.parametrize('cash_source', ['local', 'same_day_history', 'previous_day_history'])
def test_volume_only_live_data_does_not_hide_valid_same_day_cash(storage, monkeypatch, cash_source):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 21, 12, tzinfo=tz)
    monkeypatch.setattr(service, 'datetime', Clock)
    monkeypatch.setattr(service, 'fetch_market_date', lambda: DAY)
    row = contract(trade_volume=30, trade_date=DAY)
    code = int(row['ins_code'])
    storage.replace_option_sentiment_snapshot(DAY, [row], STAMP, 'public_cdn')
    live = {'ins_code': code, 'natural_buy_volume': 20, 'natural_sell_volume': 10,
            'legal_buy_volume': 10, 'legal_sell_volume': 20}
    cash = {**live, 'rec_date': 20261021, 'natural_buy_value': 80_000_000, 'natural_sell_value': 30_000_000,
            'legal_buy_value': 20_000_000, 'legal_sell_value': 70_000_000}
    if cash_source == 'local':
        storage.insert_client_type_stats([cash])
    history = MagicMock(return_value=[cash] if cash_source == 'same_day_history' else
                        [{**cash, 'rec_date': 20261020}] if cash_source == 'previous_day_history' else [])
    monkeypatch.setattr(service, 'fetch_public_client_type_current_many', lambda *a, **k: [live])
    monkeypatch.setattr(service, 'fetch_public_client_type_for_date_many', history)
    result = service.get_flow_payload(storage, '2001', 'all', DAY)['audiences']['natural']
    assert result['value']['strikes'][0]['call_flow'] == (None if cash_source == 'previous_day_history' else 50_000_000)
    assert result['volume']['strikes'][0]['call_flow'] == 10
    if cash_source == 'local':
        history.assert_not_called()
    else:
        history.assert_called_once_with([code], '20261021', timeout=8)


def test_participant_merge_keeps_each_measure_together_and_retains_valid_cash():
    row = contract(trade_volume=30)
    previous = {'natural_buy_value': 80_000_000, 'natural_sell_value': 30_000_000,
                'legal_buy_value': 20_000_000, 'legal_sell_value': 70_000_000}
    incoming = {'natural_buy_volume': 20, 'natural_sell_volume': 10, 'legal_buy_volume': 10, 'legal_sell_volume': 20,
                'natural_buy_value': 90_000_000, 'legal_buy_value': 10_000_000}
    result = service._merge_participant_data(previous, incoming, row)
    assert result['natural_buy_value'] == 80_000_000
    assert result['natural_sell_value'] == 30_000_000
    assert result['legal_buy_value'] == 20_000_000
    assert result['legal_sell_value'] == 70_000_000
    assert result['natural_buy_volume'] == 20
    assert result['natural_sell_volume'] == 10


def test_zero_net_cash_on_traded_contracts_is_observed_data_not_unavailable():
    row = contract(trade_volume=30)
    participant = {'ins_code': row['ins_code'], 'natural_buy_value': 80_000_000, 'natural_sell_value': 80_000_000,
                   'legal_buy_value': 20_000_000, 'legal_sell_value': 20_000_000}
    value = analyze_participant_flow([row], [participant])['audiences']['natural']['value']
    assert value['traded_coverage']['known'] == value['traded_coverage']['total'] == 1
    assert value['traded_coverage']['complete']
    assert value['strikes'][0]['call_flow'] == 0
