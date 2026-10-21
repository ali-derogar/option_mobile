"""Slow data analysis must not prevent the local server from serving requests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading

import pytest
from starlette.testclient import TestClient

from options.backend.api import main
from options.backend.storage import MARKET_TZ


@pytest.mark.parametrize("path, helper", [
    ("/api/summary", "get_summary"),
    ("/api/underlyings", "get_watchlist_underlyings"),
    ("/api/underlyings/2001/contracts", "get_underlying_contracts"),
    ("/api/sentiment", "get_sentiment"),
    ("/api/underlyings/2001/trend", "get_underlying_trend"),
])
def test_slow_data_requests_do_not_block_other_requests(monkeypatch, path, helper):
    entered = threading.Event()
    release = threading.Event()

    def slow_analysis(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("analysis was not released")
        return {"items": [], "total": 0}

    monkeypatch.setattr(main.storage, "is_activated", lambda: True)
    monkeypatch.setattr(main.storage, "get_watchlist_codes", lambda: [])
    monkeypatch.setattr(main.storage, "get_latest_snapshot_date", lambda: datetime.now(MARKET_TZ).date().isoformat())
    monkeypatch.setattr(main, "_underlying_known_locally", lambda _: True)
    monkeypatch.setattr(main, "_collect_trend_dates", lambda *args: ([], {}, {}))
    monkeypatch.setattr(main, helper, slow_analysis)
    headers = {main.LOCAL_API_HEADER: main.LOCAL_API_TOKEN}
    with TestClient(main.app) as client, ThreadPoolExecutor(max_workers=2) as requests:
        slow = requests.submit(client.get, path, headers=headers)
        try:
            assert entered.wait(2), "the data request did not start"
            health = requests.submit(client.get, "/api/health", headers=headers)
            assert health.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert slow.result(timeout=2).status_code == 200
