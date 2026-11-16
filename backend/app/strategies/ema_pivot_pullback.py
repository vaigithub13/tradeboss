"""SpringPad EMA 20/50 pullback + previous-day pivot R1/S1 (intraday).

State machine (closed bars only):

1. Fast/slow EMA cross sets the trend only (does not enter).
2. Trend becomes *ready* after a close beyond both EMAs in the trend direction.
3. A pullback is a bar that touches the fast EMA while the previous close was still on the
   trend side of the fast EMA and this bar's close is still beyond the slow EMA.
4. Entry on a *later* confirming candle: close in the trade direction, beyond both EMAs, and
   close > previous-day R1 (long) / close < previous-day S1 (short). Classic floor pivots from
   `DailyTracker` (previous session H/L/C).
5. Stop = slow EMA (absolute at entry; re-armed to the current slow EMA every bar while in the
   trade). Target = `rr` × initial risk from the fill (`target_points`). Also EXIT on an
   opposite EMA cross.
6. Entries only between `first_entry` and `last_entry` (IST bar open), at most
   `max_trades_per_day`; the engine squares off at 15:15.

Backtest only — not registered as a paper slot.
"""

from __future__ import annotations

import math
from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.daily_levels import DailyTracker, minute_of_day
from app.strategies.dd_five_confirm import classic_pivots

MODES = ("long_short", "long_only", "short_only")
LONG_TAG = "emp_long"
SHORT_TAG = "emp_short"
DYN_STOP = "emp_dyn_stop"


def _hhmm(value: str) -> int:
    try:
        h, m = (int(x) for x in str(value).split(":"))
    except ValueError:
        raise ValueError(f"entry time must be HH:MM, got {value!r}") from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"entry time must be HH:MM, got {value!r}")
    return h * 60 + m


