"""Calendar availability and honest local fallbacks when Time.ir is unavailable."""

from datetime import date
from io import BytesIO
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

import pytest

from options.backend import calendar_data


@pytest.fixture(autouse=True)
def reset_calendar_state(monkeypatch):
    monkeypatch.setattr(calendar_data, "_calendar_cache", {})
    monkeypatch.setattr(calendar_data, "_calendar_retry_at", 0.0)


def test_unauthorized_calendar_response_is_closed_and_not_retried_immediately(monkeypatch):
    body = BytesIO(b"Unauthorized")
    error = HTTPError("https://api.time.ir/calendar", 401, "Unauthorized", {}, body)
    fetch = MagicMock(side_effect=error)
    monkeypatch.setattr("urllib.request.urlopen", fetch)
    today = calendar_data.fetch_calendar_today(date(2026, 10, 21))
    month = calendar_data.format_calendar_events(calendar_data.fetch_calendar_month(1405, 8), 1405, 8)
    assert today["gregorian_date"] == "2026-10-21"
    assert today["jalali_date"] == "1405-07-29"
    assert today["source"] == "fallback"
    assert today["events_available"] is False
    assert today["is_holiday"] is None
    assert month["events_available"] is False
    assert len(month["days"]) == 30
    assert body.closed
    fetch.assert_called_once()


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError(), ValueError("invalid JSON")])
def test_calendar_network_and_payload_failures_keep_date_selection_available(monkeypatch, failure):
    fetch = MagicMock(side_effect=failure)
    monkeypatch.setattr("urllib.request.urlopen", fetch)
    payload = calendar_data.fetch_calendar_month(1405, 7)
    month = calendar_data.format_calendar_events(payload, 1405, 7)
    assert month["source"] == "fallback"
    assert month["events_available"] is False
    assert len(month["days"]) == 30
    assert next(day for day in month["days"] if day["day"] == 24)["is_weekend"] is True


def test_calendar_retries_after_cooldown_and_reuses_a_successful_response(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(calendar_data.time, "monotonic", lambda: now[0])
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"data":{"day_list":[],"event_list":[]}}'
    fetch = MagicMock(side_effect=[URLError("offline"), response])
    monkeypatch.setattr("urllib.request.urlopen", fetch)
    assert calendar_data.fetch_calendar_month(1405, 7)["source"] == "fallback"
    now[0] += calendar_data.CALENDAR_RETRY_SECONDS + 1
    first = calendar_data.fetch_calendar_month(1405, 7)
    assert first.get("source", "time.ir") == "time.ir"
    assert calendar_data.fetch_calendar_month(1405, 7) == first
    assert fetch.call_count == 2


def test_today_checks_the_current_month_first_and_preserves_holiday_data(monkeypatch):
    fetch = MagicMock(return_value={"data": {
        "day_list": [{"index_in_base1": 29, "is_today": True, "is_holiday": True}],
        "event_list": [],
    }})
    monkeypatch.setattr(calendar_data, "fetch_calendar_month", fetch)
    today = calendar_data.fetch_calendar_today(date(2026, 10, 21))
    assert today["gregorian_date"] == "2026-10-21"
    assert today["is_holiday"] is True
    assert today["events_available"] is True
    fetch.assert_called_once_with(1405, 7)


def test_local_friday_is_known_even_when_calendar_service_is_down(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", MagicMock(side_effect=URLError("offline")))
    today = calendar_data.fetch_calendar_today(date(2026, 10, 23))
    assert today["is_holiday"] is True
    assert today["is_weekend"] is True
    assert today["events_available"] is False


def test_calendar_api_returns_usable_dates_when_upstream_rejects_access(monkeypatch):
    from starlette.testclient import TestClient
    from options.backend.api import main

    fetch = MagicMock(side_effect=HTTPError("https://api.time.ir/calendar", 401, "Unauthorized", {}, BytesIO()))
    monkeypatch.setattr("urllib.request.urlopen", fetch)
    monkeypatch.setattr(main.storage, "is_activated", lambda: True)
    headers = {main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN}
    with TestClient(main.app) as client:
        today = client.get("/api/calendar/today", headers=headers)
        month = client.get("/api/calendar/1405/7/events", headers=headers)
        day = client.get("/api/calendar/1405/7/29/info", headers=headers)
    assert today.status_code == month.status_code == day.status_code == 200
    assert today.json()["events_available"] is False
    assert len(month.json()["days"]) == 30
    assert day.json()["jalali_date"] == "1405-07-29"
    assert day.json()["events_available"] is False
    fetch.assert_called_once()
