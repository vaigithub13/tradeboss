"""Price-action pattern + First Hour Box option strategies (research only; no paper slot).

- Patterns: enter on confirmation bar (fill next bar + live 1m lag). SL = invalidation,
  target 2R. One trade per signal; max 1 open position; engine square-off 15:15.
- Confluence (optional): within `level_pts` of PP/R1/S1/R2/S2, inside Fib 38.2–61.8 of the
  last confirmed swing (pivot 5/5), or within `level_pts` of a locked First Hour Box edge.
- Box break (variant=box_break): 30m close outside → next 5m still outside + VWAP_fut +
  SuperTrend agree; SL = midpoint of the 30m breakout candle; target 2R; window 10:15–13:30;
  skip width > max_width.
- Box fail (variant=box_fail): FAILED marker → trade toward opposite edge; SL beyond probe
  extreme.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.daily_levels import DailyTracker, minute_of_day
from app.strategies.first_hour_box import (
    DEFAULT_MAX_WIDTH,
    FhbCandle,
    compute_first_hour_box,
    ist_minute_of_day,
    thirty_open_unix,
)
from app.strategies.price_action_patterns import (
    OHLC,
    PATTERN_KINDS,
    PatternCandidate,
    atr_wilder,
    avg_body,
    classic_pivots_full,
    confirmed,
    confirmed_swing,
    detect_at,
    fib_zone,
    invalidated,
)

VARIANTS = ("pattern", "box_break", "box_fail")
MODES = ("long_short", "long_only", "short_only")


def _hhmm(value: str) -> int:
    try:
        h, m = (int(x) for x in str(value).split(":"))
    except ValueError:
        raise ValueError(f"time must be HH:MM, got {value!r}") from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"time must be HH:MM, got {value!r}")
    return h * 60 + m


@dataclass
class _Pending:
    cand: PatternCandidate
    formed_i: int  # index in strategy's running bar list


@dataclass
class _BoxBreakPending:
    side: str  # above | below
    box_high: float
    box_low: float
    break_mid: float  # midpoint of the 30m breakout candle
    break_30_open: int
    waiting_5m: bool


class PriceAction(Strategy):
    name = "price_action"

    def __init__(
        self,
        pattern: str = "tweezer_bottom",
        variant: str = "pattern",
        confluence: bool = False,
        rr: float = 2.0,
        confirm_bars: int = 3,
        atr_len: int = 14,
        avg_body_len: int = 10,
        swing_left: int = 5,
        swing_right: int = 5,
        level_pts: float = 10.0,
        max_width: float = DEFAULT_MAX_WIDTH,
        level_atr: float | None = None,
        max_width_atr: float | None = None,
        level_pct: float | None = None,
        max_width_pct: float | None = None,
        mode: str = "long_short",
        first_entry: str = "09:20",
        last_entry: str = "14:50",
        box_first: str = "10:15",
        box_last: str = "13:30",
        lots: int = 1,
        st_atr: int = 10,
        st_mult: float = 2.0,
    ) -> None:
        super().__init__(
            pattern=pattern, variant=variant, confluence=confluence, rr=rr,
            confirm_bars=confirm_bars, atr_len=atr_len, avg_body_len=avg_body_len,
            swing_left=swing_left, swing_right=swing_right, level_pts=level_pts,
            max_width=max_width, level_atr=level_atr, max_width_atr=max_width_atr,
            level_pct=level_pct, max_width_pct=max_width_pct,
            mode=mode, first_entry=first_entry, last_entry=last_entry,
            box_first=box_first, box_last=box_last, lots=lots, st_atr=st_atr, st_mult=st_mult,
        )
        if variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}")
        if variant == "pattern" and pattern not in PATTERN_KINDS:
            raise ValueError(f"pattern must be one of {PATTERN_KINDS}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if isinstance(rr, bool) or not isinstance(rr, (int, float)) or not rr > 0:
            raise ValueError("rr must be a number > 0")
        if isinstance(confirm_bars, bool) or not isinstance(confirm_bars, int) or confirm_bars < 1:
            raise ValueError("confirm_bars must be a whole number >= 1")
        if isinstance(lots, bool) or not isinstance(lots, int) or lots < 1:
            raise ValueError("lots must be a whole number >= 1")
        if not isinstance(confluence, bool):
            raise ValueError("confluence must be true or false")
        self.pattern = pattern
        self.variant = variant
        self.confluence = confluence
        self.rr = float(rr)
        self.confirm_bars = confirm_bars
        self.atr_len = atr_len
        self.avg_body_len = avg_body_len
        self.swing_left = swing_left
        self.swing_right = swing_right
        self.level_pts = float(level_pts)
        self.max_width = float(max_width)
        self.level_atr = None if level_atr is None else float(level_atr)
        self.max_width_atr = None if max_width_atr is None else float(max_width_atr)
        self.level_pct = None if level_pct is None else float(level_pct)
        self.max_width_pct = None if max_width_pct is None else float(max_width_pct)
        self.mode = mode
        self.lots = lots
        self.st_atr = st_atr
        self.st_mult = float(st_mult)
        self.first_entry_min = _hhmm(first_entry)
        self.last_entry_min = _hhmm(last_entry)
        self.box_first_min = _hhmm(box_first)
        self.box_last_min = _hhmm(box_last)
        self.daily = DailyTracker()
        self._ohlc: list[OHLC] = []
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._times: list[int] = []
        self._pending: list[_Pending] = []
        self._last_swing_hi: float | None = None
        self._last_swing_lo: float | None = None
        self._fhb_candles: list[FhbCandle] = []
        self._box_break: _BoxBreakPending | None = None
        self._last_failed: dict[str, Any] | None = None
        self._seen_marker_keys: set[tuple[int, str]] = set()
        self._ema20: float | None = None
        self._ema20_prev: float | None = None
        self._ema20_seed: list[float] = []
        self._locked_box: Any = None
        self._locked_box_day: str | None = None
        # Equal-weight session VWAP (HLC3) — index tape has no volume / fut_volume in the BT frame.
        self._vwap_day: int | None = None
        self._vwap_num: float = 0.0
        self._vwap_den: float = 0.0
        self._vwap: float | None = None

    def on_start(self, ctx: Any) -> None:
        self.daily = DailyTracker()
        self._ohlc = []
        self._closes = []
        self._highs = []
        self._lows = []
        self._times = []
        self._pending = []
        self._last_swing_hi = None
        self._last_swing_lo = None
        self._fhb_candles = []
        self._box_break = None
        self._last_failed = None
        self._seen_marker_keys = set()
        self._ema20 = None
        self._ema20_prev = None
        self._ema20_seed = []
        self._locked_box = None
        self._locked_box_day = None
        self._vwap_day = None
        self._vwap_num = 0.0
        self._vwap_den = 0.0
        self._vwap = None

    def _in_entry_window(self, bar: dict[str, Any]) -> bool:
        tod = minute_of_day(bar["time"])
        if self.variant.startswith("box"):
            return self.box_first_min <= tod <= self.box_last_min
        return self.first_entry_min <= tod <= self.last_entry_min

    def _mode_ok(self, side: str) -> bool:
        if self.mode == "long_only" and side != "long":
            return False
        if self.mode == "short_only" and side != "short":
            return False
        return True

    def _level_tol(self, price: float, atr: float | None = None) -> float:
        """Points by default (Nifty); optional ATR multiple or % of price for other instruments."""
        if self.level_atr is not None and atr is not None and atr > 0:
            return self.level_atr * atr
        if self.level_pct is not None and price > 0:
            return price * self.level_pct / 100.0
        return self.level_pts

    def _width_cap(self, price: float, atr: float | None = None) -> float:
        if self.max_width_atr is not None and atr is not None and atr > 0:
            return self.max_width_atr * atr
        if self.max_width_pct is not None and price > 0:
            return price * self.max_width_pct / 100.0
        return self.max_width

    def _near_levels(self, price: float, atr: float | None = None) -> bool:
        d = self.daily.prev
        if d is None:
            return False
        tol = self._level_tol(price, atr)
        piv = classic_pivots_full(d.high, d.low, d.close)
        if any(abs(price - v) <= tol for v in piv.values()):
            return True
        if self._last_swing_hi is not None and self._last_swing_lo is not None:
            lo, hi = fib_zone(self._last_swing_lo, self._last_swing_hi)
            if lo - tol <= price <= hi + tol:
                return True
        box = self._latest_locked_box()
        if box is not None and not box.tooWide:
            if abs(price - box.high) <= tol or abs(price - box.low) <= tol:
                return True
        return False

    def _update_ema20(self, close: float) -> None:
        if self._ema20 is None:
            self._ema20_seed.append(close)
            if len(self._ema20_seed) == 20:
                self._ema20 = sum(self._ema20_seed) / 20.0
                self._ema20_prev = self._ema20
            return
        self._ema20_prev = self._ema20
        self._ema20 = close * (2.0 / 21.0) + self._ema20 * (19.0 / 21.0)

    def _refresh_locked_box(self, t: int) -> None:
        """Recompute today's locked box at most once after 10:15 (and on day change)."""
        from app.strategies.first_hour_box import ist_day_key, ist_minute_of_day

        day = ist_day_key(t)
        minute = ist_minute_of_day(t)
        if day != self._locked_box_day:
            self._locked_box = None
            self._locked_box_day = day
            self._seen_marker_keys.clear()
        if minute < 10 * 60 + 15:
            return
        if self._locked_box is not None and not self._locked_box.forming:
            return
        # Only need recent candles (today + yesterday buffer)
        recent = self._fhb_candles[-200:] if len(self._fhb_candles) > 200 else self._fhb_candles
        ref = float(recent[-1].close) if recent else 0.0
        atr = None
        if self._ohlc:
            atr = atr_wilder(self._ohlc, len(self._ohlc) - 1, self.atr_len)
        res = compute_first_hour_box(recent, as_of=t, max_width=self._width_cap(ref, atr))
        for b in reversed(res.boxes):
            if b.day == day and not b.forming:
                self._locked_box = b
                return
            if not b.forming and self._locked_box is None:
                self._locked_box = b

    def _latest_locked_box(self):
        return self._locked_box

    def _entry_signal(self, side: str, stop: float, ref: float, tag: str) -> Signal | None:
        risk = abs(ref - stop)
        if risk <= 0:
            return None
        if side == "long":
            target = ref + self.rr * risk
            return Signal("BUY", self.lots, tag=tag, stop=stop, target=target)
        target = ref - self.rr * risk
        return Signal("SELL", self.lots, tag=tag, stop=stop, target=target)

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        self.daily.update(bar)
        o = OHLC(float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
        t = int(bar["time"])
        self._ohlc.append(o)
        self._closes.append(o.close)
        self._highs.append(o.high)
        self._lows.append(o.low)
        self._times.append(t)
        self._fhb_candles.append(FhbCandle(t, o.open, o.high, o.low, o.close))
        self._update_ema20(o.close)
        # Session VWAP proxy (equal weight) — resets each IST day
        day_i = (t + 19_800) // 86_400
        if day_i != self._vwap_day:
            self._vwap_day = day_i
            self._vwap_num = 0.0
            self._vwap_den = 0.0
        typical = (o.high + o.low + o.close) / 3.0
        self._vwap_num += typical
        self._vwap_den += 1.0
        self._vwap = self._vwap_num / self._vwap_den
        i = len(self._ohlc) - 1

        # Trim candle buffers to keep swing/ATR windows + one session (avoid unbounded growth)
        if len(self._ohlc) > 5_000:
            keep = 2_000
            drop = len(self._ohlc) - keep
            self._ohlc = self._ohlc[drop:]
            self._closes = self._closes[drop:]
            self._highs = self._highs[drop:]
            self._lows = self._lows[drop:]
            self._times = self._times[drop:]
            self._fhb_candles = self._fhb_candles[drop:]
            for p in self._pending:
                p.formed_i -= drop
            i = len(self._ohlc) - 1

        ph, pl = confirmed_swing(self._highs, self._lows, i, self.swing_left, self.swing_right)
        if ph is not None:
            self._last_swing_hi = ph
        if pl is not None:
            self._last_swing_lo = pl

        if self.confluence or self.variant.startswith("box"):
            self._refresh_locked_box(t)

        pos = ctx.position
        if not pos.is_flat:
            self._pending.clear()
            self._box_break = None
            return []

        if self.variant == "box_break":
            return self._on_box_break(bar, ctx, o, t, i)
        if self.variant == "box_fail":
            return self._on_box_fail(bar, ctx, o, t)

        return self._on_pattern(bar, ctx, o, i)

    def _on_pattern(self, bar: dict[str, Any], ctx: Any, o: OHLC, i: int) -> list[Signal]:
        atr = atr_wilder(self._ohlc, i, self.atr_len)
        avg_b = avg_body(self._ohlc, i, self.avg_body_len)
        if atr is None or avg_b is None:
            return []

        # advance / drop pending
        still: list[_Pending] = []
        for p in self._pending:
            since = i - p.formed_i
            if invalidated(p.cand, o) or since > self.confirm_bars:
                continue
            if confirmed(p.cand, o, bars_since=since, confirm_bars=self.confirm_bars):
                if not self._mode_ok(p.cand.side):
                    continue
                if not self._in_entry_window(bar):
                    continue
                if self.confluence and not self._near_levels(o.close, atr):
                    continue
                sig = self._entry_signal(
                    p.cand.side,
                    p.cand.invalid_level,
                    o.close,
                    f"pa_{p.cand.kind}_{p.cand.side}",
                )
                if sig is not None:
                    self._pending.clear()
                    return [sig]
                continue
            still.append(p)
        self._pending = still

        ema_list: list[float | None] = [None] * (i + 1)
        if self._ema20 is not None and self._ema20_prev is not None:
            ema_list[i] = self._ema20
            ema_list[i - 1] = self._ema20_prev

        found = detect_at(
            self._ohlc,
            i,
            atr=atr,
            avg_b=avg_b,
            closes=self._closes,
            ema20=ema_list,
            kinds=(self.pattern,),
        )
        for cand in found:
            if not self._mode_ok(cand.side):
                continue
            self._pending.append(_Pending(cand=cand, formed_i=i))
        return []

    def _st_vwap_agree(self, ctx: Any, side: str) -> bool:
        """SuperTrend + session VWAP agree with the break direction.

        Tries `vwap_fut` then volume VWAP; Nifty index bars have no volume in the
        backtest frame, so falls back to the equal-weight HLC3 session VWAP maintained
        in `on_bar` (research proxy for the chart futures VWAP).
        """
        try:
            st = ctx.indicator("supertrend", atr_length=self.st_atr, multiplier=self.st_mult)
            direction = st.last("direction")
        except Exception:
            return False
        if direction != direction:  # NaN
            return False
        # direction: -1 uptrend, +1 downtrend (Pine)
        if side == "above" and not (direction < 0):
            return False
        if side == "below" and not (direction > 0):
            return False
        vwap = float("nan")
        try:
            vwap = float(ctx.indicator("vwap_fut").last("vwap"))
        except Exception:
            try:
                vwap = float(ctx.indicator("vwap").last("vwap"))
            except Exception:
                vwap = float("nan")
        if vwap != vwap:
            if self._vwap is None:
                return False
            vwap = self._vwap
        close = self._closes[-1]
        if side == "above":
            return close > vwap
        return close < vwap

    def _on_box_break(self, bar: dict[str, Any], ctx: Any, o: OHLC, t: int, i: int) -> list[Signal]:
        if not self._in_entry_window(bar):
            self._box_break = None
            return []

        # Refresh markers for today's box (cheap: only recent candles)
        recent = self._fhb_candles[-120:]
        atr = atr_wilder(self._ohlc, i, self.atr_len) if self._ohlc else None
        res = compute_first_hour_box(recent, as_of=t, max_width=self._width_cap(o.close, atr))
        box = next((b for b in reversed(res.boxes) if not b.forming), None)
        if box is None or box.tooWide:
            self._box_break = None
            return []
        self._locked_box = box

        for m in box.markers:
            key = (m.time, m.kind)
            if key in self._seen_marker_keys:
                continue
            self._seen_marker_keys.add(key)
            if m.kind != "BREAK" or m.side is None:
                continue
            bucket = [c for c in recent if thirty_open_unix(c.time) == m.time]
            if not bucket:
                continue
            hi = max(c.high for c in bucket)
            lo = min(c.low for c in bucket)
            mid = (hi + lo) / 2.0
            self._box_break = _BoxBreakPending(
                side=m.side,
                box_high=box.high,
                box_low=box.low,
                break_mid=mid,
                break_30_open=m.time,
                waiting_5m=True,
            )

        pending = self._box_break
        if pending is None or not pending.waiting_5m:
            return []

        # next 5m close still outside
        still_out = (
            o.close > pending.box_high if pending.side == "above" else o.close < pending.box_low
        )
        # only after the 30m bar that broke has fully closed
        if t < pending.break_30_open + 30 * 60:
            return []
        if not still_out:
            self._box_break = None
            return []
        if not self._st_vwap_agree(ctx, pending.side):
            return []

        side = "long" if pending.side == "above" else "short"
        if not self._mode_ok(side):
            return []
        stop = pending.break_mid
        sig = self._entry_signal(side, stop, o.close, f"pa_box_break_{pending.side}")
        self._box_break = None
        return [sig] if sig is not None else []

    def _on_box_fail(self, bar: dict[str, Any], ctx: Any, o: OHLC, t: int) -> list[Signal]:
        if not self._in_entry_window(bar):
            return []
        recent = self._fhb_candles[-120:]
        atr = atr_wilder(self._ohlc, len(self._ohlc) - 1, self.atr_len) if self._ohlc else None
        res = compute_first_hour_box(recent, as_of=t, max_width=self._width_cap(o.close, atr))
        box = next((b for b in reversed(res.boxes) if not b.forming), None)
        if box is None or box.tooWide:
            return []
        self._locked_box = box

        for m in box.markers:
            key = (m.time, m.kind)
            if key in self._seen_marker_keys:
                continue
            self._seen_marker_keys.add(key)
            if m.kind != "FAILED" or m.side is None:
                continue
            # FAILED side = direction of the failed move; trade toward opposite edge
            # probe extreme: for failed above → SL beyond high of probe; trade short toward low
            if m.side == "above":
                # failed upside probe → short toward box low; SL above probe extreme (box high + cushion via pattern)
                trade_side = "short"
                stop = max(box.high, o.high)  # beyond probe extreme
                target_ref = o.close
            else:
                trade_side = "long"
                stop = min(box.low, o.low)
                target_ref = o.close
            if not self._mode_ok(trade_side):
                continue
            sig = self._entry_signal(trade_side, stop, target_ref, f"pa_box_fail_{m.side}")
            if sig is not None:
                return [sig]
        return []
