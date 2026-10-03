"""Time to expiry: trading minutes (the model default) vs calendar minutes (India VIX)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
SESSION_MIN = CLOSE_MIN - OPEN_MIN  # 375
TRADING_MIN_PER_YEAR = SESSION_MIN * 250  # 93_750
CALENDAR_MIN_PER_YEAR = 365 * 24 * 60  # 525_600, the India VIX year
BASES = ("trading", "calendar")


def _local(t: int) -> datetime:
    return datetime.fromtimestamp(int(t), IST)


def _clock_min(local: datetime) -> int:
    return local.hour * 60 + local.minute


def trading_minutes_to_expiry(t: int, expiry: date, is_trading_day: Callable[[date], bool]) -> int:
    """Minutes of regular session still to trade until 15:30 IST on `expiry`.

    Weekends and holidays contribute 0. A bar that starts at 15:29 on expiry day has 1 minute
    left; at or after 15:30 it is 0. Days after expiry are 0.
    """
    local = _local(t)
    day = local.date()
    minute = _clock_min(local)
    if day > expiry:
        return 0

    def today_left(d: date, minute_of_day: int) -> int:
        if not is_trading_day(d):
            return 0
        if minute_of_day < OPEN_MIN:
            return SESSION_MIN
        if minute_of_day >= CLOSE_MIN:
            return 0
        return CLOSE_MIN - minute_of_day

    if day == expiry:
        return today_left(day, minute)

    left = today_left(day, minute)
    d = day + timedelta(days=1)
    while d < expiry:
        if is_trading_day(d):
            left += SESSION_MIN
        d += timedelta(days=1)
    if is_trading_day(expiry):
        left += SESSION_MIN
    return left


def calendar_minutes_to_expiry(t: int, expiry: date) -> int:
    """Whole minutes until 15:30 IST on `expiry` (weekends and holidays stay in the count)."""
    end = datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=IST)
    now = _local(t)
    if now >= end:
        return 0
    return int((end - now).total_seconds() // 60)


def years_to_expiry(
    t: int, expiry: date, is_trading_day: Callable[[date], bool], basis: str = "trading"
) -> float:
    if basis not in BASES:
        raise ValueError(f"basis must be one of {BASES}, got {basis!r}")
    if basis == "trading":
        return trading_minutes_to_expiry(t, expiry, is_trading_day) / TRADING_MIN_PER_YEAR
    return calendar_minutes_to_expiry(t, expiry) / CALENDAR_MIN_PER_YEAR