class EmaPivotPullback(Strategy):
    name = "ema_pivot_pullback"

    def __init__(
        self,
        fast: int = 20,
        slow: int = 50,
        rr: float = 3.0,
        mode: str = "long_short",
        max_trades_per_day: int = 4,
        first_entry: str = "09:20",
        last_entry: str = "14:30",
        use_pivots: bool = True,
        lots: int = 1,
    ) -> None:
        super().__init__(
            fast=fast, slow=slow, rr=rr, mode=mode, max_trades_per_day=max_trades_per_day,
            first_entry=first_entry, last_entry=last_entry, use_pivots=use_pivots, lots=lots,
        )
        if isinstance(fast, bool) or not isinstance(fast, int) or fast < 1:
            raise ValueError("fast must be a whole number >= 1")
        if isinstance(slow, bool) or not isinstance(slow, int) or not slow > fast:
            raise ValueError("slow must be a whole number > fast")
        if isinstance(rr, bool) or not isinstance(rr, (int, float)) or not rr > 0:
            raise ValueError("rr must be a number > 0")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if isinstance(max_trades_per_day, bool) or not isinstance(max_trades_per_day, int) or max_trades_per_day < 1:
            raise ValueError("max_trades_per_day must be a whole number >= 1")
        if isinstance(lots, bool) or not isinstance(lots, int) or lots < 1:
            raise ValueError("lots must be a whole number >= 1")
        if not isinstance(use_pivots, bool):
            raise ValueError("use_pivots must be true or false")
        self.fast, self.slow, self.rr = fast, slow, float(rr)
        self.mode, self.max_trades_per_day, self.lots = mode, max_trades_per_day, lots
        self.use_pivots = use_pivots
        self.first_entry_min = _hhmm(first_entry)
        self.last_entry_min = _hhmm(last_entry)
        if self.last_entry_min < self.first_entry_min:
            raise ValueError("last_entry must be >= first_entry")
        self.daily = DailyTracker()
        self._trend = 0  # +1 long, -1 short, 0 none
        self._ready = False
        self._pulled = False
        self._entries_today = 0
        self._entry_tag: str | None = None
        self._was_flat = True

    @staticmethod
    def pivots(high: float, low: float, close: float) -> tuple[float, float, float]:
        return classic_pivots(high, low, close)

    def on_start(self, ctx: Any) -> None:
        self.daily = DailyTracker()
        self._trend = 0
        self._ready = False
        self._pulled = False
        self._entries_today = 0
        self._entry_tag = None
        self._was_flat = True

    def _levels(self) -> tuple[float, float, float] | None:
        d = self.daily.prev
        return None if d is None else classic_pivots(d.high, d.low, d.close)

    def _in_window(self, bar: dict[str, Any]) -> bool:
        tod = minute_of_day(bar["time"])
        return self.first_entry_min <= tod <= self.last_entry_min

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        self.daily.update(bar)
        if self.daily.new_day:
            self._entries_today = 0
            # trend state carries; setups reset so a overnight pullback flag cannot fire at the open
            self._pulled = False

        if len(ctx.bars) <= self.slow:
            return []

        fast = ctx.indicator("ema", length=self.fast, source="close")["ema"]
        slow = ctx.indicator("ema", length=self.slow, source="close")["ema"]
        f_now, f_prev = float(fast[-1]), float(fast[-2])
        s_now, s_prev = float(slow[-1]), float(slow[-2])
        if any(math.isnan(v) for v in (f_now, f_prev, s_now, s_prev)):
            return []

        close = float(bar["close"])
        high, low = float(bar["high"]), float(bar["low"])
        prev_close = float(ctx.bars.close[-2])
        cross_up = f_prev <= s_prev and f_now > s_now
        cross_dn = f_prev >= s_prev and f_now < s_now

        pos = ctx.position
        out: list[Signal] = []

        # Track flat → in-trade so a fill consumes the pullback setup.
        if pos.is_flat:
            if not self._was_flat:
                self._entry_tag = None
                self._pulled = False
            self._was_flat = True
        else:
            self._was_flat = False

        # --- exits / manage open trade ---
        if pos.side > 0 and cross_dn:
            ctx.cancel_working(DYN_STOP)
            if self._entry_tag:
                ctx.cancel_working(f"{self._entry_tag}:stop")
                ctx.cancel_working(f"{self._entry_tag}:target")
            self._set_trend(-1)
            return [Signal("EXIT", pos.lots, tag="emp_cross_exit_long")]
        if pos.side < 0 and cross_up:
            ctx.cancel_working(DYN_STOP)
            if self._entry_tag:
                ctx.cancel_working(f"{self._entry_tag}:stop")
                ctx.cancel_working(f"{self._entry_tag}:target")
            self._set_trend(1)
            return [Signal("EXIT", pos.lots, tag="emp_cross_exit_short")]

        if cross_up:
            self._set_trend(1)
        elif cross_dn:
            self._set_trend(-1)

        if not pos.is_flat:
            # Dynamic stop: re-arm at the current slow EMA every bar.
            stop_px = s_now
            if pos.side > 0 and stop_px < close:
                ctx.cancel_working(DYN_STOP)
                if self._entry_tag:
                    ctx.cancel_working(f"{self._entry_tag}:stop")
                out.append(Signal("SELL", pos.lots, "SL", stop_px, tag=DYN_STOP))
            elif pos.side < 0 and stop_px > close:
                ctx.cancel_working(DYN_STOP)
                if self._entry_tag:
                    ctx.cancel_working(f"{self._entry_tag}:stop")
                out.append(Signal("BUY", pos.lots, "SL", stop_px, tag=DYN_STOP))
            return out

        # --- flat: update ready / pullback / maybe enter ---
        if self._trend == 1 and close > f_now and close > s_now:
            self._ready = True
        elif self._trend == -1 and close < f_now and close < s_now:
            self._ready = True

        if self._ready and self._trend == 1:
            # pullback touch of fast EMA
            if (prev_close > f_prev and low <= f_now <= high and close > s_now):
                self._pulled = True
            elif self._pulled and close > f_now and close > s_now:
                sig = self._try_enter(bar, ctx, side=1, close=close, slow_ema=s_now)
                if sig is not None:
                    out.append(sig)
        elif self._ready and self._trend == -1:
            if (prev_close < f_prev and low <= f_now <= high and close < s_now):
                self._pulled = True
            elif self._pulled and close < f_now and close < s_now:
                sig = self._try_enter(bar, ctx, side=-1, close=close, slow_ema=s_now)
                if sig is not None:
                    out.append(sig)
        return out

    def _set_trend(self, side: int) -> None:
        if self._trend == side and not (side == 0):
            # same-direction cross while already in that trend: keep ready if still valid; clear pullback
            self._pulled = False
            return
        self._trend = side
        self._ready = False
        self._pulled = False

    def _try_enter(self, bar: dict[str, Any], ctx: Any, *, side: int, close: float,
                   slow_ema: float) -> Signal | None:
        if side > 0 and self.mode == "short_only":
            return None
        if side < 0 and self.mode == "long_only":
            return None
        if not self._in_window(bar):
            return None
        if self._entries_today >= self.max_trades_per_day:
            return None
        if self.use_pivots:
            piv = self._levels()
            if piv is None:
                return None
            s1, _p, r1 = piv
            if side > 0 and not close > r1:
                return None
            if side < 0 and not close < s1:
                return None
        risk = abs(close - slow_ema)
        if not risk > 0:
            return None
        # Absolute stop at slow EMA; target distance from the eventual fill.
        if side > 0 and not slow_ema < close:
            return None
        if side < 0 and not slow_ema > close:
            return None
        tag = LONG_TAG if side > 0 else SHORT_TAG
        self._entries_today += 1
        self._pulled = False
        self._entry_tag = tag
        if side > 0:
            return Signal("BUY", self.lots, tag=tag, stop=slow_ema, target_points=self.rr * risk)
        return Signal("SELL", self.lots, tag=tag, stop=slow_ema, target_points=self.rr * risk)
