"""Read-only similar-pattern routes. No orders, no paper slots, no signals."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
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
