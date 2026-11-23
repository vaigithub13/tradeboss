"""Expiry-day 09:20 ATM short straddle paper slot (app/paper/expiry_straddle.py). Pure logic, no data."""

from __future__ import annotations

import pytest

from app.paper.expiry_straddle import (
    ENTRY_LAST_MS, ENTRY_MS, EXIT_MS, ExpiryStraddle, atm_strike,
)

M = 60_000
T0920 = ENTRY_MS
T1000 = (10 * 60) * M


def slot(**kw) -> ExpiryStraddle:
    return ExpiryStraddle(day="2026-12-01", is_expiry=kw.pop("is_expiry", True), units=65, **kw)


def flat_cost(side: str, price: float, units: int) -> float:
    return 25.0  # synthetic: Rs 25 per fill


@pytest.mark.parametrize("spot,strike", [(25124.9, 25100), (25125.0, 25150), (25149.0, 25150), (25075.0, 25100)])
def test_atm_rounding(spot, strike):
    assert atm_strike(spot) == strike


def test_no_trade_on_non_expiry_day():
    s = slot(is_expiry=False)
    assert s.status == "no_trade"
    assert s.enter(T0920, 25100, 100, 100) is False
    assert s.legs == []


def test_entry_sells_both_legs_with_slip_and_stops():
    s = slot()
    assert s.enter(T0920, 25112, 120.0, 100.0, "CEKEY", "PEKEY")
    ce, pe = s.legs
    assert (ce.kind, ce.strike, ce.entry, ce.stop) == ("CE", 25100, 119.5, round(119.5 * 1.3, 2))
    assert (pe.kind, pe.entry, pe.stop) == ("PE", 99.5, round(99.5 * 1.3, 2))
    assert s.status == "open"


def test_no_entry_before_0920_and_none_after_window():
    s = slot()
    assert s.enter(T0920 - M, 25100, 100, 100) is False and s.status == "waiting"
    late = slot()
    assert late.enter(ENTRY_LAST_MS + 1, 25100, 100, 100) is False
    assert late.status == "missed_entry"


def test_entry_only_once():
    s = slot()
    assert s.enter(T0920, 25100, 100, 100)
    assert s.enter(T0920 + 30_000, 25100, 100, 100) is False
    assert len(s.legs) == 2


def test_stop_triggers_at_exactly_1_30x_not_below():
    s = slot(); s.enter(T0920, 25100, 100.5, 100.5)  # entry 100.0 -> stop 130.0
    assert s.on_quote(T1000, "CE", 129.99) is False
    assert s.on_quote(T1000, "CE", 130.0) is True
    ce = s.legs[0]
    assert (ce.exit, ce.reason) == (130.5, "stop")
    assert s.legs[1].open and s.status == "open"


def test_no_reentry_after_stop():
    s = slot(); s.enter(T0920, 25100, 100.5, 100.5)
    s.on_quote(T1000, "CE", 140)
    assert s.on_quote(T1000 + M, "CE", 200) is False
    assert s.legs[0].exit == 140.5


def test_time_exit_at_1515_closes_open_legs():
    s = slot(); s.enter(T0920, 25100, 100.5, 100.5)
    s.close_all(EXIT_MS - 1, {"CE": 50, "PE": 60})  # too early: nothing happens
    assert all(l.open for l in s.legs)
    s.close_all(EXIT_MS, {"CE": 50, "PE": 60})
    assert [(l.exit, l.reason) for l in s.legs] == [(50.5, "time"), (60.5, "time")]
    assert s.status == "done"


def test_quote_after_1515_is_a_time_exit_not_a_stop():
    s = slot(); s.enter(T0920, 25100, 100.5, 100.5)
    s.on_quote(EXIT_MS, "CE", 500)
    assert s.legs[0].reason == "time"


def test_summary_pnl_and_costs():
    s = slot(); s.enter(T0920, 25100, 100.5, 100.5)   # both entries 100.0
    s.on_quote(T1000, "CE", 130)                      # CE out 130.5 -> -30.5 pts
    s.close_all(EXIT_MS, {"PE": 20})                  # PE out 20.5  -> +79.5 pts
    r = s.summary(flat_cost)
    assert r["gross_rs"] == round((-30.5 + 79.5) * 65, 2)
    assert r["costs_rs"] == 100.0
    assert r["net_rs"] == round(49 * 65 - 100, 2)
    assert r["stops_hit"] == 1 and r["status"] == "done"


def test_round_trip_state_resumes_after_restart():
    s = slot(); s.enter(T0920, 25100, 100.5, 100.5); s.on_quote(T1000, "PE", 131)
    r = ExpiryStraddle.from_dict(s.to_dict())
    assert r.to_dict() == s.to_dict()
    assert r.on_quote(T1000 + M, "PE", 300) is False   # already stopped
    assert r.on_quote(T1000 + M, "CE", 131) is True


def test_bad_inputs():
    with pytest.raises(ValueError):
        ExpiryStraddle(day="2026-12-01", is_expiry=True, units=0)
    s = slot(); s.enter(T0920, 25100, 100, 100)
    with pytest.raises(ValueError):
        s.on_quote(T1000, "XX", 10)
    assert slot().enter(T0920, 25100, 0, 100) is False
