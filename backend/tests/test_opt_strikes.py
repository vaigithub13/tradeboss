"""Strike grid (cases 2–4)."""

from __future__ import annotations

from datetime import date

import pytest

from app.options.strikes import (
    StrikeStepTable,
    StrikeStepUnknown,
    atm_strike,
    load_default_step_table,
    pick_strike,
)


def test_2_atm_is_nearest_step_and_ties_round_up() -> None:
    assert atm_strike(22324.9, 50) == 22300.0
    assert atm_strike(22325.0, 50) == 22350.0
    assert atm_strike(22300.0, 50) == 22300.0
    assert atm_strike(25010.0, 50) == 25000.0
    assert atm_strike(25025.0, 50) == 25050.0


def test_3_offset_plus_one_is_otm_minus_one_is_itm() -> None:
    assert pick_strike("CE", 25010, 50, 0) == 25000.0
    assert pick_strike("CE", 25010, 50, 1) == 25050.0
    assert pick_strike("CE", 25010, 50, -1) == 24950.0
    assert pick_strike("PE", 25010, 50, 0) == 25000.0
    assert pick_strike("PE", 25010, 50, 1) == 24950.0
    assert pick_strike("PE", 25010, 50, -1) == 25050.0
    with pytest.raises(ValueError):
        pick_strike("CE", 25000, 50, 2)
    with pytest.raises(ValueError):
        pick_strike("XX", 25000, 50, 0)


def test_4_step_before_the_table_is_refused_and_the_seeded_row_is_unverified() -> None:
    table = load_default_step_table()
    row = table.row_for("NIFTY", date(2026, 10, 5))
    assert row.step == 50 and row.verification == "unverified"
    with pytest.raises(StrikeStepUnknown):
        table.step("NIFTY", date(2021, 12, 31))
    empty = StrikeStepTable([])
    with pytest.raises(StrikeStepUnknown):
        empty.step("NIFTY", date(2026, 1, 1))
