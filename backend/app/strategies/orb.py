"""Opening range breakout: once per day, trade the first break of the 09:15 + N minutes range.

When the range is complete the strategy places two stop-entry orders (above the high / below the
low) that cancel each other (OCO). Each carries a bracket stop at the other end of the range. The
engine's square-off flattens the position (default 15:15). One trade per day.
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy

OPEN_MIN = 9 * 60 + 15
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


class OpeningRangeBreakout(Strategy):
    name = "opening_range_breakout"

    def __init__(self, range_minutes: int = 15, lots: int = 1) -> None:
        super().__init__(range_minutes=range_minutes, lots=lots)
        if range_minutes < 1 or lots < 1:
            raise ValueError("range_minutes and lots must be >= 1")
        self.range_minutes, self.lots = range_minutes, lots
        self._day: int | None = None
        self._hi = float("-inf")
        self._lo = float("inf")
        self._placed = False

    def on_start(self, ctx: Any) -> None:
        tf = _TF_MIN.get(ctx.timeframe)
        if tf is None or tf > self.range_minutes or self.range_minutes % tf:
            raise ValueError(
                f"ORB with a {self.range_minutes}-minute range needs an intraday chart of 1/3/5/15 minutes "
                f"that divides it, got {ctx.timeframe}"
            )

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        t = bar["time"]
        day = (t + 19_800) // 86_400
        tod = ((t + 19_800) % 86_400) // 60
        if day != self._day:
            self._day, self._hi, self._lo, self._placed = day, float("-inf"), float("inf"), False
        if self._placed or not OPEN_MIN <= tod < OPEN_MIN + self.range_minutes:
            return []
        self._hi, self._lo = max(self._hi, bar["high"]), min(self._lo, bar["low"])
        tf = _TF_MIN[ctx.timeframe]
        if tod + tf != OPEN_MIN + self.range_minutes:
            return []  # the range is not complete yet
        self._placed = True
        label = f"orb{day}"
        return [
            Signal("BUY", self.lots, "SL", self._hi, tag="orb_long", stop=self._lo, oco=label),
            Signal("SELL", self.lots, "SL", self._lo, tag="orb_short", stop=self._hi, oco=label),
        ]
