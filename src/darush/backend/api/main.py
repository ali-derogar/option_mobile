"""FastAPI application for TSETMC options web UI."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from darush.backend.api.data import (
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
from darush.backend.pipeline import run_pipeline
from darush.backend.services.historical_options import import_public_option_history
from darush.backend.storage import Storage

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
STATIC_DIR = WEB_ROOT / "static"

app = FastAPI(title="TSETMC Options", version="1.0.0")
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_refresh_status(**payload: Any) -> None:
    with _refresh_lock:
        _refresh_status.update(payload)


def _run_refresh(limit: Optional[int] = None) -> None:
    global _refresh_status
    with _refresh_lock:
        if _refresh_status["running"]:
            return
        _refresh_status["running"] = True
        _refresh_status["last_error"] = None
        _refresh_status["last_result"] = None
        _refresh_status["stage"] = "starting"
        _refresh_status["message"] = "شروع به‌روزرسانی"
        _refresh_status["started_at"] = _utc_now_iso()
        _refresh_status["finished_at"] = None
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
            last_error=str(exc),
            stage="failed",
            message=f"خطا در به‌روزرسانی: {exc}",
        )
    finally:
        _update_refresh_status(running=False, finished_at=_utc_now_iso())


def _ensure_snapshot_date(date: Optional[str]) -> None:
    if not date:
        return
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "invalid date; expected YYYY-MM-DD")
    if storage.has_contract_snapshot_date(date):
        return
    with _history_import_lock:
        if storage.has_contract_snapshot_date(date):
            return
        try:
            contracts, client_type_rows = import_public_option_history(
                date,
                metadata_by_ins_code=storage.get_contract_metadata_by_ins_code(),
            )
        except Exception as exc:
            logger.exception("Historical import failed for %s", date)
            raise HTTPException(503, f"historical import failed: {exc}") from exc
        if not contracts:
            raise HTTPException(404, "historical data not found for date")
        storage.insert_contract_snapshot(contracts, snapshot_date=date)
        if client_type_rows:
            storage.insert_client_type_stats(client_type_rows)
            storage.insert_money_flow(client_type_rows)
        logger.info(
            "Imported historical option snapshot for %s: %d contracts, %d client type rows",
            date,
            len(contracts),
            len(client_type_rows),
        )


def _collect_trend_dates(
    underlying_key: str,
    end_date: str,
    days: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    try:
        cursor = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(400, "invalid date; expected YYYY-MM-DD") from exc

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
    limit: Optional[int] = Query(None),
) -> Dict[str, Any]:
    if _refresh_status["running"]:
        return {"status": "already_running"}
    background_tasks.add_task(_run_refresh, limit)
    return {"status": "started"}


@app.get("/")
async def index() -> FileResponse:
    index_path = WEB_ROOT / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "index.html not found")
    return FileResponse(index_path)


@app.get("/underlying/{underlying_key}")
async def underlying_page(underlying_key: str) -> FileResponse:
    index_path = WEB_ROOT / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "index.html not found")
    return FileResponse(index_path)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
