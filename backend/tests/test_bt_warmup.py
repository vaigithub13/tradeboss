"""Strategy warm-up: a run that starts at T and a longer run sliced from T give identical signals after T.

T is the first bar of a session. The strategy is flat at every session start (no overnight carry), so both
runs begin trading from the same position. Only the strategy's own state can differ, and warm-up is what
makes it the same: the strategy has seen the bars before T, with trading blocked.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from app.backtest.costs import get_cost_model
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.sources import ListSource
from app.strategies.log_xz import LogXZ
from tests.bt_helpers import ist


def weekdays(start: date, count: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic(days: int = 60, seed: int = 7) -> list[dict]:
    """1-minute NIFTY-like bars, 09:15-15:29 each weekday, from a seeded random walk."""
    rng = random.Random(seed)
    candles: list[dict] = []
    price = 22_000.0
    for d in weekdays(date(2026, 1, 5), days):
        for i in range(375):
            o = price
            c = o + rng.gauss(0, 6.0)
            hi, lo = max(o, c) + abs(rng.gauss(0, 2.0)), min(o, c) - abs(rng.gauss(0, 2.0))
            t = ist(d.year, d.month, d.day, 9, 15) + 60 * i
            candles.append({"time": t, "open": o, "high": hi, "low": lo, "close": c, "volume": 100.0, "oi": None})
            price = c
    return candles


def run(candles: list[dict], *, start: str | None, end: str | None, warmup_bars: int = 500):
    cfg = BacktestConfig(
        timeframe="5m", start=start, end=end, session_types=("normal",), underlying=None,
        lot_table=None, lot_size=1, contract=None, cost_model=get_cost_model("zero"), warmup_bars=warmup_bars,
    )
    return run_backtest(LogXZ(), ListSource(candles, 1), cfg)


def signals_from(res, t_from: int) -> list[tuple[int, str]]:
    return [(e["t"], e["side"]) for e in res.events if e["kind"] == "signal" and e["t"] >= t_from]


def test_a_run_from_T_matches_a_longer_run_sliced_from_T() -> None:
    candles = synthetic()
    days = weekdays(date(2026, 1, 5), 60)
    T_day = days[40]  # ~7 weeks of history before it: far more than the 500-bar warm-up
    T = ist(T_day.year, T_day.month, T_day.day, 9, 15)
    last = days[-1].isoformat()

    long_run = run(candles, start=days[0].isoformat(), end=last)
    from_T = run(candles, start=T_day.isoformat(), end=last)

    after = signals_from(long_run, T)
    assert len(after) >= 5, "the synthetic series should produce signals after T, or the proof means nothing"
    assert signals_from(from_T, T) == after


def test_the_warm_up_is_what_makes_the_runs_agree() -> None:
    """Control: without warm-up the run from T starts with an empty state and does not match."""
    candles = synthetic()
    days = weekdays(date(2026, 1, 5), 60)
    T_day = days[40]
    T = ist(T_day.year, T_day.month, T_day.day, 9, 15)
    last = days[-1].isoformat()

    long_run = run(candles, start=days[0].isoformat(), end=last)
    cold = run(candles, start=T_day.isoformat(), end=last, warmup_bars=0)
    assert signals_from(cold, T) != signals_from(long_run, T)


def test_warm_up_bars_never_trade() -> None:
    candles = synthetic()
    days = weekdays(date(2026, 1, 5), 60)
    T_day = days[40]
    res = run(candles, start=T_day.isoformat(), end=days[-1].isoformat())
    T = ist(T_day.year, T_day.month, T_day.day, 9, 15)
    assert all(t.entry_time >= T for t in res.trades)
    assert all(e["t"] >= T for e in res.events if e["kind"] == "signal")


def test_warm_up_for_a_day_uses_only_earlier_days_and_does_not_need_the_days_own_bars() -> None:
    """Live 7 Oct: at the open nothing of the day is stored yet. The warm-up came back empty and the paper
    strategy ran cold (2 signals against the backtest's 6). It must be the same bars whether or not the day exists."""
    from app.backtest.engine import warm_bars

    candles = synthetic()
    days = weekdays(date(2026, 1, 5), 60)
    D = days[40]
    T = ist(D.year, D.month, D.day, 9, 15)
    cfg = BacktestConfig(
        timeframe="5m", start=D.isoformat(), end=D.isoformat(), session_types=("normal",), underlying=None,
        lot_table=None, lot_size=1, contract=None, cost_model=get_cost_model("zero"), warmup_bars=500,
    )
    with_day = warm_bars(cfg, ListSource(candles, 1))
    before_open = warm_bars(cfg, ListSource([c for c in candles if c["time"] < T], 1))
    assert len(with_day) == 500 and all(b["time"] < T for b in with_day)
    assert before_open == with_day
    # nothing of the day itself, even when later days are stored
    assert warm_bars(cfg, ListSource(candles, 1))[-1]["time"] < T


def test_warm_up_skips_days_with_no_bars_and_takes_the_last_stored_days() -> None:
    """A holiday or a weekend before the day is skipped: the warm-up is the last 500 stored bars before it."""
    from app.backtest.engine import warm_bars

    candles = synthetic()
    days = weekdays(date(2026, 1, 5), 60)
    D = days[40]
    T = ist(D.year, D.month, D.day, 9, 15)
    gap_start = ist(days[38].year, days[38].month, days[38].day, 9, 15)  # two missing days before D
    stored = [c for c in candles if c["time"] < gap_start]
    cfg = BacktestConfig(
        timeframe="5m", start=D.isoformat(), end=D.isoformat(), session_types=("normal",), underlying=None,
        lot_table=None, lot_size=1, contract=None, cost_model=get_cost_model("zero"), warmup_bars=500,
    )
    bars = warm_bars(cfg, ListSource(stored, 1))
    assert len(bars) == 500 and bars[-1]["time"] < gap_start and bars[-1]["time"] < T
