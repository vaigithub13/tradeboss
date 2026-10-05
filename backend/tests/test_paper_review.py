"""End-of-day check (live signals vs the normal backtest) and the weekly summary."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.paper.check import compare_signals
from app.paper.store import save_day, weekly_summary

IST = timezone(timedelta(hours=5, minutes=30))
NINE_FIFTEEN = int(datetime(2026, 10, 5, 9, 15, tzinfo=IST).timestamp())
NINE_TWENTY = NINE_FIFTEEN + 300


def bar(close: float, *, o: float = 100.0, h: float = 101.0, lo: float = 99.0) -> dict:
    return {"open": o, "high": h, "low": lo, "close": close}


def test_identical_signals_produce_no_differences() -> None:
    entries = [{"time": NINE_FIFTEEN, "side": "BUY"}]
    bars = {NINE_FIFTEEN: bar(100.0)}
    assert compare_signals(entries, entries, live_bars=bars, bt_bars=bars, incomplete=set()) == []


def test_a_live_gap_on_that_bar_is_the_reason_when_the_live_bar_was_incomplete() -> None:
    bt = [{"time": NINE_FIFTEEN, "side": "BUY"}]
    diffs = compare_signals([], bt, live_bars={}, bt_bars={NINE_FIFTEEN: bar(100.0)},
                            incomplete={NINE_FIFTEEN})
    assert diffs == [{"time": "09:15", "side": "BUY", "source": "backtest",
                      "reason": "live 5m bar incomplete (feed gap)"}]


def test_a_different_close_is_reported_with_both_values() -> None:
    live = [{"time": NINE_FIFTEEN, "side": "SELL"}]
    bt = [{"time": NINE_FIFTEEN, "side": "SELL"}]
    diffs = compare_signals(live, bt, live_bars={NINE_FIFTEEN: bar(100.0)},
                            bt_bars={NINE_FIFTEEN: bar(100.5)}, incomplete=set())
    assert diffs == []  # same signal, so bar differences alone are not a diff

    diffs = compare_signals([], bt, live_bars={NINE_FIFTEEN: bar(100.0)},
                            bt_bars={NINE_FIFTEEN: bar(100.5)}, incomplete=set())
    assert len(diffs) == 1
    assert diffs[0]["source"] == "backtest"
    assert "close 100.0 live vs 100.5 backtest" in diffs[0]["reason"]


def test_same_bar_same_data_but_no_live_signal_means_state_differs() -> None:
    bt = [{"time": NINE_TWENTY, "side": "BUY"}]
    bars = {NINE_TWENTY: bar(100.0)}
    diffs = compare_signals([], bt, live_bars=bars, bt_bars=bars, incomplete=set())
    assert diffs[0]["reason"] == "same bar and prices, so the strategy state differed (position or warm-up)"


def test_a_side_mismatch_on_the_same_bar_is_two_differences() -> None:
    live = [{"time": NINE_FIFTEEN, "side": "BUY"}]
    bt = [{"time": NINE_FIFTEEN, "side": "SELL"}]
    bars = {NINE_FIFTEEN: bar(100.0)}
    diffs = compare_signals(live, bt, live_bars=bars, bt_bars=bars, incomplete=set())
    assert {(d["source"], d["side"]) for d in diffs} == {("live", "BUY"), ("backtest", "SELL")}


def test_weekly_summary_adds_up_the_days_of_the_iso_week(tmp_path) -> None:
    mon = date(2026, 10, 5)  # a Monday
    days = {
        mon: {"summary": {"trades": 2, "wins": 1, "gross": 300.0, "charges": 100.0, "net": 200.0,
                          "modelled_legs": 1}},
        mon + timedelta(days=2): {"summary": {"trades": 1, "wins": 0, "gross": -50.0, "charges": 50.0,
                                              "net": -100.0, "modelled_legs": 0}},
    }
    for d, payload in days.items():
        save_day(tmp_path, d, payload)
    week = weekly_summary(tmp_path, mon + timedelta(days=4))  # a Friday in the same week
    assert week["week"] == "2026-W41"
    assert week["totals"]["trades"] == 3 and week["totals"]["wins"] == 1
    assert week["totals"]["net"] == 100.0 and week["totals"]["modelled_legs"] == 1
    assert [d["date"] for d in week["days"]] == ["2026-10-05", "2026-10-07"]
