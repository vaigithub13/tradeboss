"""Sample strategies (11): EMA crossover, Supertrend flip, opening range breakout."""

from __future__ import annotations

import numpy as np
import pytest

from app.backtest.engine import run_backtest
from app.backtest.sources import ListSource
from app.data.resampler import resample
from app.indicators import registry
from app.indicators.frame import candles_to_frame
from app.strategies.ema_cross import EmaCrossover
from app.strategies.orb import OpeningRangeBreakout
from app.strategies.supertrend_flip import SupertrendFlip
from tests.bt_helpers import MON, TUE, OHLC, cfg, day_bars, fills, full_day, hhmm, kinds, minute_index, random_days


# ---------------------------------------------------------------- EMA crossover
HAND: list[OHLC] = [
    (10, 10.5, 9.5, 10),
    (10, 10.5, 9.5, 10),
    (10, 10.5, 9.5, 10),
    (10, 12.5, 9.5, 12),
    (12, 14.5, 11.5, 14),
    (14, 14.5, 12.5, 13),
    (13, 13.5, 10.5, 11),
    (11, 11.5, 8.5, 9),
]
# fast = EMA(1) = close, slow = EMA(2) (alpha 2/3, seeded with the first close):
#   slow = 10, 10, 10, 11.333, 13.111, 13.037, 11.679, 9.893
#   close - slow crosses UP on bar 3 (+0.667) and DOWN on bar 5 (-0.037)


def test_11_ema_cross_by_hand_long_only() -> None:
    res = run_backtest(EmaCrossover(fast=1, slow=2, mode="long_only"), ListSource(day_bars(MON, HAND), 1), cfg())
    assert fills(res) == [("09:19", "BUY", 12.0), ("09:21", "SELL", 13.0)]  # next-open of bars 3 and 5
    (t,) = res.trades
    assert t.net_pnl == 1.0 and t.direction == "LONG"


def test_11_ema_cross_by_hand_long_short_reverses_on_the_cross() -> None:
    res = run_backtest(EmaCrossover(fast=1, slow=2, mode="long_short"), ListSource(day_bars(MON, HAND), 1), cfg())
    assert [(t.direction, t.entry_price, t.exit_price, t.exit_reason) for t in res.trades] == [
        ("LONG", 12.0, 13.0, "market"),
        ("SHORT", 13.0, 9.0, "end_of_data"),
    ]


def test_11_ema_cross_acts_only_after_the_slow_length_and_validates_parameters() -> None:
    with pytest.raises(ValueError):
        EmaCrossover(fast=21, slow=9)
    flat_data = day_bars(MON, [(10, 10, 10, 10)] * 30)
    assert run_backtest(EmaCrossover(fast=3, slow=5), ListSource(flat_data, 1), cfg()).trades == []


def test_11_ema_cross_decisions_equal_the_crosses_of_the_chart_indicator() -> None:
    candles = random_days([MON, TUE], seed=21)
    bars = resample(candles, "5m", 1)
    frame = candles_to_frame(bars)
    f = registry.compute(frame, "ema", registry.validate_params("ema", {"length": 5}))["ema"]
    s = registry.compute(frame, "ema", registry.validate_params("ema", {"length": 13}))["ema"]
    d = f - s
    expected = [bars[i]["time"] + 300 for i in range(13, len(bars)) if (d[i - 1] <= 0 < d[i]) or (d[i - 1] >= 0 > d[i])]
    res = run_backtest(EmaCrossover(fast=5, slow=13, mode="long_short"), ListSource(candles, 1), cfg(timeframe="5m"))
    got = sorted({e["t"] for e in kinds(res, "signal")})
    # the strategy cannot act on the last bar of a day's data twice, but every cross must be a decision
    assert got == expected and len(expected) >= 4


# ---------------------------------------------------------------- Supertrend flip
def test_11_supertrend_flip_decisions_equal_the_direction_changes_of_the_chart_indicator() -> None:
    candles = random_days([MON, TUE], seed=33, vol=8.0)
    bars = resample(candles, "5m", 1)
    frame = candles_to_frame(bars)
    st = registry.compute(frame, "supertrend", registry.validate_params("supertrend", {"atr_length": 7, "multiplier": 2.0}))
    direction = st["direction"]
    flips = [i for i in range(1, len(bars)) if not np.isnan(direction[i - 1]) and direction[i] != direction[i - 1]]
    assert len(flips) >= 3
    res = run_backtest(SupertrendFlip(atr_length=7, multiplier=2.0), ListSource(candles, 1), cfg(timeframe="5m"))
    signals = kinds(res, "signal")
    assert sorted({e["t"] for e in signals}) == [bars[i]["time"] + 300 for i in flips]
    # at every flip the NEW side is taken (direction -1 = uptrend in TradingView's convention);
    # a reversal is "EXIT, then the new side", so the last decision of that bar is the new side
    for i in flips:
        at = [e["side"] for e in signals if e["t"] == bars[i]["time"] + 300]
        assert at[-1] == ("BUY" if direction[i] == -1 else "SELL"), (i, at)


