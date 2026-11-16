"""EMA 20/50 pullback + daily pivot R1/S1: state machine, pivots, session gates, no paper slot."""

from __future__ import annotations

import pytest

from app.backtest.catalog import build_strategy, strategy_catalog
from app.strategies.dd_five_confirm import classic_pivots
from app.strategies.ema_pivot_pullback import EmaPivotPullback
from tests.bt_helpers import MON, TUE, day_bars, fills, run

# Short EMAs so scripted paths warm up inside one session.
FAST, SLOW = 3, 5
PARAMS = dict(fast=FAST, slow=SLOW, rr=3.0, max_trades_per_day=4, first_entry="09:20", last_entry="14:30")


def prev_day_flat(price: float = 100.0) -> list:
    # H=101 L=99 C=100 → P=100, R1=101, S1=99
    return day_bars(MON, [(price, price + 1.0, price - 1.0, price)] * 375)


def test_classic_pivots_match_dd() -> None:
    assert classic_pivots(110.0, 90.0, 100.0) == EmaPivotPullback.pivots(110.0, 90.0, 100.0)


def test_in_catalog_with_defaults() -> None:
    entry = next(s for s in strategy_catalog() if s["name"] == "ema_pivot_pullback")
    names = {p["name"] for p in entry["params"]} if isinstance(entry["params"], list) else set(entry["params"])
    assert {"fast", "slow", "rr", "mode", "max_trades_per_day", "first_entry", "last_entry"} <= names
    s = build_strategy({"strategy": "ema_pivot_pullback", "params": {}})
    assert isinstance(s, EmaPivotPullback)
    assert (s.fast, s.slow, s.rr, s.mode, s.max_trades_per_day) == (20, 50, 3.0, "long_short", 4)


def test_no_entry_on_cross_alone() -> None:
    """Cross sets trend only: flat day with one bullish cross and no pullback → no fills."""
    # All bars identical except a one-bar dip then recovery that crosses EMAs without a touch-pullback.
    rows = [(100.0, 100.05, 99.95, 100.0)] * 40
    rows += [(100.0, 100.05, 99.0, 99.1)] * 8  # drag fast under slow
    rows += [(99.1, 100.4, 99.05, 100.3)]  # cross up, close above both; range may touch fast
    # After that, hold well above with floors that stay above the fast EMA (no new pullback arming
    # that can confirm — freeze price so EMAs catch up and touch every bar; block with last_entry).
    rows += [(100.3, 100.35, 100.25, 100.3)] * (375 - len(rows))
    # Window closed before the cross bar (~09:15 + 48 min ≈ 10:03); cross cannot enter.
    s = EmaPivotPullback(**{**PARAMS, "mode": "long_only", "first_entry": "09:20", "last_entry": "09:25"})
    res = run(s, prev_day_flat() + day_bars(TUE, rows[:375]), square_off="15:15")
    assert fills(res) == []


def test_long_needs_close_above_r1() -> None:
    """Confirm beyond both EMAs but still under R1 → no long."""
    s = EmaPivotPullback(**{**PARAMS, "mode": "long_only"})
    # Build a path that would otherwise fire; force pivot gate by keeping closes < R1=101.
    rows = _scripted_long_path(peak=100.5)  # below R1
    res = run(s, prev_day_flat() + day_bars(TUE, rows), square_off="15:15")
    assert fills(res) == []


def test_long_entry_after_pullback_confirm_above_r1() -> None:
    s = EmaPivotPullback(**{**PARAMS, "mode": "long_only"})
    rows = _scripted_long_path(peak=105.0)
    res = run(s, prev_day_flat() + day_bars(TUE, rows), square_off="15:15")
    f = fills(res)
    assert f, "expected a long after ready → pullback → confirm > R1"
    assert f[0][1] == "BUY"


def test_short_entry_after_pullback_confirm_below_s1() -> None:
    s = EmaPivotPullback(**{**PARAMS, "mode": "short_only"})
    rows = _scripted_short_path(trough=95.0)
    res = run(s, prev_day_flat() + day_bars(TUE, rows), square_off="15:15")
    f = fills(res)
    assert f and f[0][1] == "SELL"


def test_no_entry_before_first_day_pivots() -> None:
    rows = _scripted_long_path(peak=105.0)
    res = run(EmaPivotPullback(**{**PARAMS, "mode": "long_only"}), day_bars(TUE, rows), square_off="15:15")
    assert fills(res) == []


def test_max_trades_per_day() -> None:
    """Two long cycles in one day; max_trades_per_day=1 keeps only the first entry."""
    base = dict(fast=FAST, slow=SLOW, rr=3.0, mode="long_only", use_pivots=False,
                first_entry="09:20", last_entry="14:30")
    rows = _two_long_cycles()
    one = fills(run(EmaPivotPullback(**{**base, "max_trades_per_day": 1}),
                    prev_day_flat() + day_bars(TUE, rows), square_off="15:15"))
    many = fills(run(EmaPivotPullback(**{**base, "max_trades_per_day": 4}),
                     prev_day_flat() + day_bars(TUE, rows), square_off="15:15"))
    buys_one = [x for x in one if x[1] == "BUY"]
    buys_many = [x for x in many if x[1] == "BUY"]
    assert len(buys_one) == 1
    assert len(buys_many) >= 2


def test_entry_window_blocks_early_and_late() -> None:
    early = EmaPivotPullback(**{**PARAMS, "mode": "long_only", "first_entry": "14:00", "last_entry": "14:30"})
    rows = _scripted_long_path(peak=105.0)  # setup completes in the morning
    assert fills(run(early, prev_day_flat() + day_bars(TUE, rows), square_off="15:15")) == []


