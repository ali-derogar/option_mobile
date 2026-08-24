"""Starlette application for TSETMC options web UI."""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from starlette.applications import Starlette
from starlette.background import BackgroundTasks
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from options.backend.activation import is_valid as is_activation_code_valid
from options.backend.api.data import (
    _df_to_records,
    _text_mask,
    get_available_dates,
    get_merged_contracts,
    get_summary,
    get_sentiment,
    get_underlying_contracts,
    get_underlying_trend,
    get_underlyings,
)
from options.backend.pipeline import run_pipeline
from options.backend.services.historical_options import import_public_option_history
from options.backend.storage import MARKET_TZ, Storage

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
STATIC_DIR = WEB_ROOT / "static"

LOCAL_API_HEADER = "x-options-api-token"
LOCAL_API_COOKIE = "options_api_token"
LOCAL_API_TOKEN = secrets.token_urlsafe(32)
MAX_HISTORICAL_LOOKBACK_DAYS = 370
REFRESH_COOLDOWN_SECONDS = 60
DASHBOARD_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'none'"
)

storage = Storage()

_refresh_lock = threading.Lock()
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


class LocalApiTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/") and request.headers.get(LOCAL_API_HEADER) != LOCAL_API_TOKEN:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        if (
            request.url.path.startswith("/api/")
            and request.url.path not in {"/api/health", "/api/activation/status", "/api/activation"}
            and not storage.is_activated()
        ):
            return JSONResponse({"detail": "activation_required"}, status_code=423)
        return await call_next(request)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error for %s", request.url.path)
    return JSONResponse({"detail": "internal server error"}, status_code=500)


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _json(payload: Dict[str, Any], status_code: int = 200, background: Optional[BackgroundTasks] = None) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, background=background)


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


