"""Read-only similar-pattern routes. No orders, no paper slots, no signals."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.data.store import CandleStore
from app.similar.predictions import POINTS_LABEL, RECORD_NOTE, read_predictions, read_reviews, tally, today_table
from app.similar.worker import candles_job, days_job, pool

router = APIRouter(prefix="/api")


def _candles_dir() -> Path:
    return settings.data_dir / "candles"


def _cache_dir() -> Path:
    return settings.data_dir / "similar"


@router.get("/similar/candles")
def similar_candles(
    symbol: str = Query(...),
    timeframe: str = Query("5m"),
    window: int = Query(30, ge=5, le=200),
    as_of: int | None = Query(None),
    method: str = Query("euclidean"),
) -> dict:
    spec = {
        "candles": str(_candles_dir()),
        "cache": str(_cache_dir()),
        "symbol": symbol,
        "timeframe": timeframe,
        "window": window,
        "as_of": as_of,
        "method": method,
    }
    try:
        return pool().submit(candles_job, spec).result(timeout=60)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/similar/days")
def similar_days(
    symbol: str = Query(...),
    at: str = Query("10:15"),
    as_of: int | None = Query(None),
) -> dict:
    spec = {
        "candles": str(_candles_dir()),
        "cache": str(_cache_dir()),
        "symbol": symbol,
        "at": at,
        "as_of": as_of,
    }
    try:
        return pool().submit(days_job, spec).result(timeout=60)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/similar/predictions")
def similar_predictions(day: str | None = Query(None), symbol: str | None = Query(None)) -> dict:
    rows = read_predictions(_cache_dir(), day)
    if symbol:
        rows = [row for row in rows if row.get("symbol") == symbol]
    return {"predictions": rows, "note": RECORD_NOTE, "points_label": POINTS_LABEL}


@router.get("/similar/review")
def similar_review(day: str | None = Query(None)) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    root = _cache_dir()
    today = day or datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    store = CandleStore(_candles_dir())
    return {
        "day": today,
        "today": today_table(root, store, today),
        "totals": tally(read_reviews(root)),
        "note": RECORD_NOTE,
        "points_label": POINTS_LABEL,
    }
