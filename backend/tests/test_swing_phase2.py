"""Swing Phase 2: substitutions, event guard, delivery costs, and the dry-run book."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from app.backtest.costs import NoRatesForDate
from app.swing.delivery import DeliveryTable
from app.swing.engine import EngineConfig, prepare_signals, research_window, run_breakout, slipped
from app.swing.fixes import (
    blocked_entries,
    event_flags,
    is_mismatch,
    price_step,
    substitution_reason,
    turnover_units,
)
from scripts.swing_phase2 import live_block


def _sessions(n: int, start: date = date(2016, 1, 4)) -> list[date]:
    days = []
    cursor = start
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(days, *, lineage="L", symbol="AAA", open_, high, low, close, size="large", passes=True) -> pd.DataFrame:
    def value(item, i):
        return item[i] if isinstance(item, list) else item

    rows = []
    for i, day in enumerate(days):
        px = {
            "open": float(value(open_, i)),
            "high": float(value(high, i)),
            "low": float(value(low, i)),
            "close": float(value(close, i)),
        }
        rows.append(
            {
                "date": day,
                "lineage": lineage,
                "symbol": symbol,
                "size": size,
                "passes": passes,
                "volume": 1000.0,
                "turnover": 1e8,
                "close": px["close"],
                **{f"px_{name}": px[name] for name in px},
            }
        )
    return pd.DataFrame(rows)


def _nifty(days, start=100.0) -> pd.Series:
    return pd.Series([start + i for i in range(len(days))], index=pd.Index(days))


def _signal(day, *, lineage="L", symbol="AAA", score=1.0, atr=1.0, size="large", target=None) -> dict:
    row = {"lineage": lineage, "symbol": symbol, "date": day, "score": score, "atr": atr, "size": size}
    if target is not None:
        row["target"] = target
    return row


def _cfg(days, **kwargs) -> EngineConfig:
    base = dict(start=days[0], end=days[-1], atr_n=3, breakout_n=3, nifty_sma=3, capital=Decimal("50000"))
    base.update(kwargs)
    return EngineConfig(**base)


def test_no_lookahead_and_prior_high_excludes_today():
    days = _sessions(8)
    close = [10.0] * 5 + [11.0, 11.0, 11.0]
    high = [10.0] * 5 + [100.0, 11.0, 11.0]
    low = [9.5] * 8
    bars = _bars(days, open_=close, high=high, low=low, close=close)
    cfg = _cfg(days, atr_n=2, breakout_n=3, nifty_sma=2)
    first = prepare_signals(bars, _nifty(days, 10), cfg)
    assert days[5] in set(first["date"])
    # Today's high is 100. If it were inside the prior-20 window the close of 11 would not clear it.
    assert float(first.loc[first["date"] == days[5], "prior_high"].iloc[0]) == 10.0

    future = bars.copy()
    future.loc[future["date"] == days[7], "px_high"] = 1e9
    future.loc[future["date"] == days[7], "px_close"] = 1e8
    second = prepare_signals(future, _nifty(days, 10), cfg)
    left = first.loc[first["date"] < days[7], ["date", "score"]].reset_index(drop=True)
    right = second.loc[second["date"] < days[7], ["date", "score"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)


def test_entry_is_the_next_open_and_the_hold_ends_on_day_five():
    days = _sessions(12)
    close = [100.0 + i for i in range(len(days))]
    bars = _bars(days, open_=close, high=close, low=[c - 0.2 for c in close], close=close)
    cfg = _cfg(days)
    result = run_breakout(bars, _nifty(days), DeliveryTable.load(), cfg)
    trade = result["trades"].iloc[0]
    assert trade["signal_date"] == days[3].isoformat()
    assert trade["entry_date"] == days[4].isoformat()
    assert trade["entry_date"] > trade["signal_date"]
    assert trade["entry_fill"] == pytest.approx(104 * 1.001)
    assert trade["reason"] == "time"
    assert trade["hold_sessions"] == 5
    assert trade["exit_date"] == days[8].isoformat()
    assert trade["qty"] == int(Decimal("10000") // Decimal(str(trade["entry_fill"])))


def test_gap_through_the_stop_fills_at_the_open_unless_the_day_is_locked():
    days = _sessions(4)
    bars = _bars(
        days,
        open_=[100, 100, 97, 96],
        high=[101, 101, 97, 96.4],
        low=[99, 99.5, 97, 95],
        close=[100, 100.5, 97, 96],
    )
    signals = pd.DataFrame([_signal(days[0], atr=1)])
    result = run_breakout(bars, pd.Series(dtype=float), DeliveryTable.load(), _cfg(days), signals=signals)
    trade = result["trades"].iloc[0]
    assert trade["exit_date"] == days[3].isoformat()
    assert trade["reason"] == "stop_gap"
    assert trade["exit_fill"] == pytest.approx(96 * 0.999)
    assert days[2].isoformat() not in set(result["trades"]["exit_date"])


def test_a_stop_inside_the_day_fills_at_the_stop_and_a_target_loses_when_both_are_touched():
    days = _sessions(3)
    bars = _bars(
        days,
        open_=[100, 100, 100],
        high=[101, 101, 106],
        low=[99, 99.5, 97],
        close=[100, 100, 100],
    )
    signals = pd.DataFrame([_signal(days[0], atr=1, target=105)])
    result = run_breakout(bars, pd.Series(dtype=float), DeliveryTable.load(), _cfg(days), signals=signals)
    trade = result["trades"].iloc[0]
    assert trade["reason"] == "stop"
    assert trade["exit_fill"] == pytest.approx(98 * 0.999)


def test_event_guard_exits_the_day_before_and_blocks_two_sessions_after():
    days = _sessions(8)
    bars = _bars(
        days,
        open_=[100, 100, 100, 120, 121, 121, 121, 121],
        high=[101, 101, 101, 122, 122, 122, 122, 122],
        low=[99.5, 99.5, 99.5, 119, 120.5, 120.5, 120.5, 120.5],
        close=[100, 100, 100, 121, 121, 121, 121, 121],
    )
    signals = pd.DataFrame([_signal(days[i], score=1 + i * 0.01) for i in (0, 2, 3, 4, 5)])
    flags = event_flags(bars)
    assert list(flags["date"]) == [days[3]]
    result = run_breakout(
        bars, pd.Series(dtype=float), DeliveryTable.load(), _cfg(days), signals=signals, flags=flags
    )
    trades = result["trades"]
    guarded = trades.iloc[0]
    assert guarded["reason"] == "event"
    assert guarded["exit_date"] == days[2].isoformat()
    assert guarded["exit_fill"] == pytest.approx(100 * 0.999)
    entered = set(trades["entry_date"])
    assert days[3].isoformat() not in entered
    assert days[4].isoformat() not in entered
    assert days[5].isoformat() not in entered
    assert days[6].isoformat() in entered
    blocked = blocked_entries(flags, days, extra=2)
    assert ( "L", days[3] ) in blocked and ( "L", days[5] ) in blocked
    assert ( "L", days[6] ) not in blocked


def test_more_signals_than_slots_keeps_the_higher_score_and_cash_is_not_levered():
    days = _sessions(2)
    frames = []
    for i in range(6):
        frames.append(
            _bars(days, lineage=f"L{i}", symbol=f"S{i}", open_=100, high=101, low=99, close=100)
        )
    bars = pd.concat(frames, ignore_index=True)
    signals = pd.DataFrame([_signal(days[0], lineage=f"L{i}", symbol=f"S{i}", score=6 - i) for i in range(6)])
    result = run_breakout(bars, pd.Series(dtype=float), DeliveryTable.load(), _cfg(days, max_positions=5), signals=signals)
    assert set(result["trades"]["symbol"]) == {"S0", "S1", "S2", "S3", "S4"}
    assert int(result["daily"]["positions"].max()) <= 5

    tiny = EngineConfig(start=days[0], end=days[-1], capital=Decimal("100"), max_positions=5)
    empty = run_breakout(bars, pd.Series(dtype=float), DeliveryTable.load(), tiny, signals=signals.iloc[:1])
    assert empty["trades"].empty
    assert empty["daily"]["cash"].iloc[-1] == pytest.approx(100)


def test_delivery_costs_charge_stt_both_sides_and_dp_on_the_sell():
    table = DeliveryTable.load()
    assert table.row_for(date(2016, 5, 2)).rates["gst_pct"] == Decimal("14.5")
    assert table.row_for(date(2016, 6, 1)).rates["gst_pct"] == Decimal("15")
    assert table.row_for(date(2018, 6, 1)).rates["stamp_buy_pct"] == Decimal("0.01")
    assert table.row_for(date(2018, 6, 1)).rates["exchange_pct"] == Decimal("0.00325")
    with pytest.raises(NoRatesForDate):
        table.row_for(date(2015, 12, 31))

    buy = table.leg("BUY", Decimal("100"), 100, date(2017, 8, 1))
    sell = table.leg("SELL", Decimal("100"), 100, date(2017, 8, 1))
    assert buy["stt"] == Decimal("10.00")
    assert sell["stt"] == Decimal("10.00")
    assert buy["stamp"] == Decimal("1.00")
    assert sell["stamp"] == Decimal("0.00")
    assert buy["dp"] == Decimal("0.00")
    assert sell["dp"] == Decimal("18.50")
    assert buy["brokerage"] == Decimal("20.00")
    assert buy["exchange"] == Decimal("0.33")
    assert buy["gst"] == Decimal("3.66")
    assert buy.total == Decimal("35.00")
    assert sell["gst"] == Decimal("6.99")
    assert sell.total == Decimal("55.83")

    tiny = table.leg("BUY", Decimal("10"), 1, date(2017, 8, 1))
    assert tiny["brokerage"] == Decimal("0.25")
    assert slipped("BUY", Decimal("100"), Decimal("0.001")) == Decimal("100.1")
    assert slipped("SELL", Decimal("100"), Decimal("0.001")) == Decimal("99.9")


def test_substitution_needs_one_step_and_a_special_event_subject():
    days = _sessions(40)
    bhav = pd.Series([100.0] * 20 + [160.0] * 20, index=pd.Index(days))
    upstox = pd.Series([100.0] * 40, index=pd.Index(days))
    step = price_step(bhav, upstox)
    assert step["date"] == days[20]
    assert substitution_reason(step, ["Demerger"]) == "Demerger"
    assert substitution_reason(step, ["Rights 1:2 @ Premium Rs 10"]) == "Rights 1:2 @ Premium Rs 10"
    assert substitution_reason(step, ["Interim Dividend"]) is None
    assert is_mismatch({"overlap": 40, "over_tol": 20, "median_abs_pct": 0.2})

    drift = pd.Series([100 * (1.004 ** i) for i in range(40)], index=pd.Index(days))
    assert substitution_reason(price_step(drift, upstox), ["Demerger"]) is None


def test_turnover_units_match_across_the_format_change():
    rows = []
    for day, scale in ((date(2024, 7, 5), 1.0), (date(2024, 7, 8), 1.0)):
        rows.append({"date": day, "series": "EQ", "close": 100.0, "volume": 10.0, "turnover": 1000.0 * scale})
    same = turnover_units(pd.DataFrame(rows))
    assert same["same_units"]
    assert same["before"] == pytest.approx(1)
    assert same["after"] == pytest.approx(1)
    rows[1]["turnover"] = 10.0
    shifted = turnover_units(pd.DataFrame(rows))
    assert not shifted["same_units"]


def test_fourteen_percent_does_not_flag_and_the_dry_run_refuses_2019():
    days = _sessions(2)
    quiet = _bars(days, open_=[100, 114], high=[101, 115], low=[99, 113], close=[100, 114])
    assert event_flags(quiet).empty
    loud = _bars(days, open_=[100, 116], high=[101, 117], low=[99, 115], close=[100, 116])
    assert list(event_flags(loud)["date"]) == [days[1]]
    with pytest.raises(ValueError):
        EngineConfig(end=date(2019, 1, 1))
    assert research_window(date(2016, 1, 1), date(2019, 12, 31))
    assert research_window(date(2016, 1, 1), date(2018, 12, 31)) is None
    assert live_block(True)
    assert live_block(False) is None
