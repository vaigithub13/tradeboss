"""Opening range breakout with a risk-reward target and day filters (candidate for options; min 1:2).

The opening range is 09:15 + `range_minutes`. When it completes, stop-entry orders wait above its high
(BUY) and below its low (SELL), as one OCO pair: one trade a day at most.

* stop: `stop_mode=opposite` puts it at the other side of the range; `mid` at the range midpoint.
* target: `rr` x risk from the entry (`rr=0`: no target, the square-off closes the trade).
* filters, all from finished days only:
  - `prior_day`: off / nr4 / nr7 / inside. Trade only after a narrow (compressed) previous day (Toby Crabel).
  - `cpr`: off / narrow. Trade only when today's CPR is narrow: its width is at most `cpr_max_ratio` of the
    mean width of the previous 20 CPRs ("narrow CPR = trending day").
  - `bias`: off / cpr. With `cpr`, an open above the CPR top allows only the long side, an open below its
    bottom only the short side; an open inside the CPR is no trade.
* an entry that has not triggered by 14:50 is cancelled; the engine's square-off closes what is left.
  - `range_atr_max`: skip the day when the opening range is wider than this multiple of the daily ATR(14)
    (0 = off): a very wide first range leaves too little room for a 1:2 move.
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.daily_levels import DailyTracker, minute_of_day

OPEN_MIN = 9 * 60 + 15
LAST_ENTRY_MIN = 14 * 60 + 50
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
STOP_MODES = ("opposite", "mid")
PRIOR_DAY = ("off", "nr4", "nr7", "inside")
CPR_FILTERS = ("off", "narrow")
BIASES = ("off", "cpr")


class OrbPlus(Strategy):
    name = "orb_plus"

    def __init__(self, range_minutes: int = 15, rr: float = 2.0, stop_mode: str = "opposite", prior_day: str = "off",
                 cpr: str = "off", cpr_max_ratio: float = 0.5, bias: str = "off", range_atr_max: float = 0.0,
                 lots: int = 1) -> None:
        super().__init__(range_minutes=range_minutes, rr=rr, stop_mode=stop_mode, prior_day=prior_day, cpr=cpr,
                         cpr_max_ratio=cpr_max_ratio, bias=bias, range_atr_max=range_atr_max, lots=lots)
        for key, value in (("range_minutes", range_minutes), ("lots", lots)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        for key, value, low in (("rr", rr, 0.0), ("cpr_max_ratio", cpr_max_ratio, 0.0),
                                ("range_atr_max", range_atr_max, 0.0)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < low:
                raise ValueError(f"{key} must be a number >= {low}")
        if 0 < rr < 1:
            raise ValueError("rr must be 0 (no target) or >= 1")
        if cpr == "narrow" and not cpr_max_ratio > 0:
            raise ValueError("cpr_max_ratio must be > 0 with cpr=narrow")
        for key, value, allowed in (("stop_mode", stop_mode, STOP_MODES), ("prior_day", prior_day, PRIOR_DAY),
                                    ("cpr", cpr, CPR_FILTERS), ("bias", bias, BIASES)):
            if value not in allowed:
                raise ValueError(f"{key} must be one of {allowed}")
        self.range_minutes, self.rr, self.stop_mode = range_minutes, float(rr), stop_mode
        self.prior_day, self.cpr, self.cpr_max_ratio, self.bias = prior_day, cpr, float(cpr_max_ratio), bias
        self.range_atr_max, self.lots = float(range_atr_max), lots
        self.daily = DailyTracker()
        self._hi = float("-inf")
        self._lo = float("inf")
        self._done = False
        self._expired = False

    def on_start(self, ctx: Any) -> None:
        self.daily = DailyTracker()
        self._hi, self._lo, self._done, self._expired = float("-inf"), float("inf"), False, False
        tf = _TF_MIN.get(ctx.timeframe)
        if tf is None or tf > self.range_minutes or self.range_minutes % tf:
            raise ValueError(
                f"orb_plus with a {self.range_minutes}-minute range needs an intraday chart of 1/3/5/15 minutes "
                f"that divides it, got {ctx.timeframe}"
            )

    def _sides(self) -> tuple[bool, bool] | None:
        """(long allowed, short allowed) for today, or None when a filter skips the day."""
        d = self.daily
        narrow = d.narrow_prev(self.prior_day)
        if not narrow:
            return None
        if self.cpr == "narrow":
            ratio = d.cpr_ratio()
            if ratio is None or ratio > self.cpr_max_ratio:
                return None
        if self.bias == "off":
            return True, True
        levels = d.cpr()
        if levels is None or d.today_open is None:
            return None
        bc, _p, tc = levels
        if d.today_open > tc:
            return True, False
        if d.today_open < bc:
            return False, True
        return None

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        self.daily.update(bar)
        if self.daily.new_day:
            self._hi, self._lo, self._done, self._expired = float("-inf"), float("inf"), False, False
        tod = minute_of_day(bar["time"])
        if self._done and not self._expired and tod >= LAST_ENTRY_MIN and ctx.position.is_flat:
            self._expired = True  # an entry not triggered by 14:50 is not taken
            ctx.cancel_working("orbp_long")
            ctx.cancel_working("orbp_short")
        if self._done or not OPEN_MIN <= tod < OPEN_MIN + self.range_minutes:
            return []
        self._hi, self._lo = max(self._hi, float(bar["high"])), min(self._lo, float(bar["low"]))
        if tod + _TF_MIN[ctx.timeframe] != OPEN_MIN + self.range_minutes:
            return []  # the range is not complete yet
        self._done = True
        width = self._hi - self._lo
        if not width > 0:
            return []
        if self.range_atr_max > 0:
            atr = self.daily.atr()
            if atr is None or width > self.range_atr_max * atr:
                return []
        sides = self._sides()
        if sides is None:
            return []
        long_ok, short_ok = sides
        mid = (self._hi + self._lo) / 2.0
        label = f"orbp{self.daily.day}"
        out: list[Signal] = []
        if long_ok:
            stop = self._lo if self.stop_mode == "opposite" else mid
            risk = self._hi - stop
            target = self._hi + self.rr * risk if self.rr > 0 else None
            out.append(Signal("BUY", self.lots, "SL", self._hi, tag="orbp_long", stop=stop, target=target, oco=label))
        if short_ok:
            stop = self._hi if self.stop_mode == "opposite" else mid
            risk = stop - self._lo
            target = self._lo - self.rr * risk if self.rr > 0 else None
            out.append(Signal("SELL", self.lots, "SL", self._lo, tag="orbp_short", stop=stop, target=target, oco=label))
        return out
