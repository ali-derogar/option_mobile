"""Security-focused API behavior tests."""

from __future__ import annotations

import time

import pandas as pd
from starlette.testclient import TestClient
from starlette.datastructures import QueryParams

from options.backend.activation import encode
from options.backend.api import main


class FakeRequest:
    def __init__(self, query_string: str):
        self.query_params = QueryParams(query_string)


def test_dashboard_root_serves_index_with_local_token_cookie() -> None:
    with TestClient(main.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "options_api_token=" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_query_value_strips_whitespace_and_treats_blank_as_missing() -> None:
    assert main._query_value(FakeRequest("q=%20%20"), "q") is None
    assert main._query_value(FakeRequest("q=%20%D8%AE%D9%88%D8%AF%D8%B1%D9%88%20"), "q") == "خودرو"
    assert main._query_value(FakeRequest("date=%DB%B2%DB%B0%DB%B2%DB%B5-%DB%B0%DB%B6-%DB%B1%DB%B4"), "date") == "2025-06-14"


def test_api_data_endpoints_require_activation(monkeypatch) -> None:
    monkeypatch.setattr(main.storage, "is_activated", lambda: False)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/summary",
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )

    assert response.status_code == 423
    assert response.json()["detail"] == "activation_required"


def test_activation_endpoint_accepts_current_code(monkeypatch) -> None:
    activated = {"value": False}
    monkeypatch.setattr(main.storage, "is_activated", lambda: activated["value"])
    monkeypatch.setattr(main.storage, "set_activated", lambda value=True: activated.update(value=value))

    with TestClient(main.app) as client:
        status = client.get(
            "/api/activation/status",
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )
        response = client.post(
            "/api/activation",
            json={"code": encode(int(time.time()))},
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )

    assert status.status_code == 200
    assert status.json() == {"activated": False}
    assert response.status_code == 200
    assert response.json() == {"activated": True}
    assert activated["value"] is True


def test_activation_endpoint_rejects_invalid_code(monkeypatch) -> None:
    monkeypatch.setattr(main.storage, "is_activated", lambda: False)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/activation",
            json={"code": "bad"},
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "activation code was not accepted"


def test_underlying_trend_validates_date_before_unknown_underlying(monkeypatch) -> None:
    monkeypatch.setattr(main.storage, "is_activated", lambda: True)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/underlyings/unknown/trend?date=bad-date",
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid date; expected YYYY-MM-DD"


def test_open_interest_rejects_non_positive_instrument_code(monkeypatch) -> None:
    monkeypatch.setattr(main.storage, "is_activated", lambda: True)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/open-interest/-1",
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid ins_code"


def test_open_interest_accepts_grouped_persian_instrument_code(monkeypatch) -> None:
    called = {}
    monkeypatch.setattr(main.storage, "is_activated", lambda: True)

    def fake_history(ins_code, through_date=None):
        called["ins_code"] = ins_code
        return pd.DataFrame()

    monkeypatch.setattr(main.storage, "get_open_interest_history_df", fake_history)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/open-interest/۱٬۰۰۱",
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )

    assert response.status_code == 200
    assert response.json()["ins_code"] == "1001"
    assert called["ins_code"] == 1001


def test_calendar_today_returns_api_marked_date(monkeypatch) -> None:
    monkeypatch.setattr(main.storage, "is_activated", lambda: True)
    monkeypatch.setattr(
        main.calendar_data,
        "fetch_calendar_today",
        lambda today: {
            "jalali_date": "1405-06-02",
            "gregorian_date": "2026-08-24",
            "source": "time.ir",
        },
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/calendar/today",
            headers={main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN},
        )

    assert response.status_code == 200
    assert response.json()["gregorian_date"] == "2026-08-24"


def test_refresh_status_does_not_expose_export_paths() -> None:
    original_status = dict(main._refresh_status)
    try:
        main._refresh_status.update(
            {
                "running": False,
                "last_error": None,
                "last_result": {
                    "options": 12,
                    "client_type_stats": 8,
                    "money_flow": 8,
                    "open_interest": 12,
                    "exports": {"contracts": "/tmp/private/contracts.csv"},
                },
                "stage": "done",
                "message": "ok",
                "started_at": "2026-08-23T00:00:00+00:00",
                "finished_at": "2026-08-23T00:00:01+00:00",
            }
        )

        status = main._public_refresh_status()

        assert status["last_result"] == {
            "options": 12,
            "client_type_stats": 8,
            "money_flow": 8,
            "open_interest": 12,
        }
    finally:
        main._refresh_status.clear()
        main._refresh_status.update(original_status)


def test_refresh_status_maps_legacy_client_type_count() -> None:
    original_status = dict(main._refresh_status)
    try:
        main._refresh_status.update(
            {
                "running": False,
                "last_error": None,
                "last_result": {
                    "options": 12,
                    "client_type": 8,
                    "exports": {"contracts": "/tmp/private/contracts.csv"},
                },
                "stage": "done",
                "message": "ok",
                "started_at": "2026-08-23T00:00:00+00:00",
                "finished_at": "2026-08-23T00:00:01+00:00",
            }
        )

        status = main._public_refresh_status()

        assert status["last_result"] == {
            "options": 12,
            "client_type_stats": 8,
        }
    finally:
        main._refresh_status.clear()
        main._refresh_status.update(original_status)


def test_begin_refresh_distinguishes_running_from_cooldown(monkeypatch) -> None:
    original_status = dict(main._refresh_status)
    original_last_request = main._last_refresh_request_at
    try:
        monkeypatch.setattr(main.time, "monotonic", lambda: 1000.0)
        main._refresh_status.update({"running": False})
        main._last_refresh_request_at = 0.0

        assert main._begin_refresh() == "started"

        main._refresh_status["running"] = True
        assert main._begin_refresh() == "already_running"

        main._refresh_status["running"] = False
        assert main._begin_refresh() == "cooldown"
    finally:
        main._refresh_status.clear()
        main._refresh_status.update(original_status)
        main._last_refresh_request_at = original_last_request
