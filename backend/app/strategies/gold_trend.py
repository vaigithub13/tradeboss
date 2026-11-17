"""GOLD TREND research ports (GT9 / GT2 / GT14) from AurumBoss goldtrend_book.py.

Catalog-only — no paper slot. Run on 5m bars; signal TF (20m / 30m) and 15m Supertrend are
built with 09:15-anchored session resampling. Indicator maths match the EA (RMA seed, EMA first
value, EA Supertrend). Closed-bar signals; index stop = k×ATR from signal close; no target;
no entries after last_entry (default 14:30); engine square-off 15:15; warm-up 300 signal bars
and 25 finished days. One position at a time (opposite signals ignored while in a trade).
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.strategies.daily_levels import DailyTracker, minute_of_day
from app.strategies.goldtrend_math import (
    ATRx,
    EMAx,
    HtBar,
    Ichimoku,
    MACDx,
    RSIx,
    SessionResampler,
    SupertrendEA,
)

MODES = ("long_short", "long_only", "short_only")


def _hhmm(value: str) -> int:
    try:
        h, m = (int(x) for x in str(value).split(":"))
    except ValueError:
        raise ValueError(f"entry time must be HH:MM, got {value!r}") from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"entry time must be HH:MM, got {value!r}")
    return h * 60 + m


def _cpr_side(cpr: tuple[float, float, float] | None, x: float) -> int:
    """+1 above TC, -1 below BC, 0 inside (BC,P,TC) from DailyTracker.cpr."""
    if cpr is None:
        return 0
    bc, _p, tc = cpr
    return 1 if x > tc else -1 if x < bc else 0


def _open_side(today_open: float | None, x: float) -> int:
    if today_open is None:
        return 0
    return 1 if x > today_open else -1 if x < today_open else 0


class _GoldTrendBase(Strategy):
    """Shared plumbing: 5m feed → signal TF + 15m ST, daily CPR/trend, warmup, entry window."""

    signal_minutes: int = 20
    default_stop: float = 1.5
    code: str = "GT"
    name: str = "gold_trend"

    def __init__(
        self,
        stop_mult: float | None = None,
        mode: str = "long_short",
        warmup_bars: int = 300,
        warmup_days: int = 25,
        last_entry: str = "14:30",
        first_entry: str = "09:20",
        lots: int = 1,
        source_minutes: int = 5,
        st15_atr: int = 10,
        st15_mult: float = 3.0,
        atr_len: int = 14,
        rsi_len: int = 14,
        **extra: Any,
    ) -> None:
        stop = float(self.default_stop if stop_mult is None else stop_mult)
        params = {
            "stop_mult": stop,
            "mode": mode,
            "warmup_bars": warmup_bars,
            "warmup_days": warmup_days,
            "last_entry": last_entry,
            "first_entry": first_entry,
            "lots": lots,
            "source_minutes": source_minutes,
            "st15_atr": st15_atr,
            "st15_mult": st15_mult,
            "atr_len": atr_len,
            "rsi_len": rsi_len,
            **extra,
        }
        super().__init__(**params)
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if isinstance(stop, bool) or not isinstance(stop, (int, float)) or not stop > 0:
            raise ValueError("stop_mult must be a number > 0")
        for key, value in (
            ("warmup_bars", warmup_bars),
            ("warmup_days", warmup_days),
            ("lots", lots),
            ("source_minutes", source_minutes),
            ("st15_atr", st15_atr),
            ("atr_len", atr_len),
            ("rsi_len", rsi_len),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        if isinstance(st15_mult, bool) or not isinstance(st15_mult, (int, float)) or not st15_mult > 0:
            raise ValueError("st15_mult must be a number > 0")
        self.stop_mult = float(stop)
        self.mode = mode
        self.warmup_bars = warmup_bars
        self.warmup_days = warmup_days
        self.lots = lots
        self.source_minutes = source_minutes
        self.first_entry_min = _hhmm(first_entry)
        self.last_entry_min = _hhmm(last_entry)
        if self.last_entry_min < self.first_entry_min:
            raise ValueError("last_entry must be >= first_entry")
        self.r_sig = SessionResampler(self.signal_minutes, source_minutes)
        self.r15 = SessionResampler(15, source_minutes)
        self.st15 = SupertrendEA(st15_atr, float(st15_mult))
        self.atr = ATRx(atr_len)
        self.rsi = RSIx(rsi_len)
        self.daily = DailyTracker()
        self.daily_ema = EMAx(20)
        self.htf = 0
        self.daily_trend = 0
        self.n_sig = 0
        self.prev: dict[str, Any] = {}
        self._init_variant(**extra)

    def _init_variant(self, **extra: Any) -> None:
        pass

    def on_start(self, ctx: Any) -> None:
        self.r_sig.reset()
        self.r15.reset()
        self.st15 = SupertrendEA(self.params["st15_atr"], float(self.params["st15_mult"]))
        self.atr = ATRx(int(self.params["atr_len"]))
        self.rsi = RSIx(int(self.params["rsi_len"]))
        self.daily = DailyTracker()
        self.daily_ema = EMAx(20)
        self.htf = 0
        self.daily_trend = 0
        self.n_sig = 0
        self.prev = {}
        self._reset_variant()

    def _reset_variant(self) -> None:
        pass

    def _cross(self, key: str, diff: float) -> int:
        p = self.prev.get(key)
        self.prev[key] = diff
        if p is None:
            return 0
        if p <= 0 < diff:
            return 1
        if p >= 0 > diff:
            return -1
        return 0

    def _flip(self, key: str, d: int) -> int:
        p = self.prev.get(key)
        self.prev[key] = d
        return d if (p is not None and p != d) else 0

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        self.daily.update(bar)
        if self.daily.new_day and self.daily.prev is not None:
            e = self.daily_ema.update(self.daily.prev.close)
            c = self.daily.prev.close
            self.daily_trend = 1 if c > e else -1 if c < e else 0

        for b15 in self.r15.on_bar(bar):
            self.htf = self.st15.update(b15.high, b15.low, b15.close)

        out: list[Signal] = []
        for b in self.r_sig.on_bar(bar):
            out.extend(self._on_signal_bar(b, ctx))
        return out

    def _on_signal_bar(self, b: HtBar, ctx: Any) -> list[Signal]:
        self.n_sig += 1
        a = self.atr.update(b.high, b.low, b.close)
        r = self.rsi.update(b.close)
        pos = ctx.position
        pos_side = 0 if pos.is_flat else (1 if pos.side > 0 else -1)
        side, early_exit = self._signal(b, a, r, pos_side)

        if early_exit and not pos.is_flat:
            return [Signal("EXIT", pos.lots, tag=f"{self.code.lower()}_early_exit")]

        if not side or not pos.is_flat:
            return []
        if self.n_sig < self.warmup_bars or len(self.daily.days) < self.warmup_days:
            return []
        tod = minute_of_day(b.time)
        if tod < self.first_entry_min or tod > self.last_entry_min:
            return []
        if self.mode == "long_only" and side < 0:
            return []
        if self.mode == "short_only" and side > 0:
            return []
        dist = self.stop_mult * a
        if side > 0:
            return [Signal("BUY", self.lots, tag=f"{self.code.lower()}_long", stop=b.close - dist)]
        return [Signal("SELL", self.lots, tag=f"{self.code.lower()}_short", stop=b.close + dist)]

    def _signal(self, b: HtBar, atr: float, rsi: float, pos_side: int) -> tuple[int, bool]:
        """Return (side, early_exit). side is +1/-1/0."""
        raise NotImplementedError


class GoldTrend9(_GoldTrendBase):
    """20m Ichimoku Tenkan/Kijun cross + cloud + 15m ST + CPR + RSI; early exit on reverse cross."""

    signal_minutes = 20
    default_stop = 1.5
    code = "GT9"
    name = "gold_trend_9"

    def _init_variant(
        self,
        tenkan: int = 9,
        kijun: int = 26,
        senkou: int = 52,
        cloud_shift: int = 26,
        **_: Any,
    ) -> None:
        for key, value in (("tenkan", tenkan), ("kijun", kijun), ("senkou", senkou), ("cloud_shift", cloud_shift)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        if not tenkan < kijun <= senkou:
            raise ValueError("need tenkan < kijun <= senkou")
        self.tenkan, self.kijun, self.senkou, self.cloud_shift = tenkan, kijun, senkou, cloud_shift
        self.ichi = Ichimoku(tenkan, kijun, senkou, cloud_shift)
        self.params.update(tenkan=tenkan, kijun=kijun, senkou=senkou, cloud_shift=cloud_shift)

    def _reset_variant(self) -> None:
        self.ichi = Ichimoku(self.tenkan, self.kijun, self.senkou, self.cloud_shift)

    def _signal(self, b: HtBar, atr: float, rsi: float, pos_side: int) -> tuple[int, bool]:
        ten, kij, ctop, cbot = self.ichi.update(b.high, b.low)
        x = self._cross("ichi", ten - kij)
        early = bool(pos_side and x == -pos_side)
        if x and ctop is not None and cbot is not None and self.htf == x:
            above = b.close > ctop if x > 0 else b.close < cbot
            if above and _cpr_side(self.daily.cpr(), b.close) == x and (rsi > 50 if x > 0 else rsi < 50):
                return x, early
        return 0, early


class GoldTrend2(_GoldTrendBase):
    """30m Supertrend(10,2) flip + 15m ST agrees + RSI >50/<50."""

    signal_minutes = 30
    default_stop = 2.0
    code = "GT2"
    name = "gold_trend_2"

    def _init_variant(self, st_atr: int = 10, st_mult: float = 2.0, **_: Any) -> None:
        if isinstance(st_atr, bool) or not isinstance(st_atr, int) or st_atr < 1:
            raise ValueError("st_atr must be a whole number >= 1")
        if isinstance(st_mult, bool) or not isinstance(st_mult, (int, float)) or not st_mult > 0:
            raise ValueError("st_mult must be a number > 0")
        self.st_atr, self.st_mult = st_atr, float(st_mult)
        self.st_sig = SupertrendEA(st_atr, float(st_mult))
        self.params.update(st_atr=st_atr, st_mult=float(st_mult))

    def _reset_variant(self) -> None:
        self.st_sig = SupertrendEA(self.st_atr, self.st_mult)

    def _signal(self, b: HtBar, atr: float, rsi: float, pos_side: int) -> tuple[int, bool]:
        stdir = self.st_sig.update(b.high, b.low, b.close)
        f = self._flip("st", stdir)
        if f and self.htf == f and (rsi > 50 if f > 0 else rsi < 50):
            return f, False
        return 0, False


class GoldTrend14(_GoldTrendBase):
    """20m MACD cross + close vs EMA200 + daily trend + close vs today's open. Stop 3×ATR."""

    signal_minutes = 20
    default_stop = 3.0
    code = "GT14"
    name = "gold_trend_14"

    def _init_variant(
        self,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        ema_len: int = 200,
        **_: Any,
    ) -> None:
        for key, value in (("macd_fast", macd_fast), ("macd_slow", macd_slow), ("macd_signal", macd_signal), ("ema_len", ema_len)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number >= 1")
        if not macd_fast < macd_slow:
            raise ValueError("macd_fast must be < macd_slow")
        self.macd_fast, self.macd_slow, self.macd_signal = macd_fast, macd_slow, macd_signal
        self.ema_len = ema_len
        self.macd = MACDx(macd_fast, macd_slow, macd_signal)
        self.ema200 = EMAx(ema_len)
        self.params.update(
            macd_fast=macd_fast, macd_slow=macd_slow, macd_signal=macd_signal, ema_len=ema_len,
        )

    def _reset_variant(self) -> None:
        self.macd = MACDx(self.macd_fast, self.macd_slow, self.macd_signal)
        self.ema200 = EMAx(self.ema_len)

    def _signal(self, b: HtBar, atr: float, rsi: float, pos_side: int) -> tuple[int, bool]:
        m, ms = self.macd.update(b.close)
        e200 = self.ema200.update(b.close)
        x = self._cross("macd", m - ms)
        if (
            x
            and (b.close > e200 if x > 0 else b.close < e200)
            and self.daily_trend == x
            and _open_side(self.daily.today_open, b.close) == x
        ):
            return x, False
        return 0, False
