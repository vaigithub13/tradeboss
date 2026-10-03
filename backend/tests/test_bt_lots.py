"""Lot size by date (8).

The table here is a TEST FIXTURE shaped like NSE's published Nifty revisions. The shipped table stays
empty until the circular list is approved (see PROGRESS.md)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.backtest.lots import LotSizeAmbiguous, LotSizeTable, LotSizeUnknown, load_default_lot_table
from tests.bt_helpers import MON, Scripted, buy, day_bars, exit_, flat, ist, run

ROWS = [
    {"underlying": "NIFTY", "lot_size": 50, "effective_from": "2021-04-30", "source": "fixture"},
    {"underlying": "NIFTY", "lot_size": 25, "effective_from": "2024-04-26", "first_weekly_expiry": "2024-05-02",
     "first_monthly_expiry": "2024-05-30", "source": "fixture"},
    {"underlying": "NIFTY", "lot_size": 75, "effective_from": "2024-11-21", "first_weekly_expiry": "2025-01-02",
     "first_monthly_expiry": "2025-02-27", "source": "fixture"},
    {"underlying": "NIFTY", "lot_size": 65, "effective_from": "2025-10-29", "first_weekly_expiry": "2026-01-06",
     "first_monthly_expiry": "2026-01-27", "source": "fixture"},
]
TABLE = LotSizeTable.from_dict({"rows": ROWS})


def test_8a_lookup_by_trade_date_outside_the_transition_windows() -> None:
    assert TABLE.lot_size("NIFTY", date(2023, 6, 1)) == 50
    assert TABLE.lot_size("NIFTY", date(2024, 6, 15)) == 25
    assert TABLE.lot_size("NIFTY", date(2025, 6, 2)) == 75
    assert TABLE.lot_size("NIFTY", date(2026, 2, 10)) == 65
    assert TABLE.lot_size("NIFTY", date(2026, 10, 3)) == 65


def test_8a_effective_date_is_inclusive_and_the_old_lot_holds_the_day_before() -> None:
    assert TABLE.lot_size("NIFTY", date(2024, 4, 25)) == 50
    with pytest.raises(LotSizeAmbiguous):  # 2024-04-26: new-lot contracts start, old-lot ones still trade
        TABLE.lot_size("NIFTY", date(2024, 4, 26))
    assert TABLE.lot_size("NIFTY", date(2024, 5, 30)) == 25  # first monthly with the new lot: window over


def test_8a_inside_a_transition_window_both_lots_trade_so_a_date_alone_is_ambiguous() -> None:
    for d in (date(2024, 4, 30), date(2024, 12, 5), date(2025, 1, 15), date(2025, 11, 15), date(2026, 1, 20)):
        with pytest.raises(LotSizeAmbiguous) as e:
            TABLE.lot_size("NIFTY", d)
        assert "expiry" in str(e.value)


def test_8b_a_known_contract_resolves_the_window_exactly() -> None:
    assert TABLE.lot_size("NIFTY", date(2024, 12, 5), expiry=date(2024, 12, 12), cycle="weekly") == 25
    assert TABLE.lot_size("NIFTY", date(2024, 12, 5), expiry=date(2025, 1, 2), cycle="weekly") == 75
    assert TABLE.lot_size("NIFTY", date(2025, 1, 15), expiry=date(2025, 1, 30), cycle="monthly") == 25
    assert TABLE.lot_size("NIFTY", date(2025, 1, 15), expiry=date(2025, 2, 27), cycle="monthly") == 75
    assert TABLE.lot_size("NIFTY", date(2026, 1, 20), expiry=date(2026, 1, 27), cycle="monthly") == 65
    assert TABLE.lot_size("NIFTY", date(2026, 1, 2), expiry=date(2026, 1, 6), cycle="weekly") == 65
    assert TABLE.lot_size("NIFTY", date(2025, 12, 20), expiry=date(2025, 12, 23), cycle="weekly") == 75


def test_8c_before_the_first_row_or_for_an_unknown_underlying_it_refuses_to_guess() -> None:
    with pytest.raises(LotSizeUnknown):
        TABLE.lot_size("NIFTY", date(2021, 1, 1))
    with pytest.raises(LotSizeUnknown):
        TABLE.lot_size("BANKNIFTY", date(2025, 1, 1))


def test_8c_table_validation() -> None:
    with pytest.raises(ValueError):
        LotSizeTable.from_dict({"rows": [{**ROWS[0], "lot_size": 0}]})
    with pytest.raises(ValueError):
        LotSizeTable.from_dict({"rows": [ROWS[0], ROWS[0]]})  # duplicate effective_from
    with pytest.raises(ValueError):
        LotSizeTable.from_dict({"rows": [{**ROWS[0], "effective_from": "05-01-2021"}]})
    with pytest.raises(ValueError):
        LotSizeTable.from_dict({"rows": [{**ROWS[1], "first_weekly_expiry": None}]})  # a revision must say when expiries switch


SHIPPED = load_default_lot_table()


def lot(expiry: tuple[int, int, int], cycle: str, on: tuple[int, int, int] = (2026, 10, 1)) -> int:
    return SHIPPED.lot_size("NIFTY", date(*on), expiry=date(*expiry), cycle=cycle)


def test_8d_the_shipped_table_is_seeded_from_the_four_nse_circulars() -> None:
    rows = [r for r in SHIPPED.rows if r.underlying == "NIFTY"]
    assert [(r.lot_size, r.effective_from.isoformat()) for r in rows] == [
        (50, "2021-04-30"), (25, "2024-04-26"), (75, "2024-11-20"), (65, "2025-10-29"),
    ]
    assert [(r.first_weekly_expiry, r.first_monthly_expiry) for r in rows[1:]] == [
        (date(2024, 5, 2), date(2024, 5, 30)),
        (date(2025, 1, 2), date(2025, 2, 27)),
        (date(2026, 1, 6), date(2026, 1, 27)),
    ]
    for r, circular in zip(rows, ["47854", "61415", "64625", "70616"], strict=True):
        assert f"NSE/FAOP/{circular}" in r.source


def test_8d_changeover_april_2024_50_to_25() -> None:
    assert lot((2024, 4, 18), "weekly", (2024, 4, 10)) == 50
    assert lot((2024, 4, 25), "monthly", (2024, 4, 10)) == 50  # the April monthly keeps 50
    assert lot((2024, 5, 2), "weekly", (2024, 4, 26)) == 25  # first weekly with the new lot
    assert lot((2024, 5, 30), "monthly", (2024, 4, 26)) == 25  # first monthly with the new lot
    assert lot((2024, 5, 9), "weekly", (2024, 5, 3)) == 25
    # a date alone cannot say which lot: both exist until the first new-lot monthly expires
    for d in (date(2024, 4, 26), date(2024, 5, 10), date(2024, 5, 29)):
        with pytest.raises(LotSizeAmbiguous):
            SHIPPED.lot_size("NIFTY", d)
    assert SHIPPED.lot_size("NIFTY", date(2024, 4, 25)) == 50
    assert SHIPPED.lot_size("NIFTY", date(2024, 5, 30)) == 25


def test_8d_changeover_nov_2024_to_feb_2025_25_to_75_weeklies_and_monthlies_differ() -> None:
    d = (2024, 12, 10)
    assert lot((2024, 12, 12), "weekly", d) == 25
    assert lot((2024, 12, 19), "weekly", d) == 25  # last old-lot weekly
    assert lot((2025, 1, 2), "weekly", d) == 75  # first new-lot weekly
    assert lot((2024, 12, 26), "monthly", d) == 25
    # January 2025 (the example that matters): weekly 75 while the 30 Jan monthly is still 25
    jan = (2025, 1, 15)
    assert lot((2025, 1, 23), "weekly", jan) == 75
    assert lot((2025, 1, 30), "monthly", jan) == 25
    assert lot((2025, 2, 27), "monthly", jan) == 75  # first new-lot monthly
    assert lot((2025, 3, 27), "monthly", jan) == 75  # the quarterly follows the monthly rule
    for day in (date(2024, 11, 20), date(2024, 12, 10), date(2025, 1, 15), date(2025, 2, 26)):
        with pytest.raises(LotSizeAmbiguous):
            SHIPPED.lot_size("NIFTY", day)
    assert SHIPPED.lot_size("NIFTY", date(2025, 3, 3)) == 75


def test_8d_changeover_nov_2025_to_jan_2026_75_to_65() -> None:
    d = (2025, 12, 1)
    assert lot((2025, 12, 23), "weekly", d) == 75  # last old-lot weekly
    assert lot((2026, 1, 6), "weekly", d) == 65  # first new-lot weekly
    assert lot((2025, 12, 30), "monthly", d) == 75  # last old-lot monthly
    assert lot((2026, 1, 27), "monthly", d) == 65  # first new-lot monthly
    assert lot((2026, 3, 31), "monthly", d) == 65  # quarterly
    for day in (date(2025, 10, 29), date(2025, 12, 15), date(2026, 1, 20)):
        with pytest.raises(LotSizeAmbiguous):
            SHIPPED.lot_size("NIFTY", day)
    assert SHIPPED.lot_size("NIFTY", date(2026, 2, 3)) == 65
    assert SHIPPED.lot_size("NIFTY", date(2023, 6, 1)) == 50


def test_8d_the_shipped_table_refuses_dates_before_it_starts_and_unseeded_underlyings() -> None:
    with pytest.raises(LotSizeUnknown):
        SHIPPED.lot_size("NIFTY", date(2021, 4, 29))
    with pytest.raises(LotSizeUnknown):
        SHIPPED.lot_size("BANKNIFTY", date(2025, 1, 1))


def test_8e_engine_with_the_shipped_table_uses_the_contract_lot_in_a_changeover() -> None:
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (101, 101, 101, 101), (101, 101, 101, 101)]
    plan = {0: [buy()], 2: [exit_()]}

    def lots_for(expiry: date, cycle: str) -> int:
        res = run(Scripted(plan), day_bars((2025, 1, 15), rows), lot_size=None, lot_table=SHIPPED, underlying="NIFTY",
                  contract=lambda d: (expiry, cycle))
        return res.trades[0].lot_size

    assert lots_for(date(2025, 1, 23), "weekly") == 75
    assert lots_for(date(2025, 1, 30), "monthly") == 25
    with pytest.raises(LotSizeAmbiguous):  # no contract given inside the window
        run(Scripted(plan), day_bars((2025, 1, 15), rows), lot_size=None, lot_table=SHIPPED, underlying="NIFTY")


def test_8d_fixture_agrees_with_todays_instrument_snapshot_if_present() -> None:
    from app.upstox.instruments import load_instruments

    snap = Path(__file__).resolve().parents[2] / "data" / "instruments" / "2026-10-03" / "NSE.json.gz"
    if not snap.exists():
        pytest.skip("no instrument snapshot on this machine")
    lots = {i.lot_size for i in load_instruments(snap) if i.kind == "option"}
    assert lots == {TABLE.lot_size("NIFTY", date(2026, 10, 3))} == {SHIPPED.lot_size("NIFTY", date(2026, 10, 3))}
    # and every listed contract resolves, by its own expiry, to the lot the exchange lists
    for i in load_instruments(snap):
        if i.kind == "option" and i.expiry is not None:
            assert SHIPPED.lot_size("NIFTY", date(2026, 10, 3), expiry=i.expiry, cycle="weekly") == i.lot_size


def test_8e_engine_uses_the_lot_size_of_the_trade_date() -> None:
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (101, 101, 101, 101), (101, 101, 101, 101)]
    plan = {0: [buy()], 2: [exit_()]}

    def pnl(day: tuple[int, int, int]) -> tuple[int, float]:
        res = run(Scripted(plan), day_bars(day, rows), lot_size=None, lot_table=TABLE, underlying="NIFTY")
        return res.trades[0].lot_size, res.trades[0].net_pnl

    assert pnl((2024, 6, 3)) == (25, 25.0)
    assert pnl((2025, 6, 2)) == (75, 75.0)
    assert pnl((2026, 2, 9)) == (65, 65.0)


def test_8e_engine_refuses_an_ambiguous_date_unless_the_contract_is_given() -> None:
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (101, 101, 101, 101), (101, 101, 101, 101)]
    plan = {0: [buy()], 2: [exit_()]}
    with pytest.raises(LotSizeAmbiguous):
        run(Scripted(plan), day_bars((2024, 12, 5), rows), lot_size=None, lot_table=TABLE, underlying="NIFTY")
    old = run(Scripted(plan), day_bars((2024, 12, 5), rows), lot_size=None, lot_table=TABLE, underlying="NIFTY",
              contract=lambda d: (date(2024, 12, 12), "weekly"))
    new = run(Scripted(plan), day_bars((2024, 12, 5), rows), lot_size=None, lot_table=TABLE, underlying="NIFTY",
              contract=lambda d: (date(2025, 1, 2), "weekly"))
    assert (old.trades[0].lot_size, new.trades[0].lot_size) == (25, 75)


def test_8e_engine_needs_some_lot_size_source_and_a_fixed_override_works() -> None:
    from app.backtest.engine import ConfigError

    with pytest.raises(ConfigError):
        run(Scripted(), day_bars(MON, flat(100, 3)), lot_size=None)
    fixed = run(Scripted({0: [buy(2)], 2: [exit_()]}), day_bars(MON, [(100, 100, 100, 100)] * 2 + [(101, 101, 101, 101)] * 2), lot_size=10)
    assert fixed.trades[0].units == 20 and fixed.trades[0].lots == 2


def test_8e_units_are_fixed_at_entry_and_both_legs_use_them() -> None:
    # entered on 2024-12-31 (lot 25 contract chosen), exited on 2025-01-02: same 25 units on both legs
    day1 = day_bars((2024, 12, 31), flat(100, 3))
    day2 = day_bars((2025, 1, 2), [(101, 101, 101, 101)] * 3)
    res = run(Scripted({0: [buy()], 3: [exit_()]}, allow_overnight=True), day1 + day2, lot_size=None, lot_table=TABLE,
              underlying="NIFTY", contract=lambda d: (date(2025, 1, 30), "monthly"))
    (t,) = res.trades
    assert t.units == 25 and t.net_pnl == 25.0 and t.entry_time == ist(2024, 12, 31, 9, 16)
