"""Which days the live feed may open a session on: our calendar, not the feed's market_info.

A listed session is a trading day of the shipped NSE calendar (Monday-Friday, not a holiday) or a listed special
session (`MUHURAT_DATES`). On any other day market_info "open" opens nothing; only real in-session trades dated that
day do (the engine and the recorder then warn "unlisted session detected", so the day can be added here).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import cache

from app.backtest.expiry import load_default_calendar
from app.data.sessions import MUHURAT_DATES


@cache
def _trading_day() -> Callable[[date], bool]:
    return load_default_calendar().is_trading_day


def listed_session(d: date) -> bool:
    return d in MUHURAT_DATES or _trading_day()(d)