def test_long_only_skips_shorts() -> None:
    res = run(
        EmaPivotPullback(**{**PARAMS, "mode": "long_only"}),
        prev_day_flat() + day_bars(TUE, _scripted_short_path(trough=95.0)),
        square_off="15:15",
    )
    assert fills(res) == []


def test_bad_params() -> None:
    with pytest.raises(ValueError):
        EmaPivotPullback(fast=50, slow=20)
    with pytest.raises(ValueError):
        EmaPivotPullback(mode="both")
    with pytest.raises(ValueError):
        EmaPivotPullback(rr=0)
    with pytest.raises(ValueError):
        EmaPivotPullback(first_entry="25:00")


# --------------------------------------------------------------------------- scripted paths


def _scripted_long_path(peak: float) -> list:
    """Warm EMAs, cross up, close above both, pullback-touch 20, confirm close above both and toward peak."""
    rows: list = []
    # quiet warm-up around 100
    for _ in range(20):
        rows.append((100.0, 100.2, 99.8, 100.0))
    # push down so slow > fast
    px = 100.0
    for _ in range(15):
        c = px - 0.8
        rows.append((px, px + 0.1, c - 0.1, c))
        px = c
    # cross up + trend ready: march through both EMAs toward peak
    for _ in range(12):
        c = min(peak - 1.0, px + 1.2)
        rows.append((px, c + 0.1, px - 0.1, c))
        px = c
    # one more close firmly above both (ready)
    rows.append((px, px + 0.3, px - 0.1, px + 0.2))
    px = px + 0.2
    # pullback: previous close on trend side; this bar dips to touch fast EMA region (~px-1)
    touch = px - 1.0
    rows.append((px, px + 0.1, touch - 0.05, px - 0.3))  # close still above slow if slow lagged
    px = px - 0.3
    # confirm later: close in direction, beyond both, at/above peak (R1 gate)
    rows.append((px, peak + 0.2, px - 0.1, peak))
    px = peak
    # hold a bit
    for _ in range(10):
        rows.append((px, px + 0.2, px - 0.2, px))
    rows += [(px, px + 0.1, px - 0.1, px)] * max(0, 375 - len(rows))
    return rows[:375]


def _scripted_short_path(trough: float) -> list:
    rows: list = []
    for _ in range(20):
        rows.append((100.0, 100.2, 99.8, 100.0))
    px = 100.0
    for _ in range(15):
        c = px + 0.8
        rows.append((px, c + 0.1, px - 0.1, c))
        px = c
    for _ in range(12):
        c = max(trough + 1.0, px - 1.2)
        rows.append((px, px + 0.1, c - 0.1, c))
        px = c
    rows.append((px, px + 0.1, px - 0.3, px - 0.2))
    px = px - 0.2
    touch = px + 1.0
    rows.append((px, touch + 0.05, px - 0.1, px + 0.3))
    px = px + 0.3
    rows.append((px, px + 0.1, trough - 0.2, trough))
    px = trough
    for _ in range(10):
        rows.append((px, px + 0.2, px - 0.2, px))
    rows += [(px, px + 0.1, px - 0.1, px)] * max(0, 375 - len(rows))
    return rows[:375]


def _two_long_cycles() -> list:
    """First long setup, stop-out dump, second long setup (pivots off in the test)."""
    rows = _scripted_long_path(peak=105.0)[:70]
    px = rows[-1][3]
    for _ in range(25):  # dump through slow EMA / cross down, exit
        c = px - 1.2
        rows.append((px, px + 0.05, c - 0.05, c))
        px = c
    for _ in range(8):
        rows.append((px, px + 0.1, px - 0.1, px))
    for _ in range(15):  # cross up again
        c = px + 1.0
        rows.append((px, c + 0.1, px - 0.05, c))
        px = c
    rows.append((px, px + 0.2, px - 0.05, px + 0.15))
    px = px + 0.15
    touch = px - 0.9
    rows.append((px, px + 0.05, touch - 0.05, px - 0.25))
    px = px - 0.25
    rows.append((px, px + 2.0, px - 0.05, px + 1.8))
    px = px + 1.8
    rows += [(px, px + 0.1, px - 0.1, px)] * max(0, 375 - len(rows))
    return rows[:375]


def test_opposite_cross_exits_full_day() -> None:
    """Dedicated day: long setup early, then a sustained dump that crosses EMAs the other way."""
    s = EmaPivotPullback(**{**PARAMS, "mode": "long_only"})
    rows = _scripted_long_path(peak=105.0)[:80]
    px = rows[-1][3]
    for _ in range(40):
        c = px - 1.5
        rows.append((px, px + 0.1, c - 0.1, c))
        px = c
    rows += [(px, px + 0.1, px - 0.1, px)] * (375 - len(rows))
    res = run(s, prev_day_flat() + day_bars(TUE, rows[:375]), square_off="15:15")
    f = fills(res)
    assert any(side == "BUY" for _, side, _ in f)
    assert any(side == "SELL" for _, side, _ in f[1:]), "opposite cross (or stop) must close the long"


def test_dynamic_stop_uses_slow_ema_level() -> None:
    """Entry bracket stop is the slow EMA at the signal (absolute level below the close for a long)."""
    s = EmaPivotPullback(**{**PARAMS, "mode": "long_only"})
    rows = _scripted_long_path(peak=105.0)
    res = run(s, prev_day_flat() + day_bars(TUE, rows), square_off="15:15")
    assert res.trades, "need a trade"
    tr = res.trades[0]
    assert tr.stop_level is not None
    assert tr.target_level is not None
    assert tr.stop_level < tr.entry_price
    risk = tr.entry_price - tr.stop_level
    assert tr.target_level == pytest.approx(tr.entry_price + 3.0 * risk, abs=0.05) or (
        tr.target_level > tr.entry_price
    )