def test_11_supertrend_flip_never_trades_during_warm_up() -> None:
    candles = day_bars(MON, [(100 + i % 3, 101 + i % 3, 99 + i % 3, 100 + (i + 1) % 3) for i in range(8)])
    res = run_backtest(SupertrendFlip(atr_length=10, multiplier=3.0), ListSource(candles, 1), cfg())
    assert res.trades == [] and kinds(res, "signal") == []


# ---------------------------------------------------------------- Opening range breakout
def day(overrides: dict[int, OHLC], d: tuple[int, int, int] = MON):  # noqa: ANN201
    base: dict[int, OHLC] = {3: (100, 102, 100, 100), 8: (100, 100, 98, 100)}  # range 09:15-09:30: high 102, low 98
    base.update(overrides)
    return full_day(d, 100, overrides=base)


def orb(candles, **kw):  # noqa: ANN001, ANN201
    return run_backtest(OpeningRangeBreakout(range_minutes=15), ListSource(candles, 1), cfg(timeframe=kw.pop("tf", "5m"), square_off="15:15", **kw))


def test_11_orb_long_breakout_fills_at_the_trigger_and_squares_off() -> None:
    res = orb(day({minute_index(9, 40): (100, 103, 100, 102.5)}))
    assert fills(res) == [("09:40", "BUY", 102.0), ("15:15", "SELL", 100.0)]
    (t,) = res.trades
    assert (t.direction, t.exit_reason, t.net_pnl) == ("LONG", "square_off", -2.0)
    placed = [(hhmm(e["t"]), e["side"], e["type"], e["price"]) for e in kinds(res, "order_placed") if e["tag"].startswith("orb")]
    assert placed[:2] == [("09:30", "BUY", "SL", 102.0), ("09:30", "SELL", "SL", 98.0)]
    assert [c["reason"] for c in kinds(res, "order_cancelled")][:1] == ["oco"]  # the other side is gone


def test_11_orb_gap_through_the_trigger_fills_at_the_open() -> None:
    res = orb(day({minute_index(9, 40): (104, 105, 103.5, 104.5)}))
    assert fills(res)[0] == ("09:40", "BUY", 104.0) and res.trades[0].gap is True


def test_11_orb_short_breakout() -> None:
    res = orb(day({minute_index(9, 40): (100, 100, 97, 97.5)}))
    assert fills(res)[0] == ("09:40", "SELL", 98.0)
    t = res.trades[0]
    assert (t.direction, t.exit_price, t.net_pnl) == ("SHORT", 100.0, -2.0)


def test_11_orb_no_breakout_no_trade_and_the_range_itself_is_not_tradable() -> None:
    assert orb(day({})).trades == []
    # a spike INSIDE the range widens it to 103; later highs of 102.5 are then not a breakout
    spike = orb(day({minute_index(9, 27): (100, 103, 100, 100), minute_index(9, 40): (100, 102.5, 100, 101)}))
    assert spike.trades == [] and fills(spike) == []


def test_11_orb_one_trade_per_day_even_after_the_stop_is_hit() -> None:
    res = orb(day({
        minute_index(9, 40): (100, 103, 100, 102.5),  # long entry at 102
        minute_index(10, 15): (100, 100, 97.5, 98),  # stop at 98
        minute_index(11, 15): (100, 103, 100, 103),  # breaks out again: no second trade
    }))
    (t,) = res.trades
    assert (t.exit_reason, t.exit_price, t.net_pnl) == ("stop", 98.0, -4.0)


def test_11_orb_trades_each_day_once() -> None:
    candles = day({minute_index(9, 40): (100, 103, 100, 102.5)}, MON) + day({minute_index(9, 40): (100, 100, 97, 97.5)}, TUE)
    res = orb(candles)
    assert [t.direction for t in res.trades] == ["LONG", "SHORT"]


def test_11_orb_works_on_a_15_minute_chart_and_rejects_a_chart_coarser_than_the_range() -> None:
    res = orb(day({minute_index(9, 40): (100, 103, 100, 102.5)}), tf="15m")
    assert fills(res)[0] == ("09:40", "BUY", 102.0)
    with pytest.raises(ValueError):
        orb(day({}), tf="30m")
