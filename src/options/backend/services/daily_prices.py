"""Current trading-day ranges from the public TSETMC closing-price feed."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from math import isfinite
from typing import Any

import requests

from options.backend.config import (
    TSETMC_PUBLIC_CLIENT_TYPE_WORKERS,
    TSETMC_REQUEST_TIMEOUT,
    TSETMC_TRUST_ENV_PROXY,
)

logger = logging.getLogger(__name__)
CLOSING_PRICE_INFO_URL = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceInfo/{ins_code}"


def normalize_daily_price_range(row: dict[str, Any], today: date) -> dict[str, Any] | None:
    """Accept actual trades from the requested day, never price limits or old trades."""
    expected_date = today.strftime("%Y%m%d")
    if str(row.get("dEven")) != expected_date:
        return None
    values = (row.get("priceMin"), row.get("priceMax"), row.get("qTotTran5J"))
    if any(isinstance(value, bool) for value in values):
        return None
    try:
        low, high, volume = map(float, values)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(isfinite(value) and value > 0 for value in (low, high, volume)) or low > high:
        return None
    return {"price_min": low, "price_max": high, "price_range_date": int(expected_date)}


def fetch_public_daily_price_range(ins_code: int, today: date) -> dict[str, Any] | None:
    with requests.Session() as session:
        session.trust_env = TSETMC_TRUST_ENV_PROXY
        response = session.get(
            CLOSING_PRICE_INFO_URL.format(ins_code=ins_code),
            timeout=min(TSETMC_REQUEST_TIMEOUT, 10),
        )
        response.raise_for_status()
        payload = response.json()
    row = payload.get("closingPriceInfo") if isinstance(payload, dict) else None
    if not isinstance(row, dict):
        return None
    # This endpoint commonly returns insCode="0"; the requested URL identifies
    # the instrument. Reject an explicit conflicting code when one is supplied.
    returned_code = row.get("insCode")
    if returned_code not in (None, 0, "0", "") and str(returned_code) != str(ins_code):
        return None
    return normalize_daily_price_range(row, today)


def fetch_public_daily_price_ranges(ins_codes: list[int], today: date) -> dict[int, dict[str, Any]]:
    """Fetch only the relevant calls; unavailable ranges never replace the ask."""
    codes = sorted(set(ins_codes))
    if not codes:
        return {}
    result = {}
    workers = max(1, min(TSETMC_PUBLIC_CLIENT_TYPE_WORKERS, len(codes)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_public_daily_price_range, code, today): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                row = future.result()
            except (requests.RequestException, ValueError):
                logger.warning("Daily price range unavailable for %s", code, exc_info=True)
                continue
            if row is not None:
                result[code] = row
    return result
