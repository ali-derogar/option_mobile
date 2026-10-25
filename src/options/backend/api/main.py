"""Starlette application for TSETMC options web UI."""

from __future__ import annotations

import logging
import json
import os
import re
import secrets
import threading
import time
from math import isfinite
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.background import BackgroundTasks
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from options.backend.activation import is_valid as is_activation_code_valid
from options.backend import calendar_data
from options.backend.api.data import (
    _df_to_records,
    _text_mask,
    get_available_dates,
    get_merged_contracts,
    get_summary,
    get_sentiment,
    get_underlying_contracts,
    get_underlying_trend,
    get_watchlist_underlyings,
)
from options.backend.pipeline import run_pipeline
from options.backend.services.expiries import ExpiryRefresh
from options.backend.services.expiry_selection import ExpiryFilters
from options.backend.services.historical_options import import_public_option_history
from options.backend.services.option_sentiment import OptionSentimentRefresh, activity_date_verified, get_flow_payload, get_payload as get_option_sentiment_payload
from options.backend.services.public_options import (
    fetch_public_client_type_current,
    fetch_public_client_type_current_many,
)
from options.backend.storage import MARKET_TZ, Storage
from options.backend.services.watchlist import CatalogRefresh, save_watchlist

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
STATIC_DIR = WEB_ROOT / "static"

LOCAL_API_HEADER = "x-options-api-token"
LOCAL_API_COOKIE = "options_api_token"
LOCAL_API_TOKEN = secrets.token_urlsafe(32)
DASHBOARD_SESSION_ID = os.getenv("OPTIONS_DASHBOARD_SESSION") or secrets.token_urlsafe(24)
MAX_HISTORICAL_LOOKBACK_DAYS = 370
MAX_API_JSON_BODY_BYTES = 32 * 1024
MAX_ACTIVATION_JSON_BODY_BYTES = 1024
MAX_CLIENT_TYPE_BATCH_SIZE = 300
REFRESH_COOLDOWN_SECONDS = 60
DASHBOARD_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "manifest-src 'self'; "
    "form-action 'none'"
)

storage = Storage()
catalog_refresh_job = CatalogRefresh()
expiry_refresh_job = ExpiryRefresh()
option_sentiment_refresh_job = OptionSentimentRefresh()

_refresh_lock = threading.RLock()
_history_import_lock = threading.Lock()
_refresh_status: Dict[str, Any] = {
    "running": False,
    "last_result": None,
    "last_error": None,
    "stage": None,
    "message": None,
    "started_at": None,
    "finished_at": None,
}
_last_refresh_request_at = 0.0


def _origin_key(value: str) -> Optional[tuple[str, str, int]]:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    return (parts.scheme.lower(), parts.hostname.lower(), port)


def _request_origin_key(request: Request) -> Optional[tuple[str, str, int]]:
    request_url = urlsplit(str(request.url))
    host = request.headers.get("host") or request_url.netloc
    return _origin_key(f"{request_url.scheme}://{host}")


def _api_request_allowed_by_browser_headers(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin and _origin_key(origin) != _request_origin_key(request):
        return False
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    return not fetch_site or fetch_site == "same-origin"


def _has_local_api_token(request: Request) -> bool:
    for token in (request.headers.get(LOCAL_API_HEADER), request.cookies.get(LOCAL_API_COOKIE)):
        if isinstance(token, str) and secrets.compare_digest(token, LOCAL_API_TOKEN):
            return True
    return False


def _has_dashboard_session(request: Request) -> bool:
    session_id = request.path_params.get("dashboard_session")
    return isinstance(session_id, str) and secrets.compare_digest(session_id, DASHBOARD_SESSION_ID)


def _apply_common_security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


def _apply_dashboard_security_headers(response: Response) -> Response:
    _apply_common_security_headers(response)
    response.headers["Content-Security-Policy"] = DASHBOARD_CSP
    return response


class LocalApiTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/") and (
            not _api_request_allowed_by_browser_headers(request) or not _has_local_api_token(request)
        ):
            return _json({"detail": "forbidden"}, status_code=403)
        if (
            request.url.path.startswith("/api/")
            and request.url.path not in {"/api/health", "/api/activation/status", "/api/activation"}
            and not storage.is_activated()
        ):
            return _json({"detail": "activation_required"}, status_code=423)
        return await call_next(request)


class NoStoreStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return _apply_common_security_headers(response)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error for %s", request.url.path)
    return _json({"detail": "internal server error"}, status_code=500)


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return _json({"detail": exc.detail}, status_code=exc.status_code)


def _json(payload: Dict[str, Any], status_code: int = 200, background: Optional[BackgroundTasks] = None) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code, background=background)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _apply_common_security_headers(response)


def _query_value(request: Request, name: str) -> Optional[str]:
    value = request.query_params.get(name)
    if value is None:
        return None
    value = value.strip()
    if name == "date":
        value = _translate_digits(value)
    return value or None


def _query_int(
    request: Request,
    name: str,
    default: Optional[int] = None,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> Optional[int]:
    raw = _query_value(request, name)
    if raw is None:
        return default
    try:
        value = int(_clean_numeric_text(raw))
    except ValueError as exc:
        raise HTTPException(400, f"invalid {name}") from exc
    if min_value is not None and value < min_value:
        raise HTTPException(400, f"{name} is below minimum")
    if max_value is not None and value > max_value:
        raise HTTPException(400, f"{name} is above maximum")
    return value


async def _read_json_payload(request: Request, max_bytes: int, invalid_detail: str) -> Dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(413, "request body is too large")
        except ValueError as exc:
            raise HTTPException(400, "invalid content length") from exc
    body = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(413, "request body is too large")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(400, invalid_detail) from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, invalid_detail)
    return payload


def _path_ins_code(value: Any) -> int:
    text = _clean_numeric_text(_translate_digits(str(value or "")))
    if not re.fullmatch(r"[0-9]+", text) or len(text) > 19:
        raise HTTPException(400, "invalid instrument code")
    ins_code = int(text)
    if ins_code <= 0 or ins_code > 2**63 - 1:
        raise HTTPException(400, "invalid instrument code")
    return ins_code


def _clean_numeric_text(value: str) -> str:
    return (
        value.strip()
        .replace(",", "")
        .replace("٬", "")
        .replace("،", "")
        .replace(" ", "")
    )


def _translate_digits(value: str) -> str:
    return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_refresh_status(**payload: Any) -> None:
    with _refresh_lock:
        _refresh_status.update(payload)


def _public_refresh_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    public = {
        key: result.get(key)
        for key in ("options", "client_type_stats", "money_flow", "open_interest")
        if key in result
    }
    if "client_type_stats" not in public and "client_type" in result:
        public["client_type_stats"] = result.get("client_type")
    return public


def _public_refresh_status() -> Dict[str, Any]:
    with _refresh_lock:
        status = dict(_refresh_status)
    status["last_result"] = _public_refresh_result(status.get("last_result"))
    return status


def _refresh_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "credential" in text or "username" in text or "password" in text:
        return "دسترسی رسمی تنظیم نیست و دریافت عمومی هم کامل نشد."
    if "timeout" in text or "connection" in text or "network" in text:
        return "خطای ارتباط با سرور داده؛ اتصال اینترنت را بررسی کنید."
    return "خطا در به‌روزرسانی داده. کمی بعد دوباره تلاش کنید."


def _run_refresh(limit: Optional[int] = None, underlying_ins_codes: Optional[set[int]] = None) -> None:
    try:
        result = run_pipeline(
            limit=limit,
            skip_client_type=False,
            delay_between_calls=0.15,
            progress_callback=lambda payload: _update_refresh_status(**payload),
            underlying_ins_codes=underlying_ins_codes,
        )
        _update_refresh_status(
            last_result=result,
            stage="done",
            message=f"به‌روزرسانی کامل شد؛ {result.get('options', 0)} قرارداد",
        )
    except Exception as exc:
        logger.exception("Refresh failed")
        error_message = _refresh_error_message(exc)
        _update_refresh_status(
            last_error=error_message,
            stage="failed",
            message=error_message,
        )
    finally:
        _update_refresh_status(running=False, finished_at=_utc_now_iso())