def _run_refresh(limit: Optional[int] = None) -> None:
    try:
        result = run_pipeline(
            limit=limit,
            skip_client_type=False,
            delay_between_calls=0.15,
            progress_callback=lambda payload: _update_refresh_status(**payload),
        )
        _update_refresh_status(
            last_result=result,
            stage="done",
            message=f"به‌روزرسانی کامل شد؛ {result.get('options', 0)} قرارداد",
        )
    except Exception:
        logger.exception("Refresh failed")
        _update_refresh_status(
            last_error="refresh failed",
            stage="failed",
            message="خطا در به‌روزرسانی داده. کمی بعد دوباره تلاش کنید.",
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

    found: list[str] = []
    sources: dict[str, str] = {}
    skipped: dict[str, str] = {}
    attempts = 0
    max_attempts = max(21, days * 4)
    while len(found) < days and attempts < max_attempts:
        current = cursor - timedelta(days=attempts)
        current_date = current.isoformat()
        had_snapshot = storage.has_contract_snapshot_date(current_date)
        try:
            _ensure_snapshot_date(current_date)
        except HTTPException as exc:
            if exc.status_code in {404, 503}:
                skipped[current_date] = str(exc.detail)
                attempts += 1
                continue
            raise
        contracts = get_underlying_contracts(
            storage,
            underlying_key=underlying_key,
            snapshot_date=current_date,
        )
        if not contracts.get("items"):
            skipped[current_date] = "no contracts for underlying"
            attempts += 1
            continue
        found.append(current_date)
        sources[current_date] = "snapshot" if had_snapshot else "imported"
        attempts += 1
    found.reverse()
    return found, sources, skipped


def _underlying_known_locally(underlying_key: str) -> bool:
    return storage.has_underlying_snapshot_key(underlying_key)


async def health(request: Request) -> JSONResponse:
    return _json({"status": "ok"})


async def activation_status(request: Request) -> JSONResponse:
    return _json({"activated": storage.is_activated()})


async def activate(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid activation payload")
    code = payload.get("code")
    if not isinstance(code, str) or not is_activation_code_valid(code.strip()):
        raise HTTPException(400, "activation code was not accepted")
    storage.set_activated(True)
    return _json({"activated": True})


async def summary(request: Request) -> JSONResponse:
    date = _query_value(request, "date")
    _ensure_snapshot_date(date)
    payload = get_summary(storage, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return _json(payload)


async def dates(request: Request) -> JSONResponse:
    return _json(get_available_dates(storage))


async def contracts(request: Request) -> JSONResponse:
    q = _query_value(request, "q")
    date = _query_value(request, "date")
    _ensure_snapshot_date(date)
    merged = get_merged_contracts(storage, snapshot_date=date)
    if merged.empty:
        return _json({"items": [], "total": 0})
    if q:
        merged = merged[_text_mask(merged, ("symbol", "short_name", "long_name"), q)]
    return _json({"items": _df_to_records(merged), "total": len(merged)})


async def underlyings(request: Request) -> JSONResponse:
    q = _query_value(request, "q")
    date = _query_value(request, "date")
    _ensure_snapshot_date(date)
    payload = get_underlyings(storage, q=q, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return _json(payload)


async def underlying_contracts(request: Request) -> JSONResponse:
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


async def underlying_trend(request: Request) -> JSONResponse:
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


async def sentiment(request: Request) -> JSONResponse:
    q = _query_value(request, "q")
    date = _query_value(request, "date")
    _ensure_snapshot_date(date)
    payload = get_sentiment(storage, q=q, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return _json(payload)


async def open_interest_history(request: Request) -> JSONResponse:
    ins_code = request.path_params["ins_code"]
    date = _query_value(request, "date")
    _parse_snapshot_date(date)
    try:
        exact_ins_code = int(_clean_numeric_text(ins_code))
    except ValueError as exc:
        raise HTTPException(400, "invalid ins_code") from exc
    if exact_ins_code <= 0:
        raise HTTPException(400, "invalid ins_code")
    df = storage.get_open_interest_history_df(ins_code=exact_ins_code, through_date=date)
    return _json({"ins_code": str(exact_ins_code), "date": date, "history": _df_to_records(df)})


async def refresh_status(request: Request) -> JSONResponse:
    return _json(_public_refresh_status())


async def refresh(request: Request) -> JSONResponse:
    limit = _query_int(request, "limit", min_value=1, max_value=5000)
    status = _begin_refresh()
    if status != "started":
        return _json({"status": status})
    background_tasks = BackgroundTasks()
    background_tasks.add_task(_run_refresh, limit)
    return _json({"status": "started"}, background=background_tasks)


def _dashboard_response() -> Response:
    index_path = WEB_ROOT / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "index.html not found")
    response = FileResponse(index_path)
    response.set_cookie(
        LOCAL_API_COOKIE,
        LOCAL_API_TOKEN,
        path="/",
        secure=False,
        httponly=False,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = DASHBOARD_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def index(request: Request) -> Response:
    return _dashboard_response()


async def underlying_page(request: Request) -> Response:
    return _dashboard_response()


routes = [
    Route("/api/health", health, methods=["GET"]),
    Route("/api/activation/status", activation_status, methods=["GET"]),
    Route("/api/activation", activate, methods=["POST"]),
    Route("/api/summary", summary, methods=["GET"]),
    Route("/api/dates", dates, methods=["GET"]),
    Route("/api/contracts", contracts, methods=["GET"]),
    Route("/api/underlyings", underlyings, methods=["GET"]),
    Route("/api/underlyings/{underlying_key}/contracts", underlying_contracts, methods=["GET"]),
    Route("/api/underlyings/{underlying_key}/trend", underlying_trend, methods=["GET"]),
    Route("/api/sentiment", sentiment, methods=["GET"]),
    Route("/api/open-interest/{ins_code}", open_interest_history, methods=["GET"]),
    Route("/api/refresh/status", refresh_status, methods=["GET"]),
    Route("/api/refresh", refresh, methods=["POST"]),
    Route("/", index, methods=["GET"]),
    Route("/underlying/{underlying_key}", underlying_page, methods=["GET"]),
]
if STATIC_DIR.exists():
    routes.append(Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"))

app = Starlette(
    routes=routes,
    middleware=[Middleware(LocalApiTokenMiddleware)],
    exception_handlers={
        HTTPException: handle_http_error,
        Exception: handle_unexpected_error,
    },
)
