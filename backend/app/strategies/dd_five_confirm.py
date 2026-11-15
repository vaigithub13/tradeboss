"""DD Nifty "5 confirmation" option-buying system (Dharanidharan Ganesan, Upsurge Club video, Nov 2026).

All four confirmations on the same CLOSED bar, then enter on the next bar:

* long  (call): close above the Supertrend (uptrend), RSI > rsi_long, close > R1, close > upper Bollinger band;
* short (put):  close below the Supertrend (downtrend), RSI < rsi_short, close < S1, close < lower Bollinger band.

R1 / S1 are the classic (traditional) daily pivots of the PREVIOUS session (P = (H+L+C)/3, R1 = 2P - L,
S1 = 2P - H), built from bars the strategy has already seen (`DailyTracker`, no look-ahead).

Exit: no fixed target. The position is closed when the Supertrend direction flips against it (the Supertrend
line is the trailing stop), or by the engine's intraday square-off. One position at a time; a new entry needs
all four confirmations again. No new entries from `last_entry` (HH:MM, IST) on. `use_pivots=False` drops the
R1 / S1 confirmation (research only; the video's system keeps it on).

Quantman variant (the user's run 12524478): `use_wr=True` adds Williams %R(`wr_len`) > `wr_long` for a long and
< `wr_short` for a short; `max_trades_per_day` (0 = no limit) caps the entries of one session.
"""

from __future__ import annotations

import math
from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.daily_levels import DailyTracker, minute_of_day

MODES = ("long_short", "long_only", "short_only")


def classic_pivots(high: float, low: float, close: float) -> tuple[float, float, float]:
    """(S1, P, R1) of the classic / traditional floor pivots."""
    p = (high + low + close) / 3.0
    return 2.0 * p - high, p, 2.0 * p - low


def _hhmm(value: str) -> int:
    try:
        h, m = (int(x) for x in str(value).split(":"))
    except ValueError:
        raise ValueError(f"last_entry must be HH:MM, got {value!r}") from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"last_entry must be HH:MM, got {value!r}")
    return h * 60 + m


class DdFiveConfirm(Strategy):
    name = "dd_five_confirm"

    def __init__(self, st_atr: int = 10, st_mult: float = 2.0, rsi_len: int = 14, rsi_long: float = 70.0,
                 rsi_short: float = 30.0, bb_len: int = 20, bb_mult: float = 2.0, mode: str = "long_short",
                 last_entry: str = "14:50", use_pivots: bool = True, use_wr: bool = False, wr_len: int = 14,
                 wr_long: float = -30.0, wr_short: float = -70.0, max_trades_per_day: int = 0, lots: int = 1) -> None:
        super().__init__(st_atr=st_atr, st_mult=st_mult, rsi_len=rsi_len, rsi_long=rsi_long, rsi_short=rsi_short,
                         bb_len=bb_len, bb_mult=bb_mult, mode=mode, last_entry=last_entry, use_pivots=use_pivots,
                         use_wr=use_wr, wr_len=wr_len, wr_long=wr_long, wr_short=wr_short,
                         max_trades_per_day=max_trades_per_day, lots=lots)
        for key, value in (("st_atr", st_atr), ("rsi_len", rsi_len), ("bb_len", bb_len), ("wr_len", wr_len),
                           ("lots", lots)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        for key, value in (("st_mult", st_mult), ("bb_mult", bb_mult)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
                raise ValueError(f"{key} must be a number > 0")
        if not 0 <= rsi_short < rsi_long <= 100:
            raise ValueError("need 0 <= rsi_short < rsi_long <= 100")
        if not isinstance(use_pivots, bool) or not isinstance(use_wr, bool):
            raise ValueError("use_pivots / use_wr must be true or false")
        if not -100 <= wr_short <= wr_long <= 0:
            raise ValueError("need -100 <= wr_short <= wr_long <= 0")
        if isinstance(max_trades_per_day, bool) or not isinstance(max_trades_per_day, int) or max_trades_per_day < 0:
            raise ValueError("max_trades_per_day must be a whole number >= 0 (0 = no limit)")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.st_atr, self.st_mult, self.rsi_len = st_atr, float(st_mult), rsi_len
        self.rsi_long, self.rsi_short = float(rsi_long), float(rsi_short)
        self.bb_len, self.bb_mult, self.mode, self.lots = bb_len, float(bb_mult), mode, lots
        self.last_entry_min = _hhmm(last_entry)
        self.use_pivots = use_pivots
        self.use_wr, self.wr_len = use_wr, wr_len
        self.wr_long, self.wr_short = float(wr_long), float(wr_short)
        self.max_trades_per_day = max_trades_per_day
        self.daily = DailyTracker()
        self._entries_today = 0

    def on_start(self, ctx: Any) -> None:
        self.daily = DailyTracker()
        self._entries_today = 0

    def williams_r(self, ctx: Any) -> float:
        """Williams %R over the last `wr_len` closed bars (-100..0), NaN before enough bars or on a flat range."""
        if len(ctx.bars) < self.wr_len:
            return float("nan")
        hh = float(ctx.bars.high[-self.wr_len:].max())
        ll = float(ctx.bars.low[-self.wr_len:].min())
        if not hh > ll:
            return float("nan")
        return (hh - float(ctx.bars.close[-1])) / (hh - ll) * -100.0

    def levels(self) -> tuple[float, float, float] | None:
        """(S1, P, R1) of today, from the previous session; None before a full day has been seen."""
        d = self.daily.prev
        return None if d is None else classic_pivots(d.high, d.low, d.close)

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        self.daily.update(bar)
        if self.daily.new_day:
            self._entries_today = 0
        st = ctx.indicator("supertrend", atr_length=self.st_atr, multiplier=self.st_mult)
        direction, line = st.last("direction"), st.last("supertrend")
        if math.isnan(direction):
            return []
        up = direction < 0  # TradingView convention: direction -1 = uptrend
        pos = ctx.position

        # exit: the Supertrend has turned against the open position
        if pos.side > 0 and not up:
            return [Signal("EXIT", pos.lots, tag="dd_st_exit_long")]
        if pos.side < 0 and up:
            return [Signal("EXIT", pos.lots, tag="dd_st_exit_short")]
        if not pos.is_flat or minute_of_day(bar["time"]) >= self.last_entry_min:
            return []
        if self.max_trades_per_day and self._entries_today >= self.max_trades_per_day:
            return []

        piv = self.levels()
        rsi = ctx.indicator("rsi", length=self.rsi_len).last("rsi")
        bb = ctx.indicator("bb", length=self.bb_len, mult=self.bb_mult)
        upper, lower = bb.last("upper"), bb.last("lower")
        if (self.use_pivots and piv is None) or any(math.isnan(v) for v in (rsi, upper, lower, line)):
            return []
        s1, r1 = (piv[0], piv[2]) if (self.use_pivots and piv is not None) else (float("inf"), float("-inf"))
        close = float(bar["close"])
        wr = self.williams_r(ctx) if self.use_wr else float("nan")
        if self.use_wr and math.isnan(wr):
            return []
        if (self.mode != "short_only" and up and close > line and rsi > self.rsi_long
                and close > r1 and close > upper and (not self.use_wr or wr > self.wr_long)):
            self._entries_today += 1
            return [Signal("BUY", self.lots, tag="dd_long")]
        if (self.mode != "long_only" and not up and close < line and rsi < self.rsi_short
                and close < s1 and close < lower and (not self.use_wr or wr < self.wr_short)):
            self._entries_today += 1
            return [Signal("SELL", self.lots, tag="dd_short")]
        return []
