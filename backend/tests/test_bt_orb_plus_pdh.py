"""Options candidates: orb_plus (opening range breakout + rr target + day filters) and pdh_pdl_breakout.

All by hand on synthetic one-minute sessions; the strategies see only closed bars."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.backtest.engine import run_backtest
from app.backtest.sources import ListSource
from app.strategies.daily_levels import DailyTracker
from app.strategies.orb_plus import OrbPlus
from app.strategies.pdh_pdl import PdhPdlBreakout
from tests.bt_helpers import OHLC, cfg, fills, full_day, kinds, minute_index


def weekdays(n: int, start: date = date(2026, 1, 5)) -> list[tuple[int, int, int]]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.year, d.month, d.day))
        d += timedelta(days=1)
    return out


def session(day: tuple[int, int, int], price: float, overrides: dict[int, OHLC] | None = None) -> list:
    return full_day(day, price, overrides=overrides)


def run(strategy, candles, tf: str = "5m"):  # noqa: ANN001, ANN201
    return run_backtest(strategy, ListSource(candles, 1), cfg(timeframe=tf, square_off="15:15"))


RANGE = {minute_index(9, 15): (100, 102, 98, 100)}


def hold(price: float, start: tuple[int, int], end: tuple[int, int]) -> dict[int, OHLC]:
    """Flat bars at `price` from `start` to `end` (inclusive), so price stays where a move left it."""
    return {i: (price, price, price, price) for i in range(minute_index(*start), minute_index(*end) + 1)}  # opening range 98-102 (09:15-09:29)


# ---------------------------------------------------------------- orb_plus
def test_orb_plus_long_breakout_hits_the_1_2_target() -> None:
    (day,) = weekdays(1)
    candles = session(day, 100, {**RANGE, minute_index(9, 40): (100, 103, 100, 102.5),
                                 minute_index(10, 0): (102.5, 111, 102, 110.5)})
    res = run(OrbPlus(rr=2.0), candles)
    assert fills(res) == [("09:40", "BUY", 102.0), ("10:00", "SELL", 110.0)]  # risk 4, target 102 + 8
    assert res.trades[0].exit_reason == "limit"


def test_orb_plus_mid_stop_halves_the_risk_and_the_target_distance() -> None:
    (day,) = weekdays(1)
    candles = session(day, 100, {**RANGE, minute_index(9, 40): (101, 103, 101, 102.5),
                                 **hold(102.5, (9, 41), (9, 59)), minute_index(10, 0): (102.5, 106.5, 102, 106)})
    res = run(OrbPlus(rr=2.0, stop_mode="mid"), candles)
    assert fills(res) == [("09:40", "BUY", 102.0), ("10:00", "SELL", 106.0)]  # stop 100, risk 2, target 106


def test_orb_plus_rr_0_has_no_target_and_squares_off() -> None:
    (day,) = weekdays(1)
    candles = session(day, 100, {**RANGE, minute_index(9, 40): (100, 103, 100, 102.5)})
    res = run(OrbPlus(rr=0), candles)
    assert res.trades[0].exit_reason == "square_off"


def test_orb_plus_short_side_and_stop_at_the_range_high() -> None:
    (day,) = weekdays(1)
    candles = session(day, 100, {**RANGE, minute_index(9, 40): (100, 100, 97, 97.5),
                                 minute_index(10, 0): (97.5, 102.5, 97.5, 102.5)})
    res = run(OrbPlus(rr=2.0), candles)
    assert fills(res) == [("09:40", "SELL", 98.0), ("10:00", "BUY", 102.0)]
    assert res.trades[0].exit_reason == "stop"


def test_orb_plus_no_entry_after_14_50() -> None:
    (day,) = weekdays(1)
    candles = session(day, 100, {**RANGE, minute_index(15, 0): (100, 103, 100, 103)})
    res = run(OrbPlus(rr=2.0), candles)
    assert res.trades == []
    assert {c["tag"] for c in kinds(res, "order_cancelled")} >= {"orbp_long", "orbp_short"}


def test_orb_plus_cpr_bias_takes_only_the_side_of_the_open() -> None:
    d1, d2 = weekdays(2)
    # day 1: H 110, L 90, C 100 -> P = BC = TC = 100. Day 2 opens 105, above the CPR: long side only.
    day1 = session(d1, 100, {0: (100, 110, 90, 100)})
    day2 = session(d2, 105, {0: (105, 107, 103, 105), minute_index(9, 40): (105, 105, 102, 102)})
    res = run(OrbPlus(rr=2.0, bias="cpr"), day1 + day2)
    placed = [e["tag"] for e in kinds(res, "order_placed") if e["tag"].startswith("orbp_")]
    assert "orbp_short" not in placed and "orbp_long" in placed
    assert res.trades == []  # the downside break is not traded


def test_orb_plus_cpr_bias_skips_an_open_inside_the_cpr() -> None:
    d1, d2 = weekdays(2)
    day1 = session(d1, 110, {0: (100, 110, 90, 110)})  # C at the high: BC 100, TC 106.67
    day2 = session(d2, 103, {0: (103, 105, 101, 103), minute_index(9, 40): (103, 106, 103, 106)})
    assert run(OrbPlus(rr=2.0, bias="cpr"), day1 + day2).trades == []


def _nr_days(last_range: float) -> list:
    days = weekdays(5)
    out = []
    for i, d in enumerate(days[:4]):
        r = last_range if i == 3 else 10.0
        out += session(d, 100, {0: (100, 100 + r / 2, 100 - r / 2, 100)})
    out += session(days[4], 100, {**RANGE, minute_index(9, 40): (100, 103, 100, 102.5)})
    return out


def test_orb_plus_nr4_trades_only_after_the_narrowest_of_four_days() -> None:
    assert len(run(OrbPlus(prior_day="nr4"), _nr_days(4.0)).trades) == 1
    assert run(OrbPlus(prior_day="nr4"), _nr_days(12.0)).trades == []


def _cpr_days(last_close: float) -> list:
    days = weekdays(23)
    out = []
    for d in days[:21]:  # 21 days closing at their high: CPR width (H - L) / 3 = 20 / 3
        out += session(d, 110, {0: (100, 110, 90, 110)})
    out += session(days[21], 110, {0: (100, 110, 90, 110), 374: (last_close, last_close, last_close, last_close)})
    out += session(days[22], 100, {**RANGE, minute_index(9, 40): (100, 103, 100, 102.5)})
    return out


def test_orb_plus_narrow_cpr_filter() -> None:
    assert len(run(OrbPlus(cpr="narrow", cpr_max_ratio=0.5), _cpr_days(100.0)).trades) == 1  # close at mid: width 0
    assert run(OrbPlus(cpr="narrow", cpr_max_ratio=0.5), _cpr_days(110.0)).trades == []  # same width as before


def test_orb_plus_range_atr_max_skips_a_wide_opening_range() -> None:
    days = weekdays(16)
    candles = []
    for d in days[:15]:
        candles += session(d, 100, {0: (100, 105, 95, 100)})  # true range 10 a day: ATR 10
    candles += session(days[15], 100, {0: (100, 104, 96, 100), minute_index(9, 40): (100, 105, 100, 104.5)})
    assert len(run(OrbPlus(range_atr_max=1.0), candles).trades) == 1  # range 8 <= 10
    assert run(OrbPlus(range_atr_max=0.5), candles).trades == []  # range 8 > 5


def test_orb_plus_validates_parameters() -> None:
    for bad in ({"rr": 0.5}, {"rr": -1}, {"stop_mode": "x"}, {"prior_day": "nr5"}, {"cpr": "wide"},
                {"bias": "ema"}, {"range_minutes": 0}, {"lots": 0}, {"range_atr_max": -1}):
        with pytest.raises(ValueError):
            OrbPlus(**bad)


# ---------------------------------------------------------------- pdh_pdl_breakout
def _atr_days(n: int = 15) -> tuple[list, list]:
    days = weekdays(n + 1)
    candles = []
    for d in days[:n]:
        candles += session(d, 100, {0: (100, 105, 95, 100)})  # H 105, L 95, C 100, ATR 10
    return days, candles


def test_pdh_long_breakout_with_atr_stop_and_1_2_target() -> None:
    days, candles = _atr_days()
    candles += session(days[-1], 100, {minute_index(10, 0): (104.5, 105.5, 104.5, 105.5),
                                       **hold(105.5, (10, 1), (10, 29)), minute_index(10, 30): (105.5, 109.5, 105, 109)})
    res = run(PdhPdlBreakout(rr=2.0, stop_atr=0.2), candles)
    assert fills(res)[-2:] == [("10:00", "BUY", 105.0), ("10:30", "SELL", 109.0)]  # risk 2, target 109


def test_pdl_short_breakout_stopped() -> None:
    days, candles = _atr_days()
    candles += session(days[-1], 100, {minute_index(10, 0): (95.5, 95.5, 94.5, 94.5),
                                       **hold(94.5, (10, 1), (10, 29)), minute_index(10, 30): (94.5, 97.5, 94.5, 97)})
    res = run(PdhPdlBreakout(rr=2.0, stop_atr=0.2), candles)
    trade = res.trades[-1]
    assert (trade.direction, trade.entry_price, trade.exit_price, trade.exit_reason) == ("SHORT", 95.0, 97.0, "stop")


def test_pdh_side_skipped_when_the_open_gapped_through_it() -> None:
    days, candles = _atr_days()
    candles += session(days[-1], 107, {minute_index(10, 0): (107, 110, 107, 110)})
    res = run(PdhPdlBreakout(), candles)
    assert not [e for e in kinds(res, "order_placed") if e["tag"] == "pdh_long"]


def test_pdh_pdl_no_entry_before_atr_history() -> None:
    days, candles = _atr_days(5)
    candles += session(days[-1], 100, {minute_index(10, 0): (100, 106, 100, 106)})
    assert run(PdhPdlBreakout(), candles).trades == []


def test_pdh_pdl_entry_cancelled_at_14_50() -> None:
    days, candles = _atr_days()
    candles += session(days[-1], 100, {minute_index(15, 0): (100, 106, 100, 106)})
    assert run(PdhPdlBreakout(), candles).trades == []


def test_pdh_pdl_validates_parameters() -> None:
    for bad in ({"rr": 0}, {"stop_atr": 0}, {"bias": "x"}, {"wait_minutes": 0}, {"lots": 0}):
        with pytest.raises(ValueError):
            PdhPdlBreakout(**bad)


# ---------------------------------------------------------------- daily levels
def test_daily_tracker_levels_use_finished_days_only() -> None:
    d1, d2 = weekdays(2)
    tracker = DailyTracker()
    for bar in session(d1, 100, {0: (100, 110, 90, 105)}):
        tracker.update(bar)
    assert tracker.prev is None  # day 1 is not finished while its bars arrive
    first = session(d2, 101)[0]
    tracker.update(first)
    assert tracker.prev is not None and (tracker.prev.high, tracker.prev.low, tracker.prev.close) == (110, 90, 100)
    bc, p, tc = tracker.cpr()
    assert (bc, round(p, 4), round(tc, 4)) == (100.0, 100.0, 100.0)
    assert tracker.today_open == 101


def test_pdh_pdl_decisions_before_a_cut_ignore_every_bar_after_it() -> None:
    """Look-ahead check with daily levels: replace every bar from Friday 09:15 on; nothing before it may change.

    Strict `<` on the cut: an order that fills in the cut minute is not a decision taken before it."""
    from tests.bt_helpers import FRI, ist, random_days
    from tests.bt_helpers import MON, THU, TUE, WED

    days = [MON, TUE, WED, THU, FRI]
    cut = ist(*FRI, 9, 15)
    a = random_days(days, seed=7, start_price=22000.0, vol=6.0)
    alt = {c["time"]: c for c in random_days(days, seed=99, start_price=23500.0, vol=40.0)}
    b = [alt[c["time"]] if c["time"] >= cut else c for c in a]

    def before(candles):  # noqa: ANN001, ANN202
        res = run(PdhPdlBreakout(rr=2.0, stop_atr=0.3, atr_length=1), candles)
        return [e for e in res.events if e["t"] < cut]

    ea, eb = before(a), before(b)
    assert ea == eb
    assert len([e for e in ea if e["kind"] == "fill"]) >= 2  # it really traded before the cut
