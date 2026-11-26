"""SpringPad crosses, fills, and the gate. No network and no holdout."""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.backtest.costs import NoRatesForDate
from app.backtest.instruments import cost_model_for
from app.swing.delivery import DeliveryTable
from app.swing.springpad import (
    FAST,
    SLOW,
    SpringSpec,
    Tape,
    breakeven_win_rate,
    bracket,
    build_tapes,
    cliff_hits,
    collect_signals,
    futures_gap,
    judge,
    length_variants,
    percent_variants,
    run_book,
    run_uncapped,
    walk_one,
)

START = date(2024, 6, 3)


def _days(n: int, start: date = START) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _frame(closes: list[float], opens: list[float] | None = None) -> pd.DataFrame:
    days = _days(len(closes), date(2016, 1, 4))
    opens = opens or closes
    return pd.DataFrame(
        {
            "date": days,
            "lineage": ["L"] * len(closes),
            "symbol": ["T"] * len(closes),
            "open": opens,
            "high": [max(a, b) + 1 for a, b in zip(opens, closes)],
            "low": [min(a, b) - 1 for a, b in zip(opens, closes)],
            "close": closes,
            "px_open": opens,
            "px_high": [max(a, b) + 1 for a, b in zip(opens, closes)],
            "px_low": [min(a, b) - 1 for a, b in zip(opens, closes)],
            "px_close": closes,
            "sessions": [300] * len(closes),
            "turnover_20d": [60_000_000.0] * len(closes),
            "size": ["large"] * len(closes),
        }
    )


def _tape(rows: list[tuple], death: list[bool] | None = None, golden: list[bool] | None = None) -> Tape:
    dates = [row[0] for row in rows]
    n = len(rows)
    return Tape(
        lineage="L",
        symbol="T",
        dates=dates,
        loc={day: i for i, day in enumerate(dates)},
        open=np.array([row[1] for row in rows], dtype=float),
        high=np.array([row[2] for row in rows], dtype=float),
        low=np.array([row[3] for row in rows], dtype=float),
        close=np.array([row[4] for row in rows], dtype=float),
        golden=np.array(golden if golden is not None else [False] * n),
        death=np.array(death if death is not None else [False] * n),
        eligible=np.ones(n, dtype=bool),
        size=tuple("large" for _ in rows),
        last=dates[-1],
    )


def _spec(**kwargs) -> SpringSpec:
    values = {
        "start": START,
        "end": START + timedelta(days=40),
        "slip_liquid": Decimal("0"),
        "slip_other": Decimal("0"),
    }
    values.update(kwargs)
    return SpringSpec(**values)


def _walk(tape: Tape, spec: SpringSpec, flags: set | None = None, entry: date | None = None, side: str = "long"):
    market = tape.dates
    signal_day = tape.dates[0]
    entry_day = entry or tape.dates[0]
    from app.swing.springpad import Signal

    signal = Signal("L", "T", "large", side, signal_day, entry_day)
    loc = {day: i for i, day in enumerate(market)}
    return walk_one(signal, tape, market, loc, market[-1], spec, flags or set())


def test_flat_series_is_not_a_golden_cross_and_a_later_close_does_not_move_it():
    flat = build_tapes(_frame([100.0] * 220), FAST, SLOW)
    assert not flat["L"].golden.any()
    closes = [100.0] * 200 + [110.0, 110.0]
    first = build_tapes(_frame(closes), FAST, SLOW)["L"]
    assert bool(first.golden[200])
    assert not first.golden[:200].any()
    later = build_tapes(_frame(closes + [10_000.0]), FAST, SLOW)["L"]
    assert np.array_equal(later.golden[: len(first.golden)], first.golden)


def test_entry_is_the_next_open():
    closes = [100.0] * 200 + [110.0, 111.0]
    opens = [100.0] * 200 + [110.0, 111.0]
    frame = _frame(closes, opens)
    tapes = build_tapes(frame, FAST, SLOW)
    market = tapes["L"].dates
    signals = collect_signals(tapes, market, market[0], market[-1], set(), "long", require_eligible=True)
    assert len(signals) == 1
    assert signals[0].signal_date == market[200]
    assert signals[0].entry_date == market[201]
    trade = run_uncapped(signals, tapes, market, _spec(start=market[0], end=market[-1], max_hold=5), set())
    assert trade[0]["entry_raw"] == 111.0
    assert trade[0]["entry_date"] != trade[0]["signal_date"]