def _parse_snapshot_date(value: Optional[str]) -> Optional[date_type]:
    if not value:
        return None
    value = _translate_digits(value.strip())
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HTTPException(400, "invalid date; expected YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(400, "invalid date; expected YYYY-MM-DD") from exc
    today = datetime.now(MARKET_TZ).date()
    if parsed > today:
        raise HTTPException(400, "date cannot be in the future")
    if today - parsed > timedelta(days=MAX_HISTORICAL_LOOKBACK_DAYS):
        raise HTTPException(400, "date is outside the supported history window")
    return parsed


def _ensure_snapshot_date(snapshot_date: Optional[str]) -> None:
    if not snapshot_date:
        return
    _parse_snapshot_date(snapshot_date)
    if storage.has_contract_snapshot_date(snapshot_date):
        return
    with _history_import_lock:
        if storage.has_contract_snapshot_date(snapshot_date):
            return
        try:
            contracts, client_type_rows = import_public_option_history(
                snapshot_date,
                metadata_by_ins_code=storage.get_contract_metadata_by_ins_code(),
            )
        except Exception as exc:
            logger.exception("Historical import failed for %s", snapshot_date)
            raise HTTPException(503, "historical import failed") from exc
        if not contracts:
            raise HTTPException(404, "historical data not found for date")
        storage.insert_contract_snapshot(contracts, snapshot_date=snapshot_date)
        if client_type_rows:
            storage.insert_client_type_stats(client_type_rows)
            storage.insert_money_flow(client_type_rows)
        logger.info(
            "Imported historical option snapshot for %s: %d contracts, %d client type rows",
            snapshot_date,
            len(contracts),
            len(client_type_rows),
        )


def _begin_refresh() -> str:
    global _last_refresh_request_at
    now = time.monotonic()
    with _refresh_lock:
        if _refresh_status["running"]:
            return "already_running"
        if now - _last_refresh_request_at < REFRESH_COOLDOWN_SECONDS:
            return "cooldown"
        _last_refresh_request_at = now
        _refresh_status["running"] = True
        _refresh_status["last_error"] = None
        _refresh_status["last_result"] = None
        _refresh_status["stage"] = "starting"
        _refresh_status["message"] = "شروع به‌روزرسانی"
        _refresh_status["started_at"] = _utc_now_iso()
        _refresh_status["finished_at"] = None
    return "started"


def _collect_trend_dates(
    underlying_key: str,
    end_date: str,
    days: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    try:
        cursor = _parse_snapshot_date(end_date)
        if cursor is None:
            raise HTTPException(400, "invalid date; expected YYYY-MM-DD")
    except HTTPException:
        raise

    available_dates: list[str] = []
    for snapshot_date in storage.get_available_snapshot_dates():
        try:
            parsed_snapshot = _parse_snapshot_date(snapshot_date)
        except HTTPException:
            continue
        if parsed_snapshot is not None and parsed_snapshot <= cursor:
            available_dates.append(snapshot_date)
    available_dates.sort(reverse=True)

    found: list[str] = []
    sources: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for current_date in available_dates:
        if len(found) >= days:
            break
        contracts = get_underlying_contracts(
            storage,
            underlying_key=underlying_key,
            snapshot_date=current_date,
        )
        if not contracts.get("items"):
            skipped[current_date] = "no contracts for underlying"
            continue
        found.append(current_date)
        sources[current_date] = "database"
    found.reverse()
    return found, sources, skipped


def _underlying_known_locally(underlying_key: str) -> bool:
    return storage.has_underlying_snapshot_key(underlying_key)


async def health(request: Request) -> JSONResponse:
    return _json({"status": "ok"})


async def activation_status(request: Request) -> JSONResponse:
    return _json({"activated": storage.is_activated()})


async def activate(request: Request) -> JSONResponse:
    payload = await _read_json_payload(
        request,
        max_bytes=MAX_ACTIVATION_JSON_BODY_BYTES,
        invalid_detail="invalid activation payload",
    )
    code = payload.get("code")
    if not isinstance(code, str) or not is_activation_code_valid(code.strip()):
        raise HTTPException(400, "activation code was not accepted")
    storage.set_activated(True)
    return _json({"activated": True})


# Starlette runs synchronous endpoints in its thread pool. Keep SQLite reads
# and pandas analysis off the event loop so status requests remain responsive.
def summary(request: Request) -> JSONResponse:
    date = _query_value(request, "date")
    _parse_snapshot_date(date)
    codes = set(storage.get_watchlist_codes())
    if codes:
        _ensure_snapshot_date(date)
    payload = get_summary(storage, snapshot_date=date, underlying_ins_codes=codes)
    payload["underlying_count"] = len(codes)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return _json(payload)


def dates(request: Request) -> JSONResponse:
    return _json(get_available_dates(storage))


async def calendar_events(request: Request) -> JSONResponse:
    year = int(request.path_params["year"])
    month = int(request.path_params["month"])
    if year < 1200 or year > 1600:
        raise HTTPException(400, "invalid calendar year")
    if month < 1 or month > 12:
        raise HTTPException(400, "invalid calendar month")
    try:
        raw_calendar = await run_in_threadpool(calendar_data.fetch_calendar_month, year, month)
        payload = calendar_data.format_calendar_events(raw_calendar, year, month)
    except Exception as exc:
        logger.exception("Calendar fetch failed for %s/%s", year, month)
        raise HTTPException(503, "calendar fetch failed") from exc
    return _json(payload)


async def calendar_today(request: Request) -> JSONResponse:
    try:
        today = datetime.now(MARKET_TZ).date()
        payload = await run_in_threadpool(calendar_data.fetch_calendar_today, today)
    except Exception as exc:
        logger.exception("Calendar today fetch failed")
        raise HTTPException(503, "calendar fetch failed") from exc
    return _json(payload)


async def calendar_day_info(request: Request) -> JSONResponse:
    year = int(request.path_params["year"])
    month = int(request.path_params["month"])
    day = int(request.path_params["day"])
    if year < 1200 or year > 1600:
        raise HTTPException(400, "invalid calendar year")
    if not calendar_data.valid_jalali_date(year, month, day):
        raise HTTPException(400, "invalid calendar date")
    try:
        raw_calendar = await run_in_threadpool(calendar_data.fetch_calendar_month, year, month, day)
        payload = calendar_data.day_info_from_calendar(raw_calendar, year, month, day)
    except Exception as exc:
        logger.exception("Calendar day fetch failed for %s/%s/%s", year, month, day)
        raise HTTPException(503, "calendar fetch failed") from exc
    return _json(payload)


def contracts(request: Request) -> JSONResponse:
    q = _query_value(request, "q")
    date = _query_value(request, "date")
    _parse_snapshot_date(date)
    codes = set(storage.get_watchlist_codes())
    if codes:
        _ensure_snapshot_date(date)
    merged = get_merged_contracts(storage, snapshot_date=date, underlying_ins_codes=codes)
    if merged.empty:
        return _json({"items": [], "total": 0})
    if q:
        merged = merged[_text_mask(merged, ("symbol", "short_name", "long_name"), q)]
    return _json({"items": _df_to_records(merged), "total": len(merged)})


def underlyings(request: Request) -> JSONResponse:
    q = _query_value(request, "q")
    date = _query_value(request, "date")
    _parse_snapshot_date(date)
    if storage.get_watchlist_codes():
        _ensure_snapshot_date(date)
    payload = get_watchlist_underlyings(storage, q=q, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return _json(payload)


def underlying_contracts(request: Request) -> JSONResponse:
    underlying_key = request.path_params["underlying_key"]
    q = _query_value(request, "q")
    date = _query_value(request, "date")
    _ensure_snapshot_date(date)
    payload = get_underlying_contracts(
        storage,
        underlying_key=underlying_key,
        q=q,
        snapshot_date=date,
    )
    payload["date"] = date or storage.get_latest_snapshot_date()
    return _json(payload)


async def client_type_current(request: Request) -> JSONResponse:
    ins_code = _path_ins_code(request.path_params["ins_code"])
    try:
        row = await run_in_threadpool(fetch_public_client_type_current, ins_code)
    except Exception as exc:
        logger.exception("Client type fetch failed for %s", ins_code)
        raise HTTPException(503, "client type fetch failed") from exc
    return _json({"item": {**(row or {}), "ins_code": str(ins_code)}})


async def client_type_current_many(request: Request) -> JSONResponse:
    body = await _read_json_payload(
        request,
        max_bytes=MAX_API_JSON_BODY_BYTES,
        invalid_detail="invalid JSON body",
    )
    raw_codes = body.get("ins_codes") if isinstance(body, dict) else None
    if not isinstance(raw_codes, list):
        raise HTTPException(400, "ins_codes must be a list")
    if len(raw_codes) > MAX_CLIENT_TYPE_BATCH_SIZE:
        raise HTTPException(413, "too many instrument codes")
    ins_codes: List[int] = []
    seen: set[int] = set()
    for raw_code in raw_codes:
        ins_code = _path_ins_code(raw_code)
        if ins_code in seen:
            continue
        seen.add(ins_code)
        ins_codes.append(ins_code)
    try:
        rows = await run_in_threadpool(fetch_public_client_type_current_many, ins_codes)
    except Exception as exc:
        logger.exception("Client type batch fetch failed for %d contracts", len(ins_codes))
        raise HTTPException(503, "client type fetch failed") from exc
    rows = [{**row, "ins_code": str(row["ins_code"])} for row in rows]
    return _json({"items": rows, "total": len(rows)})


def underlying_trend(request: Request) -> JSONResponse:
    underlying_key = request.path_params["underlying_key"]
    date = _query_value(request, "date")
    _parse_snapshot_date(date)
    days = _query_int(request, "days", default=7, min_value=2, max_value=10)
    assert days is not None
    end_date = date or storage.get_latest_snapshot_date()
    if not end_date:
        return _json({"items": [], "total": 0, "summary": {}, "dates": [], "sources": {}, "skipped": {}})
    if not _underlying_known_locally(underlying_key):
        return _json({
            "items": [],
            "total": 0,
            "summary": {},
            "dates": [],
            "sources": {},
            "skipped": {end_date: "unknown underlying"},
            "date": end_date,
        })
    dates, sources, skipped = _collect_trend_dates(underlying_key, end_date, days)
    payload = get_underlying_trend(storage, underlying_key=underlying_key, dates=dates)
    payload["dates"] = dates
    payload["sources"] = sources
    payload["skipped"] = skipped
    payload["date"] = end_date
    return _json(payload)


def _option_sentiment_selection(request: Request) -> tuple[str | None, str]:
    data_date = _query_value(request, "date")
    _parse_snapshot_date(data_date)
    expiry = _translate_digits(_query_value(request, "expiry") or "all")
    if expiry != "all":
        try:
            datetime.strptime(expiry, "%Y%m%d")
        except ValueError as exc:
            raise HTTPException(400, "invalid expiry") from exc
        if not re.fullmatch(r"[0-9]{8}", expiry):
            raise HTTPException(400, "invalid expiry")
    return data_date, expiry


def option_sentiment(request: Request) -> JSONResponse:
    data_date, expiry = _option_sentiment_selection(request)
    progress = option_sentiment_refresh_job.status()
    payload = get_option_sentiment_payload(storage, _query_value(request, "underlying_key"), expiry, data_date)
    payload["refresh"] = progress
    return _json(payload)


async def option_sentiment_flow(request: Request) -> JSONResponse:
    data_date, expiry = _option_sentiment_selection(request)
    if not data_date:
        raise HTTPException(400, "date is required")
    payload = await run_in_threadpool(get_flow_payload, storage, _query_value(request, "underlying_key"), expiry, data_date)
    return _json(payload)


async def refresh_option_sentiment(request: Request) -> JSONResponse:
    body = await _read_json_payload(request, MAX_API_JSON_BODY_BYTES, "invalid refresh body")
    raw_date = body.get("date")
    if raw_date is not None and not isinstance(raw_date, str):
        raise HTTPException(400, "invalid date")
    data_date = _translate_digits(raw_date.strip()) if raw_date else None
    _parse_snapshot_date(data_date)
    if data_date:
        snapshot = await run_in_threadpool(storage.get_option_sentiment_snapshot, data_date)
        if snapshot and activity_date_verified(snapshot):
            return _json({"status": "cached"})
    status = option_sentiment_refresh_job.begin(data_date)
    if status["status"] != "started":
        return _json(status)
    tasks = BackgroundTasks()
    tasks.add_task(option_sentiment_refresh_job.run, storage, data_date)
    return _json(status, status_code=202, background=tasks)


def sentiment(request: Request) -> JSONResponse:
    q = _query_value(request, "q")
    date = _query_value(request, "date")
    _ensure_snapshot_date(date)
    payload = get_sentiment(storage, q=q, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return _json(payload)


def open_interest_history(request: Request) -> JSONResponse:
    ins_code = request.path_params["ins_code"]
    date = _query_value(request, "date")
    _parse_snapshot_date(date)
    try:
        exact_ins_code = _path_ins_code(ins_code)
    except HTTPException as exc:
        raise HTTPException(400, "invalid ins_code") from exc
    df = storage.get_open_interest_history_df(ins_code=exact_ins_code, through_date=date)
    return _json({"ins_code": str(exact_ins_code), "date": date, "history": _df_to_records(df)})


async def refresh_status(request: Request) -> JSONResponse:
    return _json(_public_refresh_status())


async def watchlist(request: Request) -> JSONResponse:
    if request.method == "PUT":
        payload = await _read_json_payload(request, MAX_API_JSON_BODY_BYTES, "invalid watchlist payload")
        raw_codes = payload.get("ins_codes")
        if not isinstance(raw_codes, list):
            raise HTTPException(400, "ins_codes must be a list")
        codes = sorted({_path_ins_code(code) for code in raw_codes})
        with _refresh_lock:
            if _refresh_status["running"]:
                raise HTTPException(409, "به‌روزرسانی داده در حال انجام است؛ پس از اتمام، فهرست را ذخیره کنید.")
            try:
                save_watchlist(storage, codes)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
    return _json({"ins_codes": [str(code) for code in storage.get_watchlist_codes()]})


def underlying_catalog(request: Request) -> JSONResponse:
    items = storage.get_underlying_catalog()
    return _json({
        "items": items,
        "total": len(items),
        "fetched_at": max((item["fetched_at"] for item in items), default=None),
        "refresh": catalog_refresh_job.status(),
    })


async def refresh_catalog(request: Request) -> JSONResponse:
    if not catalog_refresh_job.begin():
        return _json({"status": "already_running"})
    tasks = BackgroundTasks()
    tasks.add_task(catalog_refresh_job.run, storage)
    return _json({"status": "started"}, status_code=202, background=tasks)


def _expiry_filters(request: Request) -> ExpiryFilters:
    option_type = _query_value(request, "option_type")
    moneyness = _query_value(request, "moneyness")
    arbitrage_only = request.query_params.get("arbitrage_only")
    if arbitrage_only is not None:
        arbitrage_only = arbitrage_only.strip()
    if arbitrage_only is not None and arbitrage_only not in {"true", "false"}:
        raise HTTPException(400, "invalid arbitrage_only")
    if option_type is not None and option_type not in {"call", "put"}:
        raise HTTPException(400, "invalid option_type")
    if moneyness is not None and moneyness not in {"ITM", "ATM", "OTM"}:
        raise HTTPException(400, "invalid moneyness")
    min_days = _query_int(request, "min_days", default=0, min_value=0)
    max_days = _query_int(request, "max_days", min_value=0)
    if max_days is not None and max_days < min_days:
        raise HTTPException(400, "max_days is below min_days")
    min_score = None
    raw_score = _query_value(request, "min_score")
    if raw_score is not None:
        try:
            min_score = float(_clean_numeric_text(raw_score).replace("٫", "."))
        except ValueError as exc:
            raise HTTPException(400, "invalid min_score") from exc
        if not isfinite(min_score):
            raise HTTPException(400, "invalid min_score")
    return ExpiryFilters(
        underlying_ins_code=_query_int(request, "underlying_ins_code", min_value=1, max_value=2**63 - 1),
        option_type=option_type, min_days=min_days, max_days=max_days, moneyness=moneyness,
        min_score=min_score, min_volume=_query_int(request, "min_volume", min_value=0),
        arbitrage_only=arbitrage_only == "true",
    )


def expiries(request: Request) -> JSONResponse:
    filters = _expiry_filters(request)
    today = datetime.now(MARKET_TZ).date()
    progress = expiry_refresh_job.view(today, filters)
    payload = storage.get_expiry_snapshot(today, filters=filters)
    payload.update(progress)
    return _json(payload)


async def refresh_expiries(request: Request) -> JSONResponse:
    status = expiry_refresh_job.begin()
    if status != "started":
        return _json({"status": status})
    tasks = BackgroundTasks()
    tasks.add_task(expiry_refresh_job.run, storage)
    return _json({"status": status}, status_code=202, background=tasks)


async def refresh(request: Request) -> JSONResponse:
    limit = _query_int(request, "limit", min_value=1, max_value=5000)
    # Serialize selection reads and updates with the refresh reservation.
    with _refresh_lock:
        codes = set(storage.get_watchlist_codes())
        if not codes:
            return _json({"status": "no_symbols"})
        status = _begin_refresh()
    if status != "started":
        return _json({"status": status})
    background_tasks = BackgroundTasks()
    background_tasks.add_task(_run_refresh, limit, codes)
    return _json({"status": "started"}, background=background_tasks)


def _dashboard_response(request: Request, filename: str = "index.html") -> Response:
    if not _has_dashboard_session(request):
        raise HTTPException(404, "dashboard session not found")
    index_path = WEB_ROOT / filename
    if not index_path.exists():
        raise HTTPException(404, "index.html not found")
    response = FileResponse(index_path)
    response.set_cookie(
        LOCAL_API_COOKIE,
        LOCAL_API_TOKEN,
        path="/api",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return _apply_dashboard_security_headers(response)


async def index(request: Request) -> Response:
    return _dashboard_response(request)


async def underlying_page(request: Request) -> Response:
    return _dashboard_response(request)


async def symbols_page(request: Request) -> Response:
    return _dashboard_response(request, "symbols.html")


async def dashboard_not_found(request: Request) -> Response:
    raise HTTPException(404, "dashboard session required")


routes = [
    Route("/api/health", health, methods=["GET"]),
    Route("/api/activation/status", activation_status, methods=["GET"]),
    Route("/api/activation", activate, methods=["POST"]),
    Route("/api/summary", summary, methods=["GET"]),
    Route("/api/dates", dates, methods=["GET"]),
    Route("/api/calendar/today", calendar_today, methods=["GET"]),
    Route("/api/calendar/{year:int}/{month:int}/events", calendar_events, methods=["GET"]),
    Route("/api/calendar/{year:int}/{month:int}/{day:int}/info", calendar_day_info, methods=["GET"]),
    Route("/api/contracts", contracts, methods=["GET"]),
    Route("/api/client-type", client_type_current_many, methods=["POST"]),
    Route("/api/client-type/{ins_code}", client_type_current, methods=["GET"]),
    Route("/api/underlyings", underlyings, methods=["GET"]),
    Route("/api/watchlist", watchlist, methods=["GET", "PUT"]),
    Route("/api/underlyings/catalog", underlying_catalog, methods=["GET"]),
    Route("/api/underlyings/catalog/refresh", refresh_catalog, methods=["POST"]),
    Route("/api/expiries", expiries, methods=["GET"]),
    Route("/api/expiries/refresh", refresh_expiries, methods=["POST"]),
    Route("/api/underlyings/{underlying_key}/contracts", underlying_contracts, methods=["GET"]),
    Route("/api/underlyings/{underlying_key}/trend", underlying_trend, methods=["GET"]),
    Route("/api/sentiment", sentiment, methods=["GET"]),
    Route("/api/option-sentiment", option_sentiment, methods=["GET"]),
    Route("/api/option-sentiment/flow", option_sentiment_flow, methods=["GET"]),
    Route("/api/option-sentiment/refresh", refresh_option_sentiment, methods=["POST"]),
    Route("/api/open-interest/{ins_code}", open_interest_history, methods=["GET"]),
    Route("/api/refresh/status", refresh_status, methods=["GET"]),
    Route("/api/refresh", refresh, methods=["POST"]),
    Route("/", dashboard_not_found, methods=["GET"]),
    Route("/dashboard/{dashboard_session}", index, methods=["GET"]),
    Route("/dashboard/{dashboard_session}/", index, methods=["GET"]),
    Route("/dashboard/{dashboard_session}/symbols", symbols_page, methods=["GET"]),
    Route("/dashboard/{dashboard_session}/underlying/{underlying_key}", underlying_page, methods=["GET"]),
]
if STATIC_DIR.exists():
    routes.append(Mount("/static", app=NoStoreStaticFiles(directory=STATIC_DIR), name="static"))

app = Starlette(
    routes=routes,
    middleware=[Middleware(LocalApiTokenMiddleware)],
    exception_handlers={
        HTTPException: handle_http_error,
        Exception: handle_unexpected_error,
    },
)
