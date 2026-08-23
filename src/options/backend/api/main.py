"""FastAPI application for TSETMC options web UI."""

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

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

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

app = FastAPI(
    title="TSETMC Options",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
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


@app.middleware("http")
async def require_local_api_token(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.headers.get(LOCAL_API_HEADER) != LOCAL_API_TOKEN:
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error for %s", request.url.path)
    return JSONResponse({"detail": "internal server error"}, status_code=500)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_refresh_status(**payload: Any) -> None:
    with _refresh_lock:
        _refresh_status.update(payload)


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
    except Exception as exc:
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


def _begin_refresh() -> bool:
    global _last_refresh_request_at
    now = time.monotonic()
    with _refresh_lock:
        if _refresh_status["running"]:
            return False
        if now - _last_refresh_request_at < REFRESH_COOLDOWN_SECONDS:
            return False
        _last_refresh_request_at = now
        _refresh_status["running"] = True
        _refresh_status["last_error"] = None
        _refresh_status["last_result"] = None
        _refresh_status["stage"] = "starting"
        _refresh_status["message"] = "شروع به‌روزرسانی"
        _refresh_status["started_at"] = _utc_now_iso()
        _refresh_status["finished_at"] = None
    return True


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


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/summary")
def summary(
    date: Optional[str] = Query(None, description="Snapshot date in YYYY-MM-DD"),
) -> Dict[str, Any]:
    _ensure_snapshot_date(date)
    payload = get_summary(storage, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return payload


@app.get("/api/dates")
def dates() -> Dict[str, Any]:
    return get_available_dates(storage)


@app.get("/api/contracts")
def contracts(
    q: Optional[str] = Query(None, description="Search symbol or name"),
    date: Optional[str] = Query(None, description="Snapshot date in YYYY-MM-DD"),
) -> Dict[str, Any]:
    _ensure_snapshot_date(date)
    merged = get_merged_contracts(storage, snapshot_date=date)
    if merged.empty:
        return {"items": [], "total": 0}
    if q:
        merged = merged[_text_mask(merged, ("symbol", "short_name", "long_name"), q)]
    return {"items": _df_to_records(merged), "total": len(merged)}


@app.get("/api/underlyings")
def underlyings(
    q: Optional[str] = Query(None, description="Search underlying symbol or name"),
    date: Optional[str] = Query(None, description="Snapshot date in YYYY-MM-DD"),
) -> Dict[str, Any]:
    _ensure_snapshot_date(date)
    payload = get_underlyings(storage, q=q, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return payload


@app.get("/api/underlyings/{underlying_key}/contracts")
def underlying_contracts(
    underlying_key: str,
    q: Optional[str] = Query(None, description="Search contract symbol or name"),
    date: Optional[str] = Query(None, description="Snapshot date in YYYY-MM-DD"),
) -> Dict[str, Any]:
    _ensure_snapshot_date(date)
    payload = get_underlying_contracts(
        storage,
        underlying_key=underlying_key,
        q=q,
        snapshot_date=date,
    )
    payload["date"] = date or storage.get_latest_snapshot_date()
    return payload


@app.get("/api/underlyings/{underlying_key}/trend")
def underlying_trend(
    underlying_key: str,
    date: Optional[str] = Query(None, description="End date in YYYY-MM-DD"),
    days: int = Query(7, ge=2, le=10, description="Trading days to include"),
) -> Dict[str, Any]:
    end_date = date or storage.get_latest_snapshot_date()
    if not end_date:
        return {"items": [], "total": 0, "summary": {}, "dates": [], "sources": {}, "skipped": {}}
    if not _underlying_known_locally(underlying_key):
        return {
            "items": [],
            "total": 0,
            "summary": {},
            "dates": [],
            "sources": {},
            "skipped": {end_date: "unknown underlying"},
            "date": end_date,
        }
    dates, sources, skipped = _collect_trend_dates(underlying_key, end_date, days)
    payload = get_underlying_trend(storage, underlying_key=underlying_key, dates=dates)
    payload["dates"] = dates
    payload["sources"] = sources
    payload["skipped"] = skipped
    payload["date"] = end_date
    return payload


@app.get("/api/sentiment")
def sentiment(
    q: Optional[str] = Query(None, description="Search underlying symbol or sentiment"),
    date: Optional[str] = Query(None, description="Snapshot date in YYYY-MM-DD"),
) -> Dict[str, Any]:
    _ensure_snapshot_date(date)
    payload = get_sentiment(storage, q=q, snapshot_date=date)
    payload["date"] = date or storage.get_latest_snapshot_date()
    return payload


@app.get("/api/open-interest/{ins_code}")
def open_interest_history(
    ins_code: str,
    date: Optional[str] = Query(None, description="Include history through YYYY-MM-DD"),
) -> Dict[str, Any]:
    _parse_snapshot_date(date)
    try:
        exact_ins_code = int(ins_code)
    except ValueError as exc:
        raise HTTPException(400, "invalid ins_code") from exc
    df = storage.get_open_interest_history_df(ins_code=exact_ins_code, through_date=date)
    return {"ins_code": str(exact_ins_code), "date": date, "history": _df_to_records(df)}


@app.get("/api/refresh/status")
def refresh_status() -> Dict[str, Any]:
    return dict(_refresh_status)


@app.post("/api/refresh")
def refresh(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(None, ge=1, le=5000),
) -> Dict[str, Any]:
    if not _begin_refresh():
        return {"status": "already_running"}
    background_tasks.add_task(_run_refresh, limit)
    return {"status": "started"}


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
    return response


@app.get("/")
async def index() -> Response:
    return _dashboard_response()


@app.get("/underlying/{underlying_key}")
async def underlying_page(underlying_key: str) -> Response:
    return _dashboard_response()


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