def test_gap_through_the_stop_fills_at_the_open():
    days = _days(2)
    tape = _tape([(days[0], 100, 101, 99, 100), (days[1], 90, 91, 89, 90)])
    trade = _walk(tape, _spec(end=days[-1]))
    assert trade["reason"] == "stop_gap"
    assert trade["exit_raw"] == 90.0


def test_both_levels_on_the_same_day_fill_the_stop():
    days = _days(1)
    tape = _tape([(days[0], 100, 110, 90, 100)])
    trade = _walk(tape, _spec(end=days[-1]))
    assert trade["reason"] == "stop"
    assert trade["exit_raw"] == 98.0


def test_death_cross_exits_at_the_next_open():
    days = _days(3)
    rows = [(day, 100, 100.5, 99.5, 100) for day in days]
    tape = _tape(rows, death=[True, False, False])
    trade = _walk(tape, _spec(end=days[-1]))
    assert trade["reason"] == "cross"
    assert trade["exit_date"] == days[1]
    assert trade["exit_raw"] == 100.0


def test_variant_b_exits_at_the_close_of_session_five_and_variant_a_does_not():
    days = _days(6)
    rows = [(day, 100, 100.5, 99.5, 100) for day in days]
    tape = _tape(rows)
    limited = _walk(tape, _spec(end=days[-1], max_hold=5))
    assert limited["reason"] == "time"
    assert limited["exit_date"] == days[4]
    assert limited["exit_raw"] == 100.0
    assert limited["hold"] == 5
    open_ended = _walk(tape, _spec(end=days[-1], max_hold=None))
    assert open_ended["reason"] == "sample_end"
    assert open_ended["exit_date"] == days[5]


def test_a_death_cross_on_day_four_beats_the_day_five_time_exit():
    days = _days(6)
    rows = [(day, 100, 100.5, 99.5, 100) for day in days]
    death = [False] * 6
    death[3] = True
    tape = _tape(rows, death=death)
    trade = _walk(tape, _spec(end=days[-1], max_hold=5))
    assert trade["reason"] == "cross"
    assert trade["exit_date"] == days[4]
    assert trade["exit_raw"] == 100.0


def test_a_death_cross_on_day_five_loses_to_the_time_exit():
    days = _days(6)
    rows = [(day, 100, 100.5, 99.5, 100) for day in days]
    death = [False] * 6
    death[4] = True
    tape = _tape(rows, death=death)
    trade = _walk(tape, _spec(end=days[-1], max_hold=5))
    assert trade["reason"] == "time"
    assert trade["exit_date"] == days[4]
    assert trade["exit_raw"] == 100.0


def test_one_share_under_the_minimum_is_skipped():
    days = _days(1)
    tape = _tape([(days[0], 4000, 4000, 4000, 4000)])
    assert _walk(tape, _spec(end=days[-1], notional=Decimal("4500"))) is None


def test_a_flagged_entry_is_not_taken_and_a_flag_tomorrow_exits_today():
    closes = [100.0] * 200 + [110.0, 111.0]
    tapes = build_tapes(_frame(closes), FAST, SLOW)
    market = tapes["L"].dates
    entry = market[201]
    blocked = {("L", entry)}
    assert collect_signals(tapes, market, market[0], market[-1], blocked, "long", require_eligible=True) == []
    days = _days(3)
    tape = _tape([(day, 100, 100.5, 99.5, 100) for day in days])
    trade = _walk(tape, _spec(end=days[-1]), flags={("L", days[1])})
    assert trade["reason"] == "event"
    assert trade["exit_date"] == days[0]
    assert trade["exit_raw"] == 100.0


def test_holdout_window_is_refused():
    with pytest.raises(ValueError, match="holdout"):
        SpringSpec(date(2026, 6, 1), date(2026, 7, 15))
    SpringSpec(date(2022, 1, 1), date(2026, 6, 30))
    SpringSpec(date(2026, 10, 2), date(2026, 11, 23))


