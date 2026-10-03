"""Contract choice (cases 1, 5–8)."""

from __future__ import annotations

from datetime import date

import pytest

from app.options.contract import choose_contract, contract_symbol
from tests.opt_helpers import CAL, LOTS, STEPS, bar, estimate, ist, trade

D = date.fromisoformat


def pick(direction: str, spot: float, on: str, **kw):  # noqa: ANN001, ANN201
    return choose_contract(
        direction, spot, D(on), calendar=CAL, lots=LOTS, steps=STEPS, **kw,
    )


def test_1_long_is_ce_short_is_pe() -> None:
    long = pick("LONG", 25010, "2026-10-05")
    short = pick("SHORT", 25010, "2026-10-05")
    assert long.kind == "CE" and short.kind == "PE"
    assert long.strike == short.strike == 25000.0
    with pytest.raises(ValueError):
        pick("EXIT", 25010, "2026-10-05")


def test_5_nearest_expiry_holiday_shift_day_switch_and_roll() -> None:
    assert pick("LONG", 25000, "2026-10-05").expiry == D("2026-10-06")  # same week Tuesday
    assert pick("LONG", 25000, "2026-10-06").expiry == D("2026-10-06")  # used on expiry day
    assert pick("LONG", 25000, "2026-10-06", roll_on_expiry_day=True).expiry == D("2026-10-13")
    # Thursday → Tuesday
    assert pick("LONG", 25000, "2025-08-27").expiry == D("2025-08-28")
    assert pick("LONG", 25000, "2025-09-01").expiry == D("2025-09-02")
    # Eid holiday shift
    assert pick("LONG", 25000, "2024-04-09").expiry == D("2024-04-10")
    # Muhurat-only Tuesday 21 Oct 2025: weekly expired Monday 20
    assert pick("LONG", 25000, "2025-10-20").expiry == D("2025-10-20")
    assert pick("LONG", 25000, "2025-10-21").expiry == D("2025-10-28")


def test_6_lot_follows_cycle_and_expiry_not_the_trade_date() -> None:
    weekly = pick("LONG", 24000, "2025-01-15")
    monthly = pick("LONG", 24000, "2025-01-15", cycle="monthly")
    assert weekly.expiry == D("2025-01-16") and weekly.lot_size == 75 and weekly.cycle == "weekly"
    assert monthly.expiry == D("2025-01-30") and monthly.lot_size == 25 and monthly.cycle == "monthly"


def test_7_symbol_names_the_contract_and_it_is_fixed_at_entry() -> None:
    c = pick("LONG", 25010, "2026-10-05")
    assert c.symbol == contract_symbol("NIFTY", 25000, "CE", D("2026-10-06"))
    assert c.symbol == "NIFTY 25000 CE 06 OCT 26"
    # held overnight: still the Monday-chosen contract (overlay uses entry date only)
    t0, t1 = ist(2026, 10, 5, 15, 20), ist(2026, 10, 6, 10, 0)
    index = [bar(t0 - 60, 25010), bar(t0, 25010), bar(t1, 25200)]
    vix = [bar(t0, 14), bar(t1, 14)]
    out = estimate([trade(entry=(t0, 25010), exit=(t1, 25200))], index, vix)
    assert out.option.trades[0]["contract"]["expiry"] == "2026-10-06"
    assert out.option.trades[0]["contract"]["strike"] == 25000.0


def test_8_replacing_every_bar_after_the_signal_does_not_change_the_contract() -> None:
    signal = ist(2026, 10, 5, 9, 59)
    fill = ist(2026, 10, 5, 10, 0)
    exit_t = ist(2026, 10, 5, 10, 30)
    index = [bar(signal, 25010), bar(fill, 25010), bar(exit_t, 25060)]
    vix = [bar(fill, 14), bar(exit_t, 14)]
    a = estimate([trade(entry=(fill, 25010), exit=(exit_t, 25060))], index, vix)
    wild = [bar(signal, 25010), bar(fill, 99999), bar(exit_t, 1)]
    wild_vix = [bar(fill, 80), bar(exit_t, 80)]
    b = estimate([trade(entry=(fill, 25010), exit=(exit_t, 25060))], wild, wild_vix)
    assert a.option.trades[0]["contract"] == b.option.trades[0]["contract"]
