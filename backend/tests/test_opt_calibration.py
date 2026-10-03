"""Calibration math (cases 29–34) plus trading-T vs calendar-T. Fake data only."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.backtest.expiry import load_default_calendar
from app.options.bs import bs_price
from app.options.calibration import (
    FilterCounts,
    Point,
    bootstrap_scale_ci,
    build_report,
    compare_bases,
    error_stats,
    filter_points,
    fit_scale,
    implied_carry,
    model_prices,
    pair_points,
    write_report,
)
from app.options.history import OptionBar
from app.options.time import years_to_expiry

IST = timezone(timedelta(hours=5, minutes=30))
CAL = load_default_calendar()
D = date.fromisoformat


def ts(day: str, h: int, m: int) -> int:
    y, mo, d = (int(x) for x in day.split("-"))
    return int(datetime(y, mo, d, h, m, tzinfo=IST).timestamp())


def pt(
    *,
    real: float,
    spot: float = 25000.0,
    strike: float = 25000.0,
    kind: str = "CE",
    vix: float = 14.0,
    day: str = "2026-10-05",
    hm: tuple[int, int] = (10, 0),
    expiry: str = "2026-10-06",
    volume: float = 100.0,
    scale_used: float | None = None,
) -> Point:
    t = ts(day, *hm)
    exp = D(expiry)
    Ttr = years_to_expiry(t, exp, CAL.is_trading_day, "trading")
    Tcl = years_to_expiry(t, exp, CAL.is_trading_day, "calendar")
    if scale_used is not None:
        real = bs_price(kind, spot, strike, Ttr, scale_used * vix / 100.0)
    return Point(
        time=t, expiry=exp, strike=strike, kind=kind, real=real, spot=spot, vix=vix, volume=volume,
        T_trading=Ttr, T_calendar=Tcl, dte=1, moneyness="ATM", tod="morning",
    )


def test_29_real_and_model_are_paired_at_the_same_minute_on_opens() -> None:
    t = ts("2026-10-05", 10, 0)
    bars = [OptionBar(t, D("2026-10-06"), 25000.0, "CE", 100, 101, 99, 100.5, 50, 1, "upstox-expired")]
    index = {t: 25010.0, t + 60: 25020.0}
    vix = {t: 14.0}
    got = pair_points(bars, index, vix, calendar=CAL, step=50)
    assert len(got) == 1
    assert (got[0].real, got[0].spot, got[0].vix) == (100.0, 25010.0, 14.0)  # option open, index open, vix open
    assert pair_points(bars, {}, vix, calendar=CAL, step=50) == []


def test_30_filters_drop_zero_volume_cheap_prints_and_the_open_and_count_them() -> None:
    t0 = ts("2026-10-05", 9, 15)
    rows = [
        OptionBar(t0, D("2026-10-06"), 25000, "CE", 80, 80, 80, 80, 0, None, "x"),
        OptionBar(t0 + 60, D("2026-10-06"), 25000, "CE", 3, 3, 3, 3, 10, None, "x"),
        OptionBar(t0 + 120, D("2026-10-06"), 25000, "CE", 80, 80, 80, 80, 10, None, "x"),
        OptionBar(t0 + 180, D("2026-10-06"), 25000, "CE", 80, 80, 80, 80, 10, None, "x"),
    ]
    index = {b.time: 25000.0 for b in rows}
    vix = {b.time: 14.0 for b in rows}
    points = pair_points(rows, index, vix, calendar=CAL, step=50)
    kept, counts = filter_points(points)
    assert counts == FilterCounts(input=4, zero_volume=1, cheap=1, open_minutes=1, kept=1)
    assert [p.time for p in kept] == [t0 + 180]


def test_31_error_stats_match_a_hand_computed_set() -> None:
    real = np.array([100.0, 100.0, 100.0])
    model = np.array([110.0, 90.0, 100.0])
    got = error_stats(real, model)
    assert got["n"] == 3
    assert abs(got["bias"] - 0.0) < 1e-12
    assert abs(got["mape"] - (0.1 + 0.1 + 0.0) / 3) < 1e-12
    assert abs(got["median_ape"] - 0.1) < 1e-12
    assert abs(got["rmse"] - np.sqrt((0.01 + 0.01 + 0.0) / 3)) < 1e-12


def test_32_the_scale_fit_recovers_1_18_and_resists_outliers() -> None:
    clean = [
        pt(real=0, spot=s, strike=s, hm=(10 + i, 0), scale_used=1.18)
        for i, s in enumerate((24900.0, 25000.0, 25100.0, 25200.0, 24800.0))
    ]
    k = fit_scale(clean)
    assert abs(k - 1.18) < 0.01
    dirty = list(clean)
    outlier = pt(real=clean[0].real * 12, spot=24900.0, strike=24900.0, hm=(10, 0))
    dirty[0] = outlier
    k2 = fit_scale(dirty)
    assert abs(k2 - 1.18) < 0.05
    ci = bootstrap_scale_ci(clean, n=20, seed=7)
    assert ci["lo"] <= ci["median"] <= ci["hi"]
    again = bootstrap_scale_ci(clean, n=20, seed=7)
    assert ci == again  # seeded


def test_33_implied_net_carry_from_put_call_parity() -> None:
    t = ts("2026-09-29", 10, 0)
    exp = D("2026-10-06")
    S, K, vix = 25000.0, 25000.0, 14.0
    Tcal = years_to_expiry(t, exp, CAL.is_trading_day, "calendar")
    Ttr = years_to_expiry(t, exp, CAL.is_trading_day, "trading")
    r = 0.06
    ce = bs_price("CE", S, K, Tcal, 0.14, r, 0.0)
    pe = ce - (S - K * np.exp(-r * Tcal))  # PCP with q=0
    ce_p = Point(t, exp, K, "CE", ce, S, vix, 50, Ttr, Tcal, 5, "ATM", "morning")
    pe_p = Point(t, exp, K, "PE", float(pe), S, vix, 50, Ttr, Tcal, 5, "ATM", "morning")
    got = implied_carry([ce_p, pe_p])
    assert got["n"] == 1 and got["applied"] is False
    assert abs(got["median"] - r) < 1e-6


def test_34_the_report_is_byte_identical_across_writes(tmp_path: Path) -> None:
    points = [pt(real=0, spot=25000, strike=25000, scale_used=1.0, hm=(10, 0))]
    filters = FilterCounts(input=1, kept=1)
    a = build_report(points, filters, as_of=D("2026-10-03"), sources={"upstox": 1, "recorded": 0}, step=50, bootstrap_n=8)
    b = build_report(points, filters, as_of=D("2026-10-03"), sources={"upstox": 1, "recorded": 0}, step=50, bootstrap_n=8)
    assert a == b
    p1, j1 = write_report(a, tmp_path, D("2026-10-03"))
    p2, j2 = write_report(b, tmp_path / "copy", D("2026-10-03"))
    assert p1.read_text() == p2.read_text() and j1.read_text() == j2.read_text()
    assert "option_model_calibration_2026-10-03" in p1.name


def test_time_basis_comparison_picks_the_T_that_generated_the_premiums() -> None:
    # premiums generated with trading-minute T and scale 1.0 → trading should win
    # Friday and Monday into the same expiry: T_trading/T_calendar is not a constant, so a single
    # calendar scale cannot recover premiums that were generated with trading T.
    points = [
        pt(real=0, spot=25000 + 10 * i, strike=25000, day="2025-09-26", hm=(10, i), expiry="2025-09-30", scale_used=1.0)
        for i in range(6)
    ] + [
        pt(real=0, spot=25000 + 10 * i, strike=25000, day="2025-09-29", hm=(10, i), expiry="2025-09-30", scale_used=1.0)
        for i in range(6)
    ]
    got = compare_bases(points)
    assert got["all"]["winner"] == "trading"
    assert got["all"]["trading"]["median_ape"] < got["all"]["calendar"]["median_ape"]
    assert set(got) >= {"all", "0", "1", "2", "3-4", "5+"}


def test_model_prices_use_the_requested_basis() -> None:
    p = pt(real=100, spot=25010, strike=25000)
    tr = model_prices([p], 1.0, "trading")[0]
    cal = model_prices([p], 1.0, "calendar")[0]
    assert tr == bs_price("CE", 25010, 25000, p.T_trading, 0.14)
    assert cal == bs_price("CE", 25010, 25000, p.T_calendar, 0.14)
    assert tr != cal
