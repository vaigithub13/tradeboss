"""Which days the live feed may open a session on: our calendar, not the feed's market_info.

A listed session is a trading day of the shipped NSE calendar (Monday-Friday, not a holiday) or a listed special
session (`MUHURAT_DATES`). On any other day market_info "open" opens nothing; only real in-session trades dated that
day do (the engine and the recorder then warn "unlisted session detected", so the day can be added here).

Paper slot autostart uses `normal_trading_day` only (skips Muhurat / listed special windows). The backend may still
start on a listed special day so the feed can record.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import cache

from app.backtest.expiry import load_default_calendar
from app.data.sessions import MUHURAT_DATES, SESSION_WINDOWS


@cache
def _trading_day() -> Callable[[date], bool]:
    return load_default_calendar().is_trading_day


def listed_session(d: date) -> bool:
    return d in MUHURAT_DATES or _trading_day()(d)


def normal_trading_day(d: date) -> bool:
    """A regular Mon–Fri NSE session (not a holiday, not Muhurat, not another listed special window)."""
    if d in MUHURAT_DATES or d in SESSION_WINDOWS:
        return False
    return _trading_day()(d)
