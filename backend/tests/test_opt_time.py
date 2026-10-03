"""Time to expiry (case 14) and the two year conventions."""

from __future__ import annotations

from datetime import date

from app.backtest.expiry import load_default_calendar
from app.options.time import (
    CALENDAR_MIN_PER_YEAR,
    TRADING_MIN_PER_YEAR,
    calendar_minutes_to_expiry,
    trading_minutes_to_expiry,
    years_to_expiry,
)
from tests.opt_helpers import ist

CAL = load_default_calendar()
D = date.fromisoformat


def mins(t: int, expiry: str) -> int:
    return trading_minutes_to_expiry(t, D(expiry), CAL.is_trading_day)


def test_14_mon_1000_to_tue_expiry_is_705_minutes() -> None:
    t = ist(2026, 10, 5, 10, 0)  # Mon; Tue 6 Oct is expiry
    assert CAL.next_expiry(D("2026-10-05")).date == D("2026-10-06")
    assert mins(t, "2026-10-06") == 705
    assert abs(years_to_expiry(t, D("2026-10-06"), CAL.is_trading_day) - 0.00752) < 1e-12


def test_14_fri_1500_to_tue_is_780_minutes() -> None:
    # Fri 26 Sep 2025 → Tue 30 Sep monthly/weekly expiry
    t = ist(2025, 9, 26, 15, 0)
    assert CAL.next_expiry(D("2025-09-26")).date == D("2025-09-30")
    assert mins(t, "2025-09-30") == 780


def test_14_eid_shifted_expiry_has_no_holiday_gap() -> None:
    # Thu 11 Apr 2024 is Eid; weekly expiry moved to Wed 10 Apr. Tue 9 Apr → Wed is two sessions.
    t = ist(2024, 4, 9, 10, 0)
    assert CAL.next_expiry(D("2024-04-09")).date == D("2024-04-10")
    assert not CAL.is_trading_day(D("2024-04-11"))
    assert mins(t, "2024-04-10") == 705  # Tue remaining 330 + Wed 375; Thu holiday is after expiry


def test_14_last_minute_and_after_close_are_1_and_0() -> None:
    exp = D("2026-10-06")
    assert mins(ist(2026, 10, 6, 15, 29), "2026-10-06") == 1
    assert mins(ist(2026, 10, 6, 15, 30), "2026-10-06") == 0
    assert mins(ist(2026, 10, 6, 16, 0), "2026-10-06") == 0
    assert mins(ist(2026, 10, 6, 9, 0), "2026-10-06") == 375
    assert years_to_expiry(ist(2026, 10, 6, 15, 30), exp, CAL.is_trading_day) == 0.0


def test_calendar_minutes_keep_the_weekend_which_india_vix_does() -> None:
    t = ist(2025, 9, 26, 15, 0)  # Fri 15:00 → Tue 15:30 is 4 days minus the leftover Friday hour
    cal = calendar_minutes_to_expiry(t, D("2025-09-30"))
    # Fri 15:00 → Tue 15:00 is 4 × 24 h, plus the last 30 min
    assert cal == 4 * 24 * 60 + 30
    assert calendar_minutes_to_expiry(t, D("2025-09-30")) > mins(t, "2025-09-30")
    assert TRADING_MIN_PER_YEAR == 375 * 250
    assert CALENDAR_MIN_PER_YEAR == 365 * 24 * 60
