"""5-minute bar helpers. `time` is unix seconds of the bar start (IST)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
BAR_S = 300
OPEN_MIN = 9 * 60 + 15
RANGE_END_MIN = 9 * 60 + 30
ENTRY_CUTOFF_MIN = 14 * 60 + 30
LAST_BAR_MIN = 15 * 60 + 10  # the 15:10 bar closes at the 15:15 flat

Bar = dict[str, Any]


def minute_of(ts: int) -> int:
    dt = datetime.fromtimestamp(int(ts), IST)
    return dt.hour * 60 + dt.minute


def day_of(ts: int) -> date:
    return datetime.fromtimestamp(int(ts), IST).date()


def entry_allowed(ts: int) -> bool:
    """A bar that starts at 14:30 or later is too late for a new entry."""
    return minute_of(ts) < ENTRY_CUTOFF_MIN


def closed_bars(bars: list[Bar], as_of: int | None) -> list[Bar]:
    if as_of is None:
        return list(bars)
    return [b for b in bars if int(b["time"]) + BAR_S <= int(as_of)]


def split_sessions(bars: list[Bar]) -> list[list[Bar]]:
    out: list[list[Bar]] = []
    cur: list[Bar] = []
    cur_day: date | None = None
    for b in bars:
        d = day_of(int(b["time"]))
        if cur_day is not None and d != cur_day:
            out.append(cur)
            cur = []
        cur.append(b)
        cur_day = d
    if cur:
        out.append(cur)
    return out


def session_for(bars: list[Bar], day: date) -> list[Bar]:
    return [b for b in bars if day_of(int(b["time"])) == day]


def wilder_atr(bars: list[Bar], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    if len(bars) <= n:
        return out
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(float(b["high"]) - float(b["low"]))
            continue
        prev = float(bars[i - 1]["close"])
        trs.append(
            max(
                float(b["high"]) - float(b["low"]),
                abs(float(b["high"]) - prev),
                abs(float(b["low"]) - prev),
            )
        )
    atr = sum(trs[1 : n + 1]) / n
    out[n] = atr
    for i in range(n + 1, len(bars)):
        atr = (atr * (n - 1) + trs[i]) / n
        out[i] = atr
    return out


def wilder_rsi(bars: list[Bar], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    if len(bars) <= n:
        return out
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(bars)):
        change = float(bars[i]["close"]) - float(bars[i - 1]["close"])
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n

    def _rsi(g: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / loss)

    out[n] = _rsi(avg_g, avg_l)
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        out[i + 1] = _rsi(avg_g, avg_l)
    return out


def session_vwap(bars: list[Bar]) -> list[float | None]:
    """Session VWAP from typical price. A zero-volume bar counts as volume 1 so the series stays defined."""
    out: list[float | None] = [None] * len(bars)
    cum_pv = 0.0
    cum_v = 0.0
    cur: date | None = None
    for i, b in enumerate(bars):
        d = day_of(int(b["time"]))
        if d != cur:
            cum_pv = 0.0
            cum_v = 0.0
            cur = d
        typical = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        vol = float(b["volume"])
        if vol <= 0:
            vol = 1.0
        cum_pv += typical * vol
        cum_v += vol
        out[i] = cum_pv / cum_v
    return out


def prior_avg_volume(bars: list[Bar], index: int, n: int = 20) -> float | None:
    if index < n:
        return None
    vols = [float(bars[j]["volume"]) for j in range(index - n, index)]
    return sum(vols) / n


def last_swing(bars: list[Bar], end: int, *, kind: str) -> float | None:
    """Most recent confirmed swing before `end` (the signal bar). Falls back to the prior 10 bars."""
    key = "low" if kind == "low" else "high"
    for i in range(end - 3, 1, -1):
        v = float(bars[i][key])
        left_a, left_b = float(bars[i - 1][key]), float(bars[i - 2][key])
        right_a, right_b = float(bars[i + 1][key]), float(bars[i + 2][key])
        if kind == "low" and v < left_a and v < left_b and v <= right_a and v <= right_b:
            return v
        if kind == "high" and v > left_a and v > left_b and v >= right_a and v >= right_b:
            return v
    window = bars[max(0, end - 10) : end]
    if not window:
        return None
    vals = [float(b[key]) for b in window]
    return min(vals) if kind == "low" else max(vals)


def nearest_level(bars: list[Bar], end: int, price: float, *, side: str) -> float | None:
    """Nearest swing high above `price` (long) or swing low below it (short)."""
    kind = "high" if side == "long" else "low"
    levels: list[float] = []
    for i in range(2, max(2, end - 2)):
        v = float(bars[i][kind])
        left_ok = (
            v > float(bars[i - 1][kind]) and v > float(bars[i - 2][kind])
            if kind == "high"
            else v < float(bars[i - 1][kind]) and v < float(bars[i - 2][kind])
        )
        right_ok = (
            v >= float(bars[i + 1][kind]) and v >= float(bars[i + 2][kind])
            if kind == "high"
            else v <= float(bars[i + 1][kind]) and v <= float(bars[i + 2][kind])
        )
        if not (left_ok and right_ok):
            continue
        if side == "long" and v > price:
            levels.append(v)
        elif side == "short" and v < price:
            levels.append(v)
    if not levels:
        return None
    return min(levels) if side == "long" else max(levels)


def index_of_time(bars: list[Bar], ts: int) -> int:
    for i, b in enumerate(bars):
        if int(b["time"]) == int(ts):
            return i
    raise KeyError(ts)


def forward_minutes(fill_minute: int, *, include_fill_bar: bool) -> list[int]:
    minute = fill_minute if include_fill_bar else fill_minute + 5
    out: list[int] = []
    while minute <= LAST_BAR_MIN:
        out.append(minute)
        minute += 5
    return out
