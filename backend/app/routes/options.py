"""GET /api/options/*: Nifty option chain for the Options tab (read-only, token stays on the server)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query

from app.ai.chain import NIFTY_CHAIN_KEY, weekly_expiry
from app.backtest.expiry import load_default_calendar
from app.config import settings
from app.options.chain_history import latest_snapshot, view_from_snapshot
from app.options.chain_view import build_chain_view, market_status
from app.options.time import IST
from app.upstox.client import UpstoxAuthError, UpstoxError, UpstoxRateLimited
from app.upstox.deps import get_client

router = APIRouter(prefix="/api/options")

CACHE_SECONDS = 30


class OptionsSource(Protocol):
    def option_chain(self, instrument_key: str, expiry: str) -> list[dict[str, Any]]: ...

    def option_contracts(self, instrument_key: str) -> list[date]: ...


class ChainCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str, now: float) -> dict[str, Any] | None:
        with self._lock:
            hit = self._items.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        return None

    def put(self, key: str, now: float, value: dict[str, Any]) -> None:
        with self._lock:
            self._items[key] = (now, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_CACHE = ChainCache()


def get_options_client() -> OptionsSource | None:
    return get_client()


def get_options_clock() -> Callable[[], float]:
    return time.time


def get_options_dir() -> Path:
    return settings.data_dir / "options"


def get_options_cache() -> ChainCache:
    return _CACHE


def default_expiry(now: float) -> str:
    """Nearest weekly expiry; after 15:30 IST on expiry day, the next one."""
    local = datetime.fromtimestamp(now, IST)
    today = local.date()
    exp = weekly_expiry(today)
    if exp == today.isoformat() and (local.hour, local.minute) >= (15, 30):
        exp = weekly_expiry(today + timedelta(days=1))
    return exp


def next_expiry_after(exp: str) -> str:
    return weekly_expiry(date.fromisoformat(exp) + timedelta(days=1))


def _is_session(day: date) -> bool:
    return load_default_calendar().is_trading_day(day)


def _public(view: dict[str, Any], *, expiries: list[str] | None = None) -> dict[str, Any]:
    """Drop the full-chain helper field before sending to the browser."""
    out = {k: v for k, v in view.items() if k != "all_strikes"}
    if expiries is not None:
        out["expiries"] = expiries
    return out


def _fetch_live(
    client: OptionsSource,
    expiry: str,
    now: float,
    *,
    status: str,
) -> dict[str, Any]:
    rows = client.option_chain(NIFTY_CHAIN_KEY, expiry)
    return build_chain_view(rows, now=int(now), expiry=expiry, status=status, is_session=_is_session)


def _list_expiries(client: OptionsSource, now: float) -> list[str]:
    try:
        dates = client.option_contracts(NIFTY_CHAIN_KEY)
    except UpstoxError:
        dates = []
    if dates:
        return [d.isoformat() for d in dates]
    # fallback: calendar nearest + a few weeklies
    first = default_expiry(now)
    out = [first]
    cur = first
    for _ in range(5):
        cur = next_expiry_after(cur)
        out.append(cur)
    return out


@router.get("/expiries")
def option_expiries(
    client: Annotated[OptionsSource | None, Depends(get_options_client)],
    clock: Annotated[Callable[[], float], Depends(get_options_clock)],
) -> dict[str, Any]:
    if client is None:
        raise HTTPException(503, "Upstox data token missing: Options needs the live option chain")
    now = float(clock())
    try:
        expiries = _list_expiries(client, now)
    except UpstoxAuthError as e:
        raise HTTPException(503, str(e)) from e
    except UpstoxRateLimited as e:
        raise HTTPException(429, str(e)) from e
    except UpstoxError as e:
        raise HTTPException(502, f"option contracts failed: {e}") from e
    return {"expiries": expiries, "default": default_expiry(now)}


@router.get("/chain")
def option_chain(
    client: Annotated[OptionsSource | None, Depends(get_options_client)],
    clock: Annotated[Callable[[], float], Depends(get_options_clock)],
    cache: Annotated[ChainCache, Depends(get_options_cache)],
    options_dir: Annotated[Path, Depends(get_options_dir)],
    expiry: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    if client is None:
        raise HTTPException(503, "Upstox data token missing: Options needs the live option chain")
    now = float(clock())
    exp = expiry or default_expiry(now)
    try:
        date.fromisoformat(exp)
    except ValueError as e:
        raise HTTPException(422, f"bad expiry {exp}") from e

    status = market_status(now, is_session=_is_session)
    expiries = None  # filled on live path

    if status != "LIVE":
        snap = latest_snapshot(options_dir, expiry=exp)
        if snap is not None:
            view = view_from_snapshot(snap, status="MARKET_CLOSED")
            try:
                expiries = _list_expiries(client, now)
            except UpstoxError:
                expiries = [exp]
            return _public(view, expiries=expiries)
        # no snapshot yet → one live Upstox call, still labelled MARKET CLOSED
        try:
            expiries = _list_expiries(client, now)
            view = _fetch_live(client, exp, now, status="MARKET_CLOSED")
        except UpstoxAuthError as e:
            raise HTTPException(503, str(e)) from e
        except UpstoxRateLimited as e:
            raise HTTPException(429, str(e)) from e
        except UpstoxError as e:
            raise HTTPException(502, f"option chain failed: {e}") from e
        view["from_snapshot"] = False
        return _public(view, expiries=expiries)

    cache_key = exp
    if not refresh:
        hit = cache.get(cache_key, now)
        if hit is not None:
            return hit
    try:
        expiries = _list_expiries(client, now)
        view = _fetch_live(client, exp, now, status="LIVE")
    except UpstoxAuthError as e:
        raise HTTPException(503, str(e)) from e
    except UpstoxRateLimited as e:
        raise HTTPException(429, str(e)) from e
    except UpstoxError as e:
        raise HTTPException(502, f"option chain failed: {e}") from e
    view["from_snapshot"] = False
    payload = _public(view, expiries=expiries)
    cache.put(cache_key, now, payload)
    return payload
