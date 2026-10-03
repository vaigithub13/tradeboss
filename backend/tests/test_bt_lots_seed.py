"""The shipped Nifty lot-size table (seeded from NSE circulars) and its three changeover windows.

A contract's lot depends on (cycle, expiry date), not on the trade date: at a changeover weeklies
and monthlies switch on different expiries."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.backtest.lots import LotSizeAmbiguous, LotSizeUnknown, load_default_lot_table, lot_size_for_contract
from tests.bt_helpers import Scripted, buy, day_bars, exit_, run

T = load_default_lot_table()


def lot(on: date, expiry: date | None = None, cycle: str | None = None) -> int:
    return T.lot_size("NIFTY", on, expiry=expiry, cycle=cycle)


def test_the_table_has_the_four_approved_rows_with_their_circulars() -> None:
    rows = [r for r in T.rows if r.underlying == "NIFTY"]
    assert [(r.lot_size, r.effective_from) for r in rows] == [
        (50, date(2021, 4, 30)),
        (25, date(2024, 4, 26)),
        (75, date(2024, 11, 20)),
        (65, date(2025, 10, 29)),
    ]
    assert [(r.first_weekly_expiry, r.first_monthly_expiry) for r in rows[1:]] == [
        (date(2024, 5, 2), date(2024, 5, 30)),
        (date(2025, 1, 2), date(2025, 2, 27)),
        (date(2026, 1, 6), date(2026, 1, 27)),
    ]
    for r, circular in zip(rows, ["NSE/FAOP/47854", "NSE/FAOP/61415", "NSE/FAOP/64625", "NSE/FAOP/70616"]):
        assert circular in r.source and "nseindia.com" in r.source


# ---------------------------------------------------------------- window 1: Apr 2024 (50 -> 25)
def test_changeover_apr_2024_by_contract() -> None:
    assert lot(date(2024, 4, 10), date(2024, 4, 25), "monthly") == 50  # the 25 Apr monthly keeps 50
    assert lot(date(2024, 4, 24), date(2024, 4, 25), "weekly") == 50
    assert lot(date(2024, 4, 30), date(2024, 5, 2), "weekly") == 25  # first weekly with the new lot
    assert lot(date(2024, 5, 3), date(2024, 5, 30), "monthly") == 25  # first monthly with the new lot
    assert lot(date(2024, 6, 10), date(2024, 6, 27), "monthly") == 25


def test_changeover_apr_2024_a_contract_that_already_exists_keeps_the_old_lot_until_the_new_one_is_in_force() -> None:
    assert lot(date(2024, 4, 20), date(2024, 5, 30), "monthly") == 50  # traded before 26 Apr 2024
    assert lot(date(2024, 4, 26), date(2024, 5, 30), "monthly") == 25


def test_changeover_apr_2024_by_date_alone() -> None:
    assert lot(date(2024, 4, 25)) == 50
    for d in (date(2024, 4, 26), date(2024, 5, 15), date(2024, 5, 29)):
        with pytest.raises(LotSizeAmbiguous):
            lot(d)
    assert lot(date(2024, 5, 30)) == 25
    assert lot(date(2024, 8, 1)) == 25


# ---------------------------------------------------------------- window 2: Nov 2024 - Feb 2025 (25 -> 75)
def test_changeover_jan_2025_weekly_and_monthly_switch_on_different_expiries() -> None:
    # the example: on the same day a January weekly has 75 while the 30 Jan monthly still has 25
    same_day = date(2025, 1, 15)
    assert lot(same_day, date(2025, 1, 16), "weekly") == 75
    assert lot(same_day, date(2025, 1, 30), "monthly") == 25
    assert lot(same_day, date(2025, 2, 27), "monthly") == 75


def test_changeover_jan_2025_the_edges_of_the_circular() -> None:
    assert lot(date(2024, 12, 10), date(2024, 12, 19), "weekly") == 25  # last weekly with 25
    assert lot(date(2024, 12, 10), date(2025, 1, 2), "weekly") == 75  # first weekly with 75
    assert lot(date(2024, 12, 10), date(2024, 12, 26), "monthly") == 25
    assert lot(date(2025, 1, 20), date(2025, 1, 30), "monthly") == 25  # last monthly with 25
    assert lot(date(2025, 1, 20), date(2025, 2, 27), "monthly") == 75  # first monthly with 75
    assert lot(date(2025, 3, 3), date(2025, 3, 6), "weekly") == 75


def test_changeover_jan_2025_by_date_alone() -> None:
    assert lot(date(2024, 11, 19)) == 25
    for d in (date(2024, 11, 20), date(2024, 12, 31), date(2025, 1, 15), date(2025, 2, 26)):
        with pytest.raises(LotSizeAmbiguous) as e:
            lot(d)
        assert "expiry" in str(e.value)
    assert lot(date(2025, 2, 27)) == 75
    assert lot(date(2025, 8, 1)) == 75


# ---------------------------------------------------------------- window 3: Oct 2025 - Jan 2026 (75 -> 65)
def test_changeover_dec_2025_weekly_and_monthly_switch_on_different_expiries() -> None:
    same_day = date(2025, 12, 15)
    assert lot(same_day, date(2026, 1, 13), "weekly") == 65
    assert lot(same_day, date(2025, 12, 30), "monthly") == 75
    assert lot(same_day, date(2026, 1, 27), "monthly") == 65
    # a January weekly already has 65 while the last December monthly (30 Dec) still has 75
    assert lot(date(2025, 12, 31), date(2026, 1, 6), "weekly") == 65
    assert lot(date(2025, 12, 29), date(2025, 12, 30), "monthly") == 75


def test_changeover_dec_2025_the_edges_of_the_circular() -> None:
    assert lot(date(2025, 12, 1), date(2025, 12, 23), "weekly") == 75  # last weekly with 75
    assert lot(date(2025, 12, 1), date(2026, 1, 6), "weekly") == 65  # first weekly with 65
    assert lot(date(2026, 1, 5), date(2026, 1, 27), "monthly") == 65  # first monthly with 65
    assert lot(date(2026, 2, 10), date(2026, 2, 17), "weekly") == 65


def test_changeover_dec_2025_by_date_alone() -> None:
    assert lot(date(2025, 10, 28)) == 75
    for d in (date(2025, 10, 29), date(2025, 12, 15), date(2026, 1, 26)):
        with pytest.raises(LotSizeAmbiguous):
            lot(d)
    assert lot(date(2026, 1, 27)) == 65
    assert lot(date(2026, 10, 3)) == 65


# ---------------------------------------------------------------- outside, by contract only, snapshot
def test_before_the_table_starts_it_refuses_to_guess() -> None:
    with pytest.raises(LotSizeUnknown):
        lot(date(2021, 4, 29))
    assert lot(date(2021, 5, 3)) == 50
    with pytest.raises(LotSizeUnknown):
        lot_size_for_contract(T, "NIFTY", date(2021, 4, 29), "monthly")


def test_a_contract_alone_decides_its_lot_without_a_trade_date() -> None:
    f = lambda e, c: lot_size_for_contract(T, "NIFTY", e, c)  # noqa: E731
    assert [f(date(2024, 4, 25), "monthly"), f(date(2024, 5, 30), "monthly")] == [50, 25]
    assert [f(date(2024, 5, 2), "weekly"), f(date(2025, 1, 2), "weekly"), f(date(2026, 1, 6), "weekly")] == [25, 75, 65]
    assert [f(date(2025, 1, 30), "monthly"), f(date(2025, 2, 27), "monthly")] == [25, 75]
    assert [f(date(2025, 12, 30), "monthly"), f(date(2026, 1, 27), "monthly")] == [75, 65]
    with pytest.raises(ValueError):
        f(date(2025, 1, 30), "quarterly")


def test_every_nifty_option_in_todays_snapshot_has_the_lot_the_table_gives_for_its_expiry() -> None:
    from app.upstox.instruments import load_instruments

    snap = Path(__file__).resolve().parents[2] / "data" / "instruments" / "2026-10-03" / "NSE.json.gz"
    if not snap.exists():
        pytest.skip("no instrument snapshot on this machine")
    options = [i for i in load_instruments(snap) if i.kind == "option" and i.expiry is not None]
    assert options
    for expiry, lot_size in {(i.expiry, i.lot_size) for i in options}:
        for cycle in ("weekly", "monthly"):
            assert T.lot_size("NIFTY", date(2026, 10, 3), expiry=expiry, cycle=cycle) == lot_size, (expiry, cycle)


# ---------------------------------------------------------------- the engine uses it
ROWS = [(100, 100, 100, 100), (100, 100, 100, 100), (101, 101, 101, 101), (101, 101, 101, 101)]


def _pnl(day: tuple[int, int, int], expiry: date, cycle: str) -> tuple[int, float]:
    res = run(Scripted({0: [buy()], 2: [exit_()]}), day_bars(day, ROWS), lot_size=None, lot_table=T, underlying="NIFTY",
              contract=lambda d: (expiry, cycle))
    return res.trades[0].lot_size, res.trades[0].net_pnl


def test_the_engine_gives_a_weekly_and_a_monthly_bought_on_the_same_day_their_own_lots() -> None:
    day = (2025, 1, 15)
    assert _pnl(day, date(2025, 1, 16), "weekly") == (75, 75.0)
    assert _pnl(day, date(2025, 1, 30), "monthly") == (25, 25.0)
    day = (2025, 12, 15)
    assert _pnl(day, date(2026, 1, 13), "weekly") == (65, 65.0)
    assert _pnl(day, date(2025, 12, 30), "monthly") == (75, 75.0)


def test_the_engine_refuses_a_changeover_date_without_the_contract() -> None:
    for day in ((2025, 1, 15), (2025, 12, 15), (2024, 5, 15)):
        with pytest.raises(LotSizeAmbiguous):
            run(Scripted({0: [buy()], 2: [exit_()]}), day_bars(day, ROWS), lot_size=None, lot_table=T, underlying="NIFTY")
