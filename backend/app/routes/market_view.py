"""GET /api/market-view: today's pre-market view (built at 08:50 IST, or on demand). Read-only."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.config import settings
from app.data.store import CandleStore
from app.market_view import service
from app.options.time import IST

router = APIRouter(prefix="/api")
_build_lock = threading.Lock()


def get_mv_data_dir() -> Path:
    return settings.data_dir


def get_mv_store() -> CandleStore:
    return CandleStore(settings.candles_dir)


def get_mv_clock() -> Callable[[], float]:
    return time.time


def get_mv_builder() -> Callable[..., dict[str, Any]]:
    return service.build_for


def get_mv_trading_day() -> Callable[[date], bool]:
    # regular sessions only: the participant-OI file of a Muhurat day is not the one to read
    from app.backtest.expiry import load_default_calendar

    return load_default_calendar().is_trading_day


@router.get("/market-view")
def market_view(
    data_dir: Annotated[Path, Depends(get_mv_data_dir)],
    store: Annotated[Any, Depends(get_mv_store)],
    clock: Annotated[Callable[[], float], Depends(get_mv_clock)],
    builder: Annotated[Callable[..., dict[str, Any]], Depends(get_mv_builder)],
    is_trading_day: Annotated[Callable[[date], bool], Depends(get_mv_trading_day)],
    refresh: bool = False,
) -> dict[str, Any]:
    today = datetime.fromtimestamp(clock(), IST).date()
    if not refresh:
        saved = service.load_view(data_dir, today)
        if saved is not None:
            return saved
    with _build_lock:  # one build at a time; a second caller gets the fresh file
        if not refresh:
            saved = service.load_view(data_dir, today)
            if saved is not None:
                return saved
        return builder(today, data_dir=data_dir, store=store, is_trading_day=is_trading_day, clock=clock)


@router.get("/market-view/history")
def market_view_history(data_dir: Annotated[Path, Depends(get_mv_data_dir)]) -> dict[str, Any]:
    return service.history(data_dir)
