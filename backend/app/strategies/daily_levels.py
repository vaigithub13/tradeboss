"""Daily levels built from the intraday bars a strategy has already seen (no look-ahead).

`DailyTracker.update(bar)` is called once per closed bar. When a new session starts, the finished day is
appended to `days` (open, high, low, close) and the levels of the new day are available:

* previous day high / low / close, today's open;
* CPR (central pivot range) from the previous day: P = (H + L + C) / 3, BC = (H + L) / 2, TC = 2P - BC;
* daily ATR (simple mean of the true range over `n` finished days);
* narrow-range checks on the previous day (NR4 / NR7 / inside day).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

IST_OFFSET_S = 19_800


def session_day(t: int) -> int:
    return (int(t) + IST_OFFSET_S) // 86_400


def minute_of_day(t: int) -> int:
    return ((int(t) + IST_OFFSET_S) % 86_400) // 60


@dataclass(frozen=True)
class Day:
    open: float
    high: float
    low: float
    close: float

    @property
    def range(self) -> float:
        return self.high - self.low


class DailyTracker:
    def __init__(self) -> None:
        self.days: list[Day] = []
        self.day: int | None = None
        self.today_open: float | None = None
        self._o = self._h = self._l = self._c = 0.0
        self.new_day = False

    def update(self, bar: dict[str, Any]) -> None:
        d = session_day(bar["time"])
        self.new_day = d != self.day
        if self.new_day:
            if self.day is not None:
                self.days.append(Day(self._o, self._h, self._l, self._c))
            self.day = d
            self._o, self._h, self._l = float(bar["open"]), float(bar["high"]), float(bar["low"])
            self.today_open = self._o
        else:
            self._h = max(self._h, float(bar["high"]))
            self._l = min(self._l, float(bar["low"]))
        self._c = float(bar["close"])

    # ---------------------------------------------------------------- previous day
    @property
    def prev(self) -> Day | None:
        return self.days[-1] if self.days else None

    def cpr(self, back: int = 1) -> tuple[float, float, float] | None:
        """(BC, P, TC) built from the day `back` sessions before today, with BC <= TC."""
        if len(self.days) < back:
            return None
        d = self.days[-back]
        p = (d.high + d.low + d.close) / 3.0
        bc = (d.high + d.low) / 2.0
        tc = 2.0 * p - bc
        lo, hi = min(bc, tc), max(bc, tc)
        return lo, p, hi

    def cpr_width(self, back: int = 1) -> float | None:
        c = self.cpr(back)
        return None if c is None else c[2] - c[0]

    def cpr_ratio(self, lookback: int = 20) -> float | None:
        """Today's CPR width over the mean width of the `lookback` CPRs before it."""
        if len(self.days) < lookback + 1:
            return None
        today = self.cpr_width(1)
        past = [self.cpr_width(k) for k in range(2, lookback + 2)]
        mean = sum(w for w in past if w is not None) / lookback
        return None if today is None or not mean > 0 else today / mean

    def atr(self, n: int = 14) -> float | None:
        if len(self.days) < n + 1:
            return None
        trs = []
        for i in range(len(self.days) - n, len(self.days)):
            d, prev_close = self.days[i], self.days[i - 1].close
            trs.append(max(d.high - d.low, abs(d.high - prev_close), abs(d.low - prev_close)))
        return sum(trs) / n

    def narrow_prev(self, kind: str) -> bool | None:
        """The previous day passes the filter: off / nr4 / nr7 / inside. None = not enough days."""
        if kind == "off":
            return True
        if kind == "inside":
            if len(self.days) < 2:
                return None
            a, b = self.days[-2], self.days[-1]
            return b.high <= a.high and b.low >= a.low
        n = {"nr4": 4, "nr7": 7}[kind]
        if len(self.days) < n:
            return None
        last = self.days[-n:]
        return last[-1].range <= min(d.range for d in last)
