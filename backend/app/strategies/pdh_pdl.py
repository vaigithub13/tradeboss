"""Previous-day high / low breakout with an ATR stop and a risk-reward target (candidate for options).

Once the first `wait_minutes` of the session have closed, stop-entry orders wait at the previous day's high
(BUY) and low (SELL) as one OCO pair: one trade a day at most. A side the open has already gapped through
is not traded (the move happened before the order could work).

* stop: `stop_atr` x the daily ATR(`atr_length`, default 14) from the entry (e.g. 0.2 x 200 = 40 points);
* target: `rr` x that risk from the entry;
* an entry not triggered by 14:50 is cancelled; the engine's square-off closes what is left.
* `bias=cpr`: an open above the CPR top allows only the long side, below its bottom only the short side,
  inside it neither.
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.daily_levels import DailyTracker, minute_of_day

OPEN_MIN = 9 * 60 + 15
LAST_ENTRY_MIN = 14 * 60 + 50
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
BIASES = ("off", "cpr")


class PdhPdlBreakout(Strategy):
    name = "pdh_pdl_breakout"

    def __init__(self, wait_minutes: int = 15, rr: float = 2.0, stop_atr: float = 0.2, bias: str = "off",
                 atr_length: int = 14, lots: int = 1) -> None:
        super().__init__(wait_minutes=wait_minutes, rr=rr, stop_atr=stop_atr, bias=bias, atr_length=atr_length, lots=lots)
        for key, value in (("wait_minutes", wait_minutes), ("atr_length", atr_length), ("lots", lots)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        for key, value in (("rr", rr), ("stop_atr", stop_atr)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
                raise ValueError(f"{key} must be a number > 0")
        if bias not in BIASES:
            raise ValueError(f"bias must be one of {BIASES}")
        self.wait_minutes, self.rr, self.stop_atr, self.bias, self.lots = wait_minutes, float(rr), float(stop_atr), bias, lots
        self.atr_length = atr_length
        self.daily = DailyTracker()
        self._placed = False
        self._expired = False

    def on_start(self, ctx: Any) -> None:
        self.daily = DailyTracker()
        self._placed, self._expired = False, False
        tf = _TF_MIN.get(ctx.timeframe)
        if tf is None or tf > self.wait_minutes or self.wait_minutes % tf:
            raise ValueError(f"pdh_pdl_breakout needs an intraday chart that divides {self.wait_minutes} minutes")

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        d = self.daily
        d.update(bar)
        if d.new_day:
            self._placed, self._expired = False, False
        tod = minute_of_day(bar["time"])
        if self._placed and not self._expired and tod >= LAST_ENTRY_MIN and ctx.position.is_flat:
            self._expired = True
            ctx.cancel_working("pdh_long")
            ctx.cancel_working("pdl_short")
        if self._placed or tod + _TF_MIN[ctx.timeframe] != OPEN_MIN + self.wait_minutes:
            return []
        self._placed = True
        prev, atr, opened = d.prev, d.atr(self.atr_length), d.today_open
        if prev is None or atr is None or opened is None:
            return []
        long_ok = opened < prev.high
        short_ok = opened > prev.low
        if self.bias == "cpr":
            levels = d.cpr()
            if levels is None:
                return []
            bc, _p, tc = levels
            long_ok = long_ok and opened > tc
            short_ok = short_ok and opened < bc
        close = float(bar["close"])
        long_ok = long_ok and close < prev.high  # already through the level by now: no stop-entry above
        short_ok = short_ok and close > prev.low
        risk = self.stop_atr * atr
        label = f"pdhl{d.day}"
        out: list[Signal] = []
        if long_ok:
            entry = prev.high
            out.append(Signal("BUY", self.lots, "SL", entry, tag="pdh_long", stop=entry - risk,
                              target=entry + self.rr * risk, oco=label))
        if short_ok:
            entry = prev.low
            out.append(Signal("SELL", self.lots, "SL", entry, tag="pdl_short", stop=entry + risk,
                              target=entry - self.rr * risk, oco=label))
        return out