def test_sma_pair_and_bracket_shift_by_twenty_percent():
    assert length_variants(FAST, SLOW) == [("sma -20%", 40, 160), ("sma +20%", 60, 240)]
    spec = _spec()
    shifted = dict(percent_variants(spec))
    assert shifted["stop -20%"].stop_pct == Decimal("0.016")
    assert shifted["stop +20%"].stop_pct == Decimal("0.024")
    assert shifted["target -20%"].target_pct == Decimal("0.032")
    assert shifted["target +20%"].target_pct == Decimal("0.048")


def test_short_stop_is_above_the_fill_and_the_target_is_below():
    stop, target = bracket(Decimal("100"), "short", Decimal("0.02"), Decimal("0.04"))
    assert stop == Decimal("102")
    assert target == Decimal("96")
    days = _days(1)
    tape = _tape([(days[0], 100, 103, 99, 100)])
    trade = _walk(tape, _spec(end=days[-1], side="short"), side="short")
    assert trade["reason"] == "stop"
    assert trade["exit_raw"] == 102.0


def test_a_date_before_the_futures_table_is_not_costed():
    table = cost_model_for("index_future")
    with pytest.raises(NoRatesForDate):
        table.leg_cost("BUY", 100.0, 1, date(2021, 6, 1))
    assert futures_gap(table, date(2016, 1, 1), date(2021, 12, 31))
    assert futures_gap(table, date(2022, 1, 1), date(2026, 6, 30)) is None


def test_breakeven_with_no_cost_is_one_third():
    assert breakeven_win_rate(0.02, 0.04, 0.0) == pytest.approx(1 / 3)
    assert breakeven_win_rate(0.02, 0.04, 0.02) == pytest.approx(0.04 / 0.06)


def test_random_gate_needs_forty_eight_of_fifty():
    dev = {"pnl": 1.0, "avg": 0.01, "trades": 200}
    oos = {"pnl": 1.0, "avg": 0.01, "trades": 200}
    assert not judge(dev, oos, [0.0] * 47 + [0.02] * 3, [])["pass"]
    assert judge(dev, oos, [0.0] * 48 + [0.02] * 2, [])["pass"]


def test_a_losing_neighbour_is_a_cliff_and_a_losing_base_is_not():
    assert cliff_hits(10.0, 0.01, [("sma -20%", -1.0, 0.02)]) == ["sma -20%"]
    assert cliff_hits(10.0, 0.01, [("stop -20%", 4.0, 0.004)]) == ["stop -20%"]
    assert cliff_hits(10.0, 0.01, [("stop +20%", 4.0, 0.005)]) == []
    assert cliff_hits(-1.0, -0.01, [("sma +20%", -5.0, -0.02)]) == []


def test_portfolio_takes_five_names_and_a_loss_funds_fewer_slots():
    costs = DeliveryTable.load()

    def charge(side, price, qty, on):
        return costs.leg(side, price, qty, on).total

    days = _days(2)
    tapes = {}
    signals = []
    from app.swing.springpad import Signal

    for name in ("a", "b", "c", "d", "e", "f"):
        tapes[name] = Tape(
            lineage=name,
            symbol=name,
            dates=days,
            loc={day: i for i, day in enumerate(days)},
            open=np.array([100.0, 100.0]),
            high=np.array([100.5, 100.5]),
            low=np.array([99.5, 99.5]),
            close=np.array([100.0, 100.0]),
            golden=np.array([False, False]),
            death=np.array([False, False]),
            eligible=np.array([True, True]),
            size=("large", "large"),
            last=days[-1],
        )
        signals.append(Signal(name, name, "large", "long", days[0] - timedelta(days=1), days[0]))
    wide = _spec(end=days[-1], capital=Decimal("200000"), max_positions=5)
    booked = run_book(signals, tapes, days, wide, set(), charge)
    assert len(booked["trades"]) == 5
    assert {item["lineage"] for item in booked["trades"]} == {"a", "b", "c", "d", "e"}
    assert min(item["entry_date"] for item in booked["trades"]) > min(item["signal_date"] for item in booked["trades"])
    narrow = _spec(end=days[-1], capital=Decimal("12000"), max_positions=5)
    small = run_book(signals[:3], tapes, days, narrow, set(), charge)
    assert len(small["trades"]) == 2
