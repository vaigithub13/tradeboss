"""No look-ahead (1). The engine's most important property."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from app.backtest.contracts import LookAheadError, Signal, Strategy
from app.backtest.engine import run_backtest
from app.backtest.sources import ListSource
from app.data.candle import Candle
from app.indicators import registry
from app.indicators.frame import candles_to_frame
from app.strategies.ema_cross import EmaCrossover
from app.strategies.log_xz import LogXZ
from app.strategies.orb import OpeningRangeBreakout
from app.strategies.pivot_extension import PivotExtension
from app.strategies.price_channel import PriceChannel
from app.strategies.supertrend_flip import SupertrendFlip
from tests.bt_helpers import FRI, MON, THU, TUE, WED, Scripted, cfg, day_bars, flat, ist, random_days, run

DAYS = [MON, TUE, WED, THU, FRI]
T = ist(*WED, 11, 0)  # an hour boundary: aligned for 5m / 15m / 1h bars


class BracketTrader(Strategy):
    """Enters every 7th bar when flat with a bracket from PAST closes only."""

    name = "bracket_trader"

    def __init__(self) -> None:
        super().__init__()
        self.n = 0

    def on_bar(self, bar: Candle, ctx: Any) -> list[Signal]:
        self.n += 1
        if ctx.position.lots != 0 or self.n % 7 != 0 or len(ctx.bars) < 5:
            return []
        c = ctx.bars.close
        side = "BUY" if c[-1] >= c[-5:].mean() else "SELL"
        px = float(c[-1])
        up, dn = (px + 15, px - 15) if side == "BUY" else (px - 15, px + 15)
        return [Signal(side, 1, stop=dn, target=up)]  # type: ignore[arg-type]


STRATEGIES: dict[str, Callable[[], Strategy]] = {
    "ema": lambda: EmaCrossover(fast=5, slow=13),
    "supertrend": lambda: SupertrendFlip(atr_length=7, multiplier=2.0),
    "orb": lambda: OpeningRangeBreakout(range_minutes=15),
    "bracket": BracketTrader,
    "pivot_faithful": lambda: PivotExtension(left_bars=2, right_bars=1),
    "pivot_carried": lambda: PivotExtension(variant="carried_pivots", left_bars=2, right_bars=1),
    "log_xz": lambda: LogXZ(z_length=5),
    "price_channel": lambda: PriceChannel(length=5),
    "pivot_faithful_tv": lambda: PivotExtension(left_bars=2, right_bars=1, execution="tv_parity"),
    "pivot_carried_tv": lambda: PivotExtension(variant="carried_pivots", left_bars=2, right_bars=1, execution="tv_parity"),
    "log_xz_tv": lambda: LogXZ(z_length=5, execution="tv_parity"),
    "price_channel_tv": lambda: PriceChannel(length=5, execution="tv_parity"),
}


def original() -> list[Candle]:
    return random_days(DAYS, seed=7, start_price=22000.0, vol=6.0)


def scrambled(candles: list[Candle]) -> list[Candle]:
    """Same timestamps; every bar from T on is replaced by wild unrelated data (bigger moves, gaps)."""
    alt = {c["time"]: c for c in random_days(DAYS, seed=99, start_price=23500.0, vol=40.0)}
    return [alt[c["time"]] if c["time"] >= T else c for c in candles]


def visible(e: dict[str, Any]) -> bool:
    """Things that happened before T, plus the decisions taken AT T (they used bars that ended at T)."""
    return e["t"] < T or (e["t"] == T and e["kind"] in ("signal", "order_placed", "order_rejected"))


def events_until_t(candles: list[Candle], make: Callable[[], Strategy], tf: str) -> list[dict[str, Any]]:
    res = run_backtest(make(), ListSource(candles, 1), cfg(timeframe=tf, square_off="15:15", lot_size=1, warmup_bars=0))
    return [e for e in res.events if visible(e)]


@pytest.mark.parametrize("name", list(STRATEGIES))
@pytest.mark.parametrize("tf", ["5m", "15m"])
def test_1a_replacing_every_bar_after_t_changes_nothing_before_t(name: str, tf: str) -> None:
    a = events_until_t(original(), STRATEGIES[name], tf)
    b = events_until_t(scrambled(original()), STRATEGIES[name], tf)
    assert a == b
    # not vacuous: the strategy really traded before T
    assert len([e for e in a if e["kind"] == "fill"]) >= 2, f"{name}/{tf} did not trade before T; pick other params/data"


def test_1a_the_two_datasets_really_differ_after_t() -> None:
    a, b = original(), scrambled(original())
    assert [c for c in a if c["time"] < T] == [c for c in b if c["time"] < T]
    assert [c for c in a if c["time"] >= T] != [c for c in b if c["time"] >= T]


def test_1b_canary_a_leaky_indicator_is_caught_by_the_same_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    real = registry.compute

    def leaky(df, itype, params):  # noqa: ANN001, ANN202
        out = real(df, itype, params)
        return {k: np.append(v[40:], np.full(40, np.nan)) for k, v in out.items()}  # value at i = value at i+40

    monkeypatch.setattr(registry, "compute", leaky)
    a = events_until_t(original(), STRATEGIES["ema"], "5m")
    b = events_until_t(scrambled(original()), STRATEGIES["ema"], "5m")
    assert a != b


class Peeker(Strategy):
    name = "peeker"

    def __init__(self) -> None:
        super().__init__()
        self.raised = 0
        self.other: list[Exception] = []

    def on_bar(self, bar: Candle, ctx: Any) -> list[Signal]:
        n = len(ctx.bars)
        attempts: list[Callable[[], Any]] = [
            lambda: ctx.bars[n],
            lambda: ctx.bars[n + 5],
            lambda: ctx.bars[0 : n + 1],
            lambda: ctx.bars.at_time(bar["time"] + 10_000_000),
            lambda: ctx.indicator("ema", length=3)["ema"][n],
        ]
        for attempt in attempts:
            try:
                attempt()
            except LookAheadError:
                self.raised += 1
            except Exception as exc:  # noqa: BLE001
                self.other.append(exc)
        return []


def test_1c_every_attempt_to_read_the_future_raises_and_a_swallowing_strategy_still_fails_the_run() -> None:
    p = Peeker()
    candles = day_bars(MON, flat(100, 30))
    with pytest.raises(LookAheadError):
        run_backtest(p, ListSource(candles, 1), cfg())
    assert p.raised == 5 * 30 and p.other == []


class Honest(Strategy):
    name = "honest"

    def __init__(self) -> None:
        super().__init__()
        self.lengths: list[tuple[int, int, int, int]] = []

    def on_bar(self, bar: Candle, ctx: Any) -> list[Signal]:
        n = len(ctx.bars)
        assert ctx.bars[-1]["time"] == bar["time"] == ctx.bars[n - 1]["time"]
        assert ctx.bars.at_time(bar["time"]) is not None
        assert len(ctx.bars[0:n]) == n
        ind = ctx.indicator("ema", length=3)
        self.lengths.append((n, len(ctx.bars.close), len(ind.series("ema")), len(ctx.bars.times)))
        assert not ctx.bars.close.flags.writeable  # read-only copies: nothing to mutate or overrun
        return []


def test_1c_everything_ctx_exposes_has_exactly_the_past_in_it() -> None:
    h = Honest()
    run_backtest(h, ListSource(day_bars(MON, flat(100, 30)), 1), cfg())
    assert h.lengths == [(i, i, i, i) for i in range(1, 31)]


class Recorder(Strategy):
    name = "recorder"

    def __init__(self, indicators: list[tuple[str, dict[str, Any]]]) -> None:
        super().__init__()
        self.indicators = indicators
        self.got: dict[int, dict[str, dict[str, np.ndarray]]] = {}

    def on_bar(self, bar: Candle, ctx: Any) -> list[Signal]:
        n = len(ctx.bars)
        if n in (60, 61, 120, 200):
            self.got[n] = {}
            for itype, params in self.indicators:
                view = ctx.indicator(itype, **params)
                self.got[n][itype] = {name: view.series(name).copy() for name in registry.OUTPUTS[itype]}
        return []


def test_1d_ctx_indicator_values_equal_the_chart_code_run_on_the_past_only() -> None:
    """Why precomputing the indicator once is safe: value[i] == compute(bars[:i+1])[i], for every indicator."""
    candles = random_days([MON, TUE], seed=3, start_price=22000.0, vol=6.0)
    specs: list[tuple[str, dict[str, Any]]] = [
        ("sma", {"length": 20}),
        ("ema", {"length": 21}),
        ("bb", {"length": 20, "mult": 2.0}),
        ("supertrend", {"atr_length": 10, "multiplier": 3.0}),
        ("rsi", {"length": 14}),
        ("macd", {"fast": 12, "slow": 26, "signal": 9}),
        ("vwap", {}),
    ]
    rec = Recorder(specs)
    run_backtest(rec, ListSource(candles, 1), cfg())
    assert set(rec.got) == {60, 61, 120, 200}
    for n, per_type in rec.got.items():
        frame = candles_to_frame(candles[:n])
        for itype, params in specs:
            expected = registry.compute(frame, itype, registry.validate_params(itype, params))
            for name, values in per_type[itype].items():
                assert np.array_equal(values, expected[name], equal_nan=True), (n, itype, name)


def test_the_context_has_no_public_attribute_holding_the_future() -> None:
    """Checked from inside on_bar (the context object may be reused): nothing public is longer than the past."""
    checked: list[tuple[int, dict[str, int]]] = []

    def look(bar, ctx):  # noqa: ANN001, ANN202
        past = len(ctx.bars)
        sizes = {}
        for name in dir(ctx):
            if name.startswith("_"):
                continue
            v = getattr(ctx, name)
            if hasattr(v, "__len__") and not isinstance(v, str):
                sizes[name] = len(v)
        checked.append((past, sizes))
        return []

    run(Scripted({i: look for i in range(10)}), day_bars(MON, flat(100, 10)))
    assert len(checked) == 10
    for past, sizes in checked:
        assert all(s <= past for s in sizes.values()), (past, sizes)
