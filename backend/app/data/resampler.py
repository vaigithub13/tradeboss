"""Candle resampler (critical module). Spec: tests/test_resampler.py.

All calendar logic is IST (UTC+5:30). Candle.time is unix seconds of the bar START.
Pure function, no I/O, never fabricates bars: a candle is emitted only for a
bucket that contains at least one source bar.
"""

from __future__ import annotations

from collections.abc import Iterable, Set
from datetime import date

from app.data.candle import Candle

TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W")

_INTRADAY_MINUTES: dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}

IST_OFFSET_S = 19_800
SESSION_OPEN_MIN = 9 * 60 + 15  # 09:15
SESSION_CLOSE_MIN = 15 * 60 + 30  # 15:30 (exclusive)

DAY_S = 86_400
_UNIX_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
# 1970-01-01 was a Thursday, so with Monday=0: weekday(day_number) = (day_number + 3) % 7
# and the Monday-based week number is (day_number + 3) // 7.


def available_timeframes(source_minutes: int) -> tuple[str, ...]:
    """Timeframes that can be built from bars of `source_minutes` (no fabricated data)."""
    return tuple(
        tf
        for tf in TIMEFRAMES
        if tf not in _INTRADAY_MINUTES or _INTRADAY_MINUTES[tf] % source_minutes == 0
    )


def resample(
    candles: Iterable[Candle],
    timeframe: str,
    source_minutes: int = 1,
    *,
    anchor_to_first_bar_dates: Set[date] = frozenset(),
) -> list[Candle]:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {TIMEFRAMES}")
    if source_minutes < 1:
        raise ValueError("source_minutes must be >= 1")
    n = _INTRADAY_MINUTES.get(timeframe)
    if n is not None and n % source_minutes != 0:
        raise ValueError(
            f"Cannot build {timeframe} from {source_minutes}m bars "
            f"(needs a multiple of {source_minutes}m)"
        )

    anchored_days = {d.toordinal() - _UNIX_EPOCH_ORDINAL for d in anchor_to_first_bar_dates}
    first_minute_of_day: dict[int, int] = {}

    out: list[Candle] = []
    cur_key: tuple[int, int] | int | None = None
    cur: Candle | None = None

    for c in sorted(candles, key=lambda x: x["time"]):
        day, sec_of_day = divmod(c["time"] + IST_OFFSET_S, DAY_S)
        minute = sec_of_day // 60
        anchored = day in anchored_days
        if not anchored and not (SESSION_OPEN_MIN <= minute < SESSION_CLOSE_MIN):
            continue  # pre-open / post-close print

        key: tuple[int, int] | int
        if timeframe == "1D":
            key = day
            start_time = c["time"]
        elif timeframe == "1W":
            key = (day + 3) // 7
            start_time = c["time"]
        else:
            assert n is not None
            anchor = first_minute_of_day.setdefault(day, minute) if anchored else SESSION_OPEN_MIN
            k = (minute - anchor) // n
            key = (day, k)
            start_time = day * DAY_S - IST_OFFSET_S + (anchor + k * n) * 60

        if key != cur_key or cur is None:
            if cur is not None:
                out.append(cur)
            cur_key = key
            cur = {
                "time": start_time,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
                "oi": c["oi"],
            }
        else:
            if c["high"] > cur["high"]:
                cur["high"] = c["high"]
            if c["low"] < cur["low"]:
                cur["low"] = c["low"]
            cur["close"] = c["close"]
            cur["volume"] += c["volume"]
            cur["oi"] = c["oi"]

    if cur is not None:
        out.append(cur)
    return out
