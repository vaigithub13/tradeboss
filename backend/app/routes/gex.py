"""GET /api/gex: Nifty gamma exposure from the live Upstox option chain (read-only, cached)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query

from app.ai.chain import NIFTY_CHAIN_KEY, weekly_expiry
from app.config import settings
from app.options.gex import compute_gex
from app.options.time import IST
from app.upstox.client import UpstoxAuthError, UpstoxError
from app.upstox.deps import get_client

router = APIRouter(prefix="/api")

#: one option-chain call per expiry per this many seconds, however many charts poll
CACHE_SECONDS = 30


class ChainSource(Protocol):
    def option_chain(self, instrument_key: str, expiry: str) -> list[dict[str, Any]]: ...


class GexCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, expiry: str, now: float) -> dict[str, Any] | None:
        with self._lock:
            hit = self._items.get(expiry)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        return None

    def put(self, expiry: str, now: float, value: dict[str, Any]) -> None:
        with self._lock:
            self._items[expiry] = (now, value)


_CACHE = GexCache()


def get_gex_cache() -> GexCache:
    return _CACHE


def get_gex_client() -> ChainSource | None:
    return get_client()


def get_gex_clock() -> Callable[[], float]:
    return time.time


def get_gex_dir():
    return settings.data_dir / "gex"


def default_expiry(now: float) -> str:
    """Nearest weekly expiry; after 15:30 IST on expiry day, the next one."""
    local = datetime.fromtimestamp(now, IST)
    today = local.date()
    exp = weekly_expiry(today)
    if exp == today.isoformat() and (local.hour, local.minute) >= (15, 30):
        exp = weekly_expiry(today + timedelta(days=1))
    return exp


@router.get("/gex")
def gex(
    client: Annotated[ChainSource | None, Depends(get_gex_client)],
    clock: Annotated[Callable[[], float], Depends(get_gex_clock)],
    cache: Annotated[GexCache, Depends(get_gex_cache)],
    expiry: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    if client is None:
        raise HTTPException(503, "Upstox data token missing: GEX needs the live option chain")
    now = float(clock())
    exp = expiry or default_expiry(now)
    try:
        exp_date = date.fromisoformat(exp)
    except ValueError as e:
        raise HTTPException(422, f"bad expiry {exp}") from e
    if not refresh:
        hit = cache.get(exp, now)
        if hit is not None:
            return hit
    try:
        rows = client.option_chain(NIFTY_CHAIN_KEY, exp)
    except UpstoxAuthError as e:
        raise HTTPException(503, str(e)) from e
    except UpstoxError as e:
        raise HTTPException(502, f"option chain failed: {e}") from e
    result = compute_gex(rows, now=int(now), expiry=exp_date)
    cache.put(exp, now, result)
    return result


@router.get("/gex/report")
def gex_report(gex_dir: Annotated[Any, Depends(get_gex_dir)]) -> dict[str, Any]:
    """The latest end-of-day GEX study (written by scripts/gex_report.py)."""
    path = gex_dir / "report_latest.json"
    if not path.is_file():
        raise HTTPException(404, "no GEX report yet: it is written after the close on a day with snapshots")
    return json.loads(path.read_text(encoding="utf-8"))
