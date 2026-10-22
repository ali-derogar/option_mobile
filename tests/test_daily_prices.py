"""Validate the source and date of actual intraday traded-price ranges."""

from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

from options.backend.services import daily_prices

TODAY = date(2026, 10, 21)


def quote(**changes):
    return {"insCode": "1001", "dEven": 20261021, "priceMin": 80, "priceMax": 160,
            "qTotTran5J": 30, **changes}


def test_daily_range_accepts_actual_trades_including_a_single_traded_price():
    assert daily_prices.normalize_daily_price_range(quote(priceMin=100, priceMax=100), TODAY) == {
        "price_min": 100, "price_max": 100, "price_range_date": 20261021,
    }


@pytest.mark.parametrize("changes", [
    {"dEven": 20261020}, {"dEven": None}, {"qTotTran5J": 0}, {"qTotTran5J": True},
    {"priceMin": None}, {"priceMin": True}, {"priceMin": -1}, {"priceMin": "bad"},
    {"priceMin": float("nan")}, {"priceMax": float("inf")}, {"priceMin": 170},
])
def test_old_untraded_and_invalid_ranges_are_unavailable(changes):
    assert daily_prices.normalize_daily_price_range(quote(**changes), TODAY) is None


@pytest.mark.parametrize("payload, available", [
    ({"closingPriceInfo": quote()}, True),
    ({"closingPriceInfo": quote(insCode="0")}, True),
    ({"closingPriceInfo": quote(insCode=None)}, True),
    ({"closingPriceInfo": quote(insCode="2002")}, False),
    ({"closingPriceInfo": quote(dEven=20261020)}, False),
    ({"closingPriceInfo": None}, False), ([], False),
])
def test_fetch_uses_the_official_envelope_and_rejects_explicit_instrument_mismatches(monkeypatch, payload, available):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value.json.return_value = payload
    monkeypatch.setattr(daily_prices.requests, "Session", lambda: session)
    row = daily_prices.fetch_public_daily_price_range(1001, TODAY)
    assert (row is not None) is available
    session.get.assert_called_once_with(daily_prices.CLOSING_PRICE_INFO_URL.format(ins_code=1001),
                                        timeout=min(daily_prices.TSETMC_REQUEST_TIMEOUT, 10))
    session.get.return_value.raise_for_status.assert_called_once()


def test_batch_deduplicates_and_isolates_unavailable_contracts(monkeypatch):
    def fetch(code, today):
        assert today == TODAY
        if code == 2:
            raise requests.Timeout()
        return daily_prices.normalize_daily_price_range(quote(), today) if code == 1 else None

    fetch_mock = MagicMock(side_effect=fetch)
    monkeypatch.setattr(daily_prices, "fetch_public_daily_price_range", fetch_mock)
    result = daily_prices.fetch_public_daily_price_ranges([1, 1, 2, 3], TODAY)
    assert set(result) == {1}
    assert fetch_mock.call_count == 3
    assert daily_prices.fetch_public_daily_price_ranges([], TODAY) == {}
