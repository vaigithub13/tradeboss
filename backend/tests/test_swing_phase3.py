"""Swing Phase 3: rules, gate, and the research book. No live data."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.swing.delivery import DeliveryTable
from app.swing.research import (
    DEV_END,
    DEV_START,
    FORWARD_START,
    OOS_END,
    OOS_START,
    BookSpec,
    cliff_labels,
    judge,
    overlaps_holdout,
    param_grid,
    pnl_without_top_winners,
    research_windows,
    run_research,
    shift_param,
)
from app.swing.strategies import DEFAULTS, _momentum, _rolling_mean, _rolling_max, make_signals, prepare_tape, week_end_dates
from scripts.swing_phase3 import live_block


def _sessions(n: int, start: date = date(2016, 1, 4)) -> list[date]:
    days = []
    cursor = start
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _frame(days, *, close, high=None, low=None, open_=None, volume=100.0, lineage="L", symbol="AAA", size="large"):
    def at(value, i):
        return value[i] if isinstance(value, list) else value

    rows = []
    for i, day in enumerate(days):
        px = float(at(close, i))
        hi = px if high is None else float(at(high, i))
        lo = px if low is None else float(at(low, i))
        op = px if open_ is None else float(at(open_, i))
        rows.append(
            {
                "date": day,
                "lineage": lineage,
                "symbol": symbol,
                "size": size,
                "passes": True,
                "volume": float(at(volume, i)),
                "turnover": float(at(volume, i)) * px,
                "px_open": op,
                "px_high": hi,
                "px_low": lo,
                "px_close": px,
            }
        )
    return pd.DataFrame(rows)


def _signal(day, **kwargs):
    row = {
        "lineage": "L",
        "symbol": "AAA",
        "date": day,
        "score": 1.0,
        "size": "large",
        "atr": 1.0,
        "atr_mult": 2.0,
        "stop_price": float("nan"),
        "exit_kind": "time",
        "exit_sma": 5,
        "max_hold": 5,
    }
    row.update(kwargs)
    return row


def test_windows_leave_the_holdout_sealed_and_parameters_move_by_twenty_percent():
    latest = date(2026, 11, 25)
    windows = research_windows(latest)
    assert windows["DEV"] == (DEV_START, DEV_END)
    assert windows["OOS"] == (OOS_START, OOS_END)
    assert windows["FORWARD"] == (FORWARD_START, latest)
    for start, end in windows.values():
        assert not overlaps_holdout(start, end)
    with pytest.raises(ValueError):
        BookSpec(Decimal("50000"), 5, date(2026, 7, 1), date(2026, 8, 1))
    assert shift_param(20, -0.2) == 16
    assert shift_param(20, 0.2) == 24
    assert shift_param(252, 0.2) == 302
    labels = [label for label, _ in param_grid(DEFAULTS["S1"])]
    assert labels == ["breakout_n -20%", "breakout_n +20%", "vol_mult -20%", "vol_mult +20%", "atr_mult -20%", "atr_mult +20%"]
    assert live_block(True)
    assert live_block(False) is None


def test_momentum_skips_the_last_five_sessions_and_volume_average_excludes_today():
    close = np.array([100.0] * 130)
    close[-5:] = 300.0
    assert _momentum(close, 120, 5)[-1] == pytest.approx(0.0)
    assert _momentum(close, 120, 0)[-1] == pytest.approx(2.0)
    volume = np.array([1.0] * 20 + [100.0])
    assert _rolling_mean(volume, 20, include_today=False)[20] == pytest.approx(1.0)
    assert _rolling_mean(volume, 20, include_today=True)[20] == pytest.approx(5.95)
    high = np.array([1.0] * 5 + [9.0])
    assert _rolling_max(high, 5, include_today=False)[5] == pytest.approx(1.0)


def test_breakout_does_not_see_a_future_high_and_needs_the_regime():
    days = _sessions(30)
    close = [10.0] * 25 + [12.0, 12.0, 12.0, 12.0, 12.0]
    high = [10.0] * 25 + [12.0, 12.0, 12.0, 12.0, 50.0]
    bars = _frame(days, close=close, high=high, low=[c - 0.2 for c in close], volume=[10.0] * 25 + [20.0] * 5)
    nifty = pd.Series([100.0 + i for i in range(len(days))], index=pd.Index(days))
    tape = prepare_tape(bars, nifty)
    # 30 sessions cannot clear a 200-day average, so the regime blocks every name.
    assert make_signals("S1", tape, week_end_dates(days)).empty
    tape["regime"] = True
    first = make_signals("S1", tape, week_end_dates(days))
    future = tape.copy()
    future.attrs["bounds"] = tape.attrs["bounds"]
    future.loc[future.index[-1], "px_high"] = 1e9
    future["prior20"] = _rolling_max(future["px_high"].to_numpy(dtype=float), 20, include_today=False)
    second = make_signals("S1", future, week_end_dates(days))
    left = set(first.loc[first["date"] < days[-1], "date"])
    right = set(second.loc[second["date"] < days[-1], "date"])
    assert left == right
    assert days[25] in set(first["date"])


def test_entry_is_the_next_open_size_follows_equity_and_a_week_ends_on_friday():
    days = _sessions(6)
    bars = _frame(days, close=[100, 100, 100, 100, 100, 100], high=[101] * 6, low=[99] * 6, open_=[100] * 6)
    bars["sma5"] = 90.0
    bars["prev_close"] = [99, 100, 100, 100, 100, 100]
    signals = pd.DataFrame([_signal(days[0], atr=1.0)])
    spec = BookSpec(Decimal("10000"), 2, days[0], days[-1])
    result = run_research(bars, signals, DeliveryTable.load(), spec, calendar=days)
    trade = result["trades"].iloc[0]
    assert trade["signal_date"] == days[0].isoformat()
    assert trade["entry_date"] == days[1].isoformat()
    assert trade["qty"] == int(Decimal("5000") // Decimal(str(trade["entry_fill"])))
    assert trade["qty"] < 100
    assert trade["reason"] == "time"
    assert trade["hold_sessions"] == 5

    week = _sessions(3)
    short = _frame(week, close=[100, 100, 100], high=[101] * 3, low=[99] * 3, open_=[100] * 3)
    short["sma5"] = 110.0
    short["prev_close"] = 100.0
    ends = week_end_dates(week)
    booked = run_research(
        short,
        pd.DataFrame([_signal(week[0], exit_kind="week", atr=10.0)]),
        DeliveryTable.load(),
        BookSpec(Decimal("10000"), 2, week[0], week[-1]),
        calendar=week,
        week_ends=ends,
    )
    assert booked["trades"].iloc[0]["reason"] == "week"
    assert booked["trades"].iloc[0]["exit_date"] == week[-1].isoformat()


def test_pullback_exits_on_the_average_gap_stop_is_the_low_and_zero_brokerage_keeps_stt():
    days = _sessions(3)
    bars = _frame(days, close=[100, 100, 100], high=[101, 101, 101], low=[99, 99, 99], open_=[100, 100, 100])
    bars["sma5"] = [90.0, 90.0, 90.0]
    bars["sma4"] = 90.0
    bars["sma6"] = 90.0
    bars["prev_close"] = 100.0
    result = run_research(
        bars,
        pd.DataFrame([_signal(days[0], exit_kind="sma", atr=10.0)]),
        DeliveryTable.load(),
        BookSpec(Decimal("10000"), 1, days[0], days[-1]),
        calendar=days,
    )
    assert result["trades"].iloc[0]["reason"] == "sma"
    assert result["trades"].iloc[0]["exit_date"] == days[1].isoformat()

    gap = _frame(days, close=[100, 90, 90], high=[101, 91, 91], low=[99, 89, 89], open_=[100, 88, 90])
    gap["sma5"] = 1.0
    gap["prev_close"] = [99, 100, 90]
    stopped = run_research(
        gap,
        pd.DataFrame([_signal(days[0], stop_price=99.0, atr_mult=0.0)]),
        DeliveryTable.load(),
        BookSpec(Decimal("10000"), 1, days[0], days[-1]),
        calendar=days,
    )
    assert stopped["trades"].iloc[0]["reason"] == "stop_gap"
    assert stopped["trades"].iloc[0]["exit_fill"] == pytest.approx(88 * 0.999)

    table = DeliveryTable.load()
    plain = table.leg("BUY", Decimal("100"), 100, date(2017, 8, 1))
    free = table.leg("BUY", Decimal("100"), 100, date(2017, 8, 1), flat_override=Decimal("0"))
    assert free["brokerage"] == Decimal("0.00")
    assert free["stt"] == plain["stt"] == Decimal("10.00")
    assert free["gst"] < plain["gst"]


def test_event_guard_and_the_gate_numbers():
    days = _sessions(4)
    bars = _frame(
        days,
        close=[100, 100, 100, 120],
        high=[101, 101, 101, 122],
        low=[99, 99, 99, 119],
        open_=[100, 100, 100, 120],
    )
    bars["sma5"] = 200.0
    bars["prev_close"] = [99, 100, 100, 100]
    result = run_research(
        bars,
        pd.DataFrame([_signal(days[0], atr=20.0)]),
        DeliveryTable.load(),
        BookSpec(Decimal("10000"), 1, days[0], days[-1]),
        calendar=days,
    )
    assert result["trades"].iloc[0]["reason"] == "event"
    assert result["trades"].iloc[0]["exit_date"] == days[2].isoformat()

    good = {"cagr": 0.12, "pnl": 1000.0, "max_dd": 0.20, "avg_trade_pct": 0.01, "trades": 120}
    nifty = {"cagr": 0.08, "max_dd": 0.18}
    verdict = judge(good, good, nifty, [0.0] * 20, 100.0, [])
    assert verdict["pass"]
    short = dict(good)
    short["trades"] = 99
    assert not judge(good, short, nifty, [0.0] * 20, 100.0, [])["pass"]
    weak = dict(good)
    weak["avg_trade_pct"] = 0.004
    assert any("0.5%" in reason for reason in judge(good, weak, nifty, [0.0] * 20, 100.0, [])["reasons"])
    assert pnl_without_top_winners([10, 9, 8, -3, -2], 2) == pytest.approx(10 + 9 + 8 - 3 - 2 - 10 - 9)
    assert cliff_labels(0.10, 50.0, [("atr -20%", 0.04, 10.0)])
    assert cliff_labels(0.10, 50.0, [("atr +20%", 0.09, 40.0)]) == []
