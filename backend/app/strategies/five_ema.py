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
The gap / range-shift position sizing of the "modified" video is not here: it is discretionary. Instead each
entry carries context tags after a "|" in its tag (`5ema_short|gap_zone,range_shift`), for the breakdowns to
measure; they change no order. All of them come from closed bars:

* `gap_zone`: the entry price is within `gap_buffer` points of an edge of an unfilled daily gap. A gap up is a
  day whose low is above the previous day's high (edges: that high and the day's low); a gap down the mirror.
  Today's gap is judged on today's closed bars so far. A gap is filled, and dropped, once a bar reaches its far
  edge (the previous day's high for a gap up, its low for a gap down).
* `range_shift`: the first signal of a side after the swing structure turned that way. A swing high (low) is a
  bar whose high (low) is strictly beyond the `swing_bars` bars on each side, known once the last of those bars
  has closed. A close above the latest swing high sets the bias up (a higher high), a close below the latest
  swing low sets it down; a break that turns the bias over is a range shift. The tag goes to the next alert
  candle of that side, and stays on alert candles that replace its waiting order; a lapsed order uses it up.
* `against_structure`: a sell while the bias is up, or a buy while it is down.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.pine_common import entry_window, timeframe_minutes

DIRECTIONS = ("short_only", "long_only", "both")
SHORT_TAG = "5ema_short"
LONG_TAG = "5ema_long"
DAY_S = 86_400
IST_OFFSET_S = 19_800


class DailyGaps:
    """Unfilled daily gaps, from closed bars only. `near(price, buffer)`: is the price that close to a gap edge?"""

    def __init__(self) -> None:
        self._day: int | None = None
        self._hi = self._lo = 0.0  # today so far
        self._prev: tuple[float, float] | None = None  # the previous day's (high, low)
        self._open: list[tuple[str, float, float]] = []  # (up | down, lower edge, upper edge) of earlier days

    def _today(self) -> tuple[str, float, float] | None:
        if self._prev is None:
            return None
        prev_hi, prev_lo = self._prev
        if self._lo > prev_hi:
            return ("up", prev_hi, self._lo)
        if self._hi < prev_lo:
            return ("down", self._hi, prev_lo)
        return None

    def update(self, bar: dict[str, Any]) -> None:
        day = (int(bar["time"]) + IST_OFFSET_S) // DAY_S
        hi, lo = float(bar["high"]), float(bar["low"])
        if day != self._day:
            if self._day is not None:
                gap = self._today()
                if gap is not None:
                    self._open.append(gap)
                self._prev = (self._hi, self._lo)
            self._day, self._hi, self._lo = day, hi, lo
        else:
            self._hi, self._lo = max(self._hi, hi), min(self._lo, lo)
        self._open = [g for g in self._open if not (lo <= g[1] if g[0] == "up" else hi >= g[2])]

    def near(self, price: float, buffer: float) -> bool:
        gaps = self._open + [g for g in (self._today(),) if g is not None]
        return any(abs(price - edge) <= buffer for _, lower, upper in gaps for edge in (lower, upper))


class SwingStructure:
    """Swing pivots confirmed `swing_bars` closed bars late, and the bias their breaks set (up / down / None).

    `update` returns "up" or "down" when this bar's close turned the bias over (a range shift), else None."""

    def __init__(self, swing_bars: int) -> None:
        self.n = swing_bars
        self._bars: deque[tuple[float, float]] = deque(maxlen=2 * swing_bars + 1)
        self.swing_high: float | None = None
        self.swing_low: float | None = None
        self.bias: str | None = None

    def update(self, bar: dict[str, Any]) -> str | None:
        self._bars.append((float(bar["high"]), float(bar["low"])))
        if len(self._bars) == self._bars.maxlen:
            highs = [b[0] for b in self._bars]
            lows = [b[1] for b in self._bars]
            mid_hi, mid_lo = highs[self.n], lows[self.n]
            others = range(len(highs))
            if all(mid_hi > highs[i] for i in others if i != self.n):
                self.swing_high = mid_hi
            if all(mid_lo < lows[i] for i in others if i != self.n):
                self.swing_low = mid_lo
        close = float(bar["close"])
        turn: str | None = None
        if self.swing_high is not None and close > self.swing_high:
            turn = "up"
            self.swing_high = None  # broken: wait for the next confirmed swing high
        elif self.swing_low is not None and close < self.swing_low:
            turn = "down"
            self.swing_low = None
        if turn is None:
            return None
        shifted = self.bias is not None and self.bias != turn
        self.bias = turn
        return turn if shifted else None


class FiveEma(Strategy):
    name = "five_ema"

    def __init__(self, ema_length: int = 5, direction: str = "short_only", rr: float = 3.0, entry_bars: int = 1,
                 max_trades_per_day: int = 4, lots: int = 1, gap_buffer: float = 30.0, swing_bars: int = 5) -> None:
        super().__init__(ema_length=ema_length, direction=direction, rr=rr, entry_bars=entry_bars,
                         max_trades_per_day=max_trades_per_day, lots=lots, gap_buffer=gap_buffer,
                         swing_bars=swing_bars)
        for key, value in (("ema_length", ema_length), ("entry_bars", entry_bars),
                           ("max_trades_per_day", max_trades_per_day), ("lots", lots), ("swing_bars", swing_bars)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        if isinstance(rr, bool) or not isinstance(rr, (int, float)) or not rr > 0:
            raise ValueError("rr must be a number > 0")
        if isinstance(gap_buffer, bool) or not isinstance(gap_buffer, (int, float)) or gap_buffer < 0:
            raise ValueError("gap_buffer must be a number >= 0")
        self.ema_length, self.direction, self.rr = ema_length, direction, float(rr)
        self.entry_bars, self.max_trades, self.lots = entry_bars, max_trades_per_day, lots
        self._day: int | None = None
        self._trades = 0
        self._was_flat = True
        self._cash: float | None = None
        self._age = {SHORT_TAG: 0, LONG_TAG: 0}  # closed bars since that side's order was placed (0 = none)
        self.gap_buffer = float(gap_buffer)
        self._gaps = DailyGaps()
        self._swings = SwingStructure(swing_bars)
        self._shift = {SHORT_TAG: False, LONG_TAG: False}  # a range shift waits for that side's next signal
        self._placed = {SHORT_TAG: SHORT_TAG, LONG_TAG: LONG_TAG}  # the full tag of that side's waiting order

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

    def _context(self, bar: dict[str, Any]) -> None:
        self._gaps.update(bar)
        turn = self._swings.update(bar)
        if turn is not None:
            self._shift = {SHORT_TAG: turn == "down", LONG_TAG: turn == "up"}

    def _tagged(self, side: str, entry: float, replacing: bool) -> str:
        tags = []
        if self._gaps.near(entry, self.gap_buffer):
            tags.append("gap_zone")
        if self._shift[side] or (replacing and "range_shift" in self._placed[side]):
            tags.append("range_shift")
        self._shift[side] = False
        if self._swings.bias == ("up" if side == SHORT_TAG else "down"):
            tags.append("against_structure")
        self._placed[side] = f"{side}|{','.join(tags)}" if tags else side
        return self._placed[side]

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        t = int(bar["time"])
        self._context(bar)
        day = (t + IST_OFFSET_S) // DAY_S
        entries = self._trades
        if day != self._day:
            self._day, self._trades = day, 0
            self._age = {SHORT_TAG: 0, LONG_TAG: 0}
            self._was_flat, self._cash = ctx.position.is_flat, float(ctx.cash)
        else:
            self._count_entries(ctx)

        if not ctx.position.is_flat or self._trades > entries:
            self._age = {SHORT_TAG: 0, LONG_TAG: 0}  # a filled entry's twin went with the OCO
            if not ctx.position.is_flat:
                return []
        waiting = {tag: self._age[tag] > 0 for tag in (SHORT_TAG, LONG_TAG)}  # worked through this bar, unfilled
        for tag in (SHORT_TAG, LONG_TAG):  # an order whose window has passed is dropped
            if self._age[tag]:
                self._age[tag] += 1
                if self._age[tag] > self.entry_bars:
                    ctx.cancel_working(self._placed[tag])
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
            replacing = waiting[SHORT_TAG]
            ctx.cancel_working(self._placed[SHORT_TAG])
            out.append(Signal("SELL", self.lots, "SL", lo, tag=self._tagged(SHORT_TAG, lo, replacing), stop=hi,
                              target=lo - self.rr * risk, oco=label))
            self._age[SHORT_TAG] = 1
        if self.direction != "short_only" and hi < ema:
            replacing = waiting[LONG_TAG]
            ctx.cancel_working(self._placed[LONG_TAG])
            out.append(Signal("BUY", self.lots, "SL", hi, tag=self._tagged(LONG_TAG, hi, replacing), stop=lo,
                              target=hi + self.rr * risk, oco=label))
            self._age[LONG_TAG] = 1
        return out
