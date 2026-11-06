"""5 EMA (Power of Stocks, Subhashish Pani): trade the break of a candle that did not touch the 5 EMA.

The video's rules (youtube.com/watch?v=fBTMspiqjS4), run on a closed-bar basis:

* sell side (the video uses a 5-minute chart): an *alert candle* is a closed candle whose whole range,
  wicks included, is above the EMA (low > EMA). A stop-entry SELL waits at the alert candle's low, with the
  stop at its high and the target `rr` times that risk below the entry.
* buy side (the video uses a 15-minute chart): the mirror image. The whole candle is below the EMA
  (high < EMA); a stop-entry BUY waits at its high, the stop at its low.
* the entry order works for the next `entry_bars` candles only. When a newer candle is also an alert
  candle it replaces the order ("the next candle becomes the alert candle"). When the window passes with
  no break and no new alert candle, the setup is gone.
* at most `max_trades_per_day` entries a day (the video: 3-4). New entries only while the bar sits inside
  the project's entry window (09:15-14:50); the engine's square-off closes what is left.

The video trades sells on 5m and buys on 15m, so the faithful runs are `direction=short_only` on a 5m
chart and `direction=long_only` on a 15m chart. `both` runs the two sides on one chart (a variant).
The gap / range-shift position sizing of the "modified" video is not here: it is discretionary.
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.pine_common import entry_window, timeframe_minutes

DIRECTIONS = ("short_only", "long_only", "both")
SHORT_TAG = "5ema_short"
LONG_TAG = "5ema_long"


class FiveEma(Strategy):
    name = "five_ema"

    def __init__(self, ema_length: int = 5, direction: str = "short_only", rr: float = 3.0, entry_bars: int = 1,
                 max_trades_per_day: int = 4, lots: int = 1) -> None:
        super().__init__(ema_length=ema_length, direction=direction, rr=rr, entry_bars=entry_bars,
                         max_trades_per_day=max_trades_per_day, lots=lots)
        for key, value in (("ema_length", ema_length), ("entry_bars", entry_bars),
                           ("max_trades_per_day", max_trades_per_day), ("lots", lots)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        if isinstance(rr, bool) or not isinstance(rr, (int, float)) or not rr > 0:
            raise ValueError("rr must be a number > 0")
        self.ema_length, self.direction, self.rr = ema_length, direction, float(rr)
        self.entry_bars, self.max_trades, self.lots = entry_bars, max_trades_per_day, lots
        self._day: int | None = None
        self._trades = 0
        self._was_flat = True
        self._cash: float | None = None
        self._age = {SHORT_TAG: 0, LONG_TAG: 0}  # closed bars since that side's order was placed (0 = none)

    def on_start(self, ctx: Any) -> None:
        timeframe_minutes(ctx.timeframe)  # intraday charts only

    def _count_entries(self, ctx: Any) -> None:
        """An entry since the last bar: the position opened, or a round trip opened and closed inside the bar."""
        flat = ctx.position.is_flat
        cash = float(ctx.cash)
        if self._was_flat and not flat:
            self._trades += 1
        elif self._was_flat and flat and self._cash is not None and cash != self._cash:
            self._trades += 1
        self._was_flat, self._cash = flat, cash

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        t = int(bar["time"])
        day = (t + 19_800) // 86_400
        if day != self._day:
            self._day, self._trades = day, 0
            self._age = {SHORT_TAG: 0, LONG_TAG: 0}
            self._was_flat, self._cash = ctx.position.is_flat, float(ctx.cash)
        else:
            self._count_entries(ctx)

        if not ctx.position.is_flat:
            self._age = {SHORT_TAG: 0, LONG_TAG: 0}  # a filled entry's twin went with the OCO
            return []
        for tag in (SHORT_TAG, LONG_TAG):  # an order whose window has passed is dropped
            if self._age[tag]:
                self._age[tag] += 1
                if self._age[tag] > self.entry_bars:
                    ctx.cancel_working(tag)
                    self._age[tag] = 0

        if len(ctx.bars) < self.ema_length:
            return []
        ema = float(ctx.indicator("ema", length=self.ema_length, source="close")["ema"][-1])
        if ema != ema:  # NaN during warm-up
            return []
        if self._trades >= self.max_trades or not entry_window(t, timeframe_minutes(ctx.timeframe)):
            return []

        hi, lo = float(bar["high"]), float(bar["low"])
        risk = hi - lo
        if not risk > 0:
            return []
        label = f"5ema{day}"  # one entry fill cancels the other side's waiting order
        out: list[Signal] = []
        if self.direction != "long_only" and lo > ema:
            ctx.cancel_working(SHORT_TAG)
            out.append(Signal("SELL", self.lots, "SL", lo, tag=SHORT_TAG, stop=hi, target=lo - self.rr * risk,
                              oco=label))
            self._age[SHORT_TAG] = 1
        if self.direction != "short_only" and hi < ema:
            ctx.cancel_working(LONG_TAG)
            out.append(Signal("BUY", self.lots, "SL", hi, tag=LONG_TAG, stop=lo, target=hi + self.rr * risk,
                              oco=label))
            self._age[LONG_TAG] = 1
        return out
