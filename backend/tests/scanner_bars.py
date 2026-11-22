"""Tiny 5-minute session builders for scanner tests."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def at(day: date, hour: int, minute: int) -> int:
    return int(datetime.combine(day, time(hour, minute), tzinfo=IST).timestamp())


def bar(day: date, hour: int, minute: int, o: float, h: float, low: float, c: float, volume: float = 1000) -> dict:
    return {"time": at(day, hour, minute), "open": o, "high": h, "low": low, "close": c, "volume": volume}


def clock_minutes() -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    hour, minute = 9, 15
    while (hour, minute) <= (15, 25):
        out.append((hour, minute))
        minute += 5
        if minute >= 60:
            hour += 1
            minute -= 60
    return out


def flat_session(day: date, price: float = 100.0, volume: float = 1000) -> list[dict]:
    return [bar(day, h, m, price, price + 0.05, price - 0.05, price, volume) for h, m in clock_minutes()]


def quiet_prefix(day: date, n: int = 30, price: float = 100.0) -> list[dict]:
    """First n bars of the session, tight enough that ATR stays small."""
    bars = []
    for i, (h, m) in enumerate(clock_minutes()[:n]):
        px = price + i * 0.01
        bars.append(bar(day, h, m, px, px + 0.1, px - 0.1, px, 1000))
    return bars
