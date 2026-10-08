"""Shared builders for the backtest tests (Phase 3a). Nothing here touches real data."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.backtest.contracts import Signal, Strategy
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.result import BacktestResult
from app.backtest.sources import ListSource
from app.data.candle import Candle

IST = timezone(timedelta(hours=5, minutes=30))

MON = (2026, 1, 5)
TUE = (2026, 1, 6)
WED = (2026, 1, 7)
THU = (2026, 1, 8)
FRI = (2026, 1, 9)

OHLC = tuple[float, float, float, float]


def ist(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def hhmm(t: int) -> str:
    return datetime.fromtimestamp(t, IST).strftime("%H:%M")


def day_bars(day: tuple[int, int, int], ohlc: Sequence[OHLC], start: tuple[int, int] = (9, 15), step_min: int = 1,
             volume: float = 100.0) -> list[Candle]:
    t0 = ist(*day, *start)
    return [
        {"time": t0 + 60 * step_min * i, "open": o, "high": h, "low": lo, "close": c, "volume": volume, "oi": None}
        for i, (o, h, lo, c) in enumerate(ohlc)
    ]


def flat(price: float, n: int) -> list[OHLC]:
    return [(price, price, price, price)] * n


def full_day(day: tuple[int, int, int], price: float = 100.0, *, overrides: dict[int, OHLC] | None = None) -> list[Candle]:
    """A complete 09:15-15:29 session (375 one-minute bars) at a flat price, with per-minute overrides."""
    rows = flat(price, 375)
    for i, r in (overrides or {}).items():
        rows[i] = r
    return day_bars(day, rows)


def minute_index(h: int, m: int) -> int:
    return (h * 60 + m) - (9 * 60 + 15)


class Scripted(Strategy):
    """Emits the signals listed in `plan` (bar number -> signals or a callable), nothing else.

    Bar numbers count the on_bar calls made to this strategy, warm-up bars included (0 = the first bar it is shown)."""

    name = "scripted"

    def __init__(self, plan: dict[int, Any] | None = None, *, allow_overnight: bool = False) -> None:
        super().__init__()
        self.plan = plan or {}
        self.allow_overnight = allow_overnight
        self.n = -1
        self.seen: list[int] = []
        self.ctx_log: list[Any] = []

    def on_bar(self, bar: Candle, ctx: Any) -> list[Signal]:
        self.n += 1
        self.seen.append(bar["time"])
        item = self.plan.get(self.n, [])
        return list(item(bar, ctx)) if callable(item) else list(item)


def cfg(**kw: Any) -> BacktestConfig:
    """The engine tests pin the matching rules on the old timing (orders work from the bar's end);
    tests/test_bt_live_timing.py covers live timing, the default."""
    base: dict[str, Any] = {"timeframe": "1m", "lot_size": 1, "square_off": None, "warmup_bars": 0,
                            "live_timing": False}
    base.update(kw)
    return BacktestConfig(**base)


def run(strategy: Strategy, candles: list[Candle], *, base_minutes: int = 1, labels: dict[date, str] | None = None,
        **kw: Any) -> BacktestResult:
    return run_backtest(strategy, ListSource(candles, base_minutes, labels), cfg(**kw))


def buy(qty: int = 1, **kw: Any) -> Signal:
    return Signal("BUY", qty, **kw)


def sell(qty: int = 1, **kw: Any) -> Signal:
    return Signal("SELL", qty, **kw)


def exit_(**kw: Any) -> Signal:
    return Signal("EXIT", 1, **kw)


def kinds(res: BacktestResult, *wanted: str) -> list[dict[str, Any]]:
    return [e for e in res.events if e["kind"] in wanted]


def fills(res: BacktestResult) -> list[tuple[str, str, float]]:
    """(hh:mm, side, price) of every fill."""
    return [(hhmm(e["t"]), e["side"], e["price"]) for e in res.events if e["kind"] == "fill"]


# ---------------------------------------------------------------- random data for the look-ahead tests
def random_days(days: Sequence[tuple[int, int, int]], seed: int, *, start_price: float = 22000.0, vol: float = 6.0,
                drift: float = 0.0) -> list[Candle]:
    """Random-walk one-minute sessions (09:15-15:29). Valid OHLC; volume > 0."""
    rng = np.random.RandomState(seed)
    out: list[Candle] = []
    price = start_price
    for d in days:
        t0 = ist(*d, 9, 15)
        for i in range(375):
            o = price
            c = o + drift + rng.normal(0, vol)
            h = max(o, c) + abs(rng.normal(0, vol / 2))
            lo = min(o, c) - abs(rng.normal(0, vol / 2))
            out.append({"time": t0 + 60 * i, "open": round(o, 2), "high": round(h, 2), "low": round(lo, 2),
                        "close": round(c, 2), "volume": float(rng.randint(100, 1000)), "oi": None})
            price = c
    return out


Visible = Callable[[dict[str, Any]], bool]
