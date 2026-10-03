"""Nifty expiry calendar (proposal B): rules + holiday shift, checked against REAL expiries.

Ground truth: the 105 real Nifty expiry dates Upstox returns (2024-10-03 .. 2026-09-29), the NSE circulars
quoted in the rules file, and the lot-size circulars' own expiry dates. Holidays come from a fixture
(shipped in data/nse_holidays.json: weekdays with no regular session in our Nifty data + the NSE 2026 holiday circular)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.backtest.expiry import ExpiryCalendar, ExpiryConfigError, HolidayCalendar, load_default_calendar, rules_from_dict
from app.backtest.lots import load_default_lot_table

ROOT = Path(__file__).resolve().parents[1]
CAL = load_default_calendar()  # the shipped rules + holidays
RULES = CAL.rules
REAL = [date.fromisoformat(x) for x in json.loads((Path(__file__).parent / "fixtures/upstox/nifty_expiries_2024-10_to_2026-09.json").read_text())["expiries"]]
D = date.fromisoformat


def dates(a: str, b: str) -> list[date]:
    return [e.date for e in CAL.expiries(D(a), D(b))]


def test_the_proposed_rules_are_thursday_then_tuesday_from_the_1_sep_2025_expiries() -> None:
    assert [(r.weekday, r.first_expiry_on_or_after) for r in RULES] == [(3, date.min), (1, date(2025, 9, 1))]
    assert all(r.source for r in RULES)


# ---------------------------------------------------------------- against reality
def test_it_reproduces_every_real_expiry_from_oct_2024_to_sep_2026_exactly() -> None:
    got = dates("2024-10-03", "2026-09-29")
    assert got == REAL, (sorted(set(got) - set(REAL)), sorted(set(REAL) - set(got)))
    assert len(REAL) == 105


def test_every_expiry_the_exchange_lists_today_is_in_the_calendar() -> None:
    from app.upstox.instruments import load_instruments

    snap = ROOT.parent / "data" / "instruments" / "2026-10-03" / "NSE.json.gz"
    if not snap.exists():
        pytest.skip("no instrument snapshot on this machine")
    listed = {i.expiry for i in load_instruments(snap) if i.kind == "option" and i.expiry and i.expiry.year <= 2026}
    assert listed
    calendar = set(dates("2026-10-03", "2026-12-31"))
    assert listed <= calendar, sorted(listed - calendar)


# ---------------------------------------------------------------- the one rule change (Thursday -> Tuesday)
def test_around_the_thursday_to_tuesday_change_of_sep_2025() -> None:
    got = CAL.expiries(D("2025-08-11"), D("2025-09-30"))
    assert [(e.date.isoformat(), e.date.strftime("%a"), e.kind) for e in got] == [
        ("2025-08-14", "Thu", "weekly"),
        ("2025-08-21", "Thu", "weekly"),
        ("2025-08-28", "Thu", "monthly"),  # last Thursday of August: the last Thursday expiry
        ("2025-09-02", "Tue", "weekly"),  # the first Tuesday expiry (not Sep 4)
        ("2025-09-09", "Tue", "weekly"),
        ("2025-09-16", "Tue", "weekly"),
        ("2025-09-23", "Tue", "weekly"),
        ("2025-09-30", "Tue", "monthly"),  # last Tuesday of September
    ]
    assert CAL.monthly_of(2025, 9).weekday_rule == "Tuesday" and CAL.monthly_of(2025, 8).weekday_rule == "Thursday"


def test_circular_68685_examples() -> None:
    # Oct 2025 monthly: 30-Oct (Thursday) became 28-Oct (last Tuesday); Sep monthly 25-Sep -> 30-Sep
    assert CAL.monthly_of(2025, 10).date == D("2025-10-28") and CAL.monthly_of(2025, 9).date == D("2025-09-30")
    assert D("2025-10-07") in dates("2025-10-01", "2025-10-10")


def test_the_deferred_monday_plan_of_march_2025_never_happened() -> None:
    got = CAL.expiries(D("2025-03-31"), D("2025-05-05"))
    assert [(e.date.isoformat(), e.date.strftime("%a")) for e in got] == [
        ("2025-04-03", "Thu"), ("2025-04-09", "Wed"),  # 10-Apr holiday -> Wed
        ("2025-04-17", "Thu"), ("2025-04-24", "Thu"), ("2025-04-30", "Wed"),  # 1-May holiday -> Wed
    ]
    assert D("2025-04-07") not in dates("2025-04-01", "2025-04-30")  # the Mondays of the deferred plan


def test_weekly_expiries_of_other_indices_going_away_in_nov_2024_does_not_touch_nifty() -> None:
    assert dates("2024-11-04", "2024-11-30") == [D("2024-11-07"), D("2024-11-14"), D("2024-11-21"), D("2024-11-28")]


# ---------------------------------------------------------------- holiday shifts (previous trading day)
@pytest.mark.parametrize(
    "week_of, expected, why",
    [
        ("2022-04-12", "2022-04-13", "14-Apr-2022 Ambedkar Jayanti (Thu) -> Wed"),
        ("2025-04-07", "2025-04-09", "10-Apr-2025 Mahavir Jayanti (Thu) -> Wed"),
        ("2025-04-28", "2025-04-30", "1-May-2025 Maharashtra Day (Thu) -> Wed"),
        ("2025-10-20", "2025-10-20", "21-Oct-2025 Diwali Tuesday (muhurat session only) -> Mon"),
        ("2026-03-02", "2026-03-02", "3-Mar-2026 Holi (Tue) -> Mon"),
        ("2026-03-30", "2026-03-30", "31-Mar-2026 Mahavir Jayanti (Tue) -> Mon"),
        ("2026-04-13", "2026-04-13", "14-Apr-2026 Ambedkar Jayanti (Tue) -> Mon"),
        ("2026-10-19", "2026-10-19", "20-Oct-2026 Dussehra (Tue, NSE circular 71777) -> Mon"),
        ("2026-11-23", "2026-11-23", "24-Nov-2026 Guru Nanak Jayanti (Tue) -> Mon"),
    ],
)
def test_a_holiday_moves_the_expiry_to_the_previous_trading_day(week_of: str, expected: str, why: str) -> None:
    e = CAL.weekly_in_week_of(D(week_of))
    assert e.date == D(expected) and e.shifted, why


def test_a_run_of_holidays_goes_back_to_the_last_trading_day() -> None:
    cal = ExpiryCalendar(RULES, HolidayCalendar([D("2024-05-22"), D("2024-05-23")]))  # Wed + Thu closed
    e = cal.weekly_in_week_of(D("2024-05-20"))
    assert (e.date, e.nominal) == (D("2024-05-21"), D("2024-05-23"))  # Tuesday, never the next Friday


def test_a_monthly_expiry_on_a_holiday_also_moves_back() -> None:
    assert CAL.monthly_of(2026, 3).date == D("2026-03-30") and CAL.monthly_of(2026, 3).nominal == D("2026-03-31")
    assert CAL.monthly_of(2026, 11).date == D("2026-11-23")


# ---------------------------------------------------------------- consistency with the lot-size circulars
def test_the_expiry_dates_named_in_the_lot_circulars_are_calendar_expiries_of_the_right_kind() -> None:
    rows = [r for r in load_default_lot_table().rows if r.first_weekly_expiry]
    assert len(rows) == 3
    for r in rows:
        assert r.first_weekly_expiry is not None and r.first_monthly_expiry is not None
        weekly = {e.date for e in CAL.expiries(r.first_weekly_expiry, r.first_weekly_expiry)}
        monthly = {e.date for e in CAL.expiries(r.first_monthly_expiry, r.first_monthly_expiry) if e.kind == "monthly"}
        assert weekly == {r.first_weekly_expiry}, r
        assert monthly == {r.first_monthly_expiry}, r
    # "last expiry with the old lot" dates of the circulars
    for d in ("2024-04-25", "2024-12-19", "2025-01-30", "2025-12-23", "2025-12-30"):
        assert D(d) in dates(d, d), d
    assert CAL.monthly_of(2024, 4).date == D("2024-04-25") and CAL.monthly_of(2025, 12).date == D("2025-12-30")
    assert CAL.monthly_of(2025, 3).date == D("2025-03-27")  # the quarterly named in the Oct-2024 circular


# ---------------------------------------------------------------- the API
def test_next_expiry_and_rolling_on_expiry_day() -> None:
    tue = D("2026-09-22")  # a Tuesday expiry
    assert CAL.next_expiry(tue).date == tue
    assert CAL.next_expiry(tue, skip_expiry_day=True).date == D("2026-09-29")
    assert CAL.next_expiry(D("2026-09-23")).date == D("2026-09-29")
    assert CAL.next_expiry(D("2026-09-01"), "monthly").date == D("2026-09-29")
    assert CAL.next_expiry(D("2026-09-29"), "monthly").kind == "monthly"
    assert CAL.next_expiry(D("2026-09-30"), "monthly").date == D("2026-10-27")
    assert CAL.next_expiry(D("2026-10-17")).date == D("2026-10-19")  # Tue 20-Oct is a holiday


def test_is_expiry_day_and_weekend_dates() -> None:
    assert CAL.is_expiry_day(D("2025-09-02")) and not CAL.is_expiry_day(D("2025-09-04"))
    assert CAL.is_expiry_day(D("2025-09-30"), "monthly") and not CAL.is_expiry_day(D("2025-09-23"), "monthly")
    assert not CAL.is_expiry_day(D("2026-10-17"))  # a Saturday


def test_the_calendar_does_not_depend_on_today_and_is_repeatable() -> None:
    assert CAL.expiries(D("2023-01-01"), D("2023-12-31")) == CAL.expiries(D("2023-01-01"), D("2023-12-31"))
    year = CAL.expiries(D("2023-01-01"), D("2023-12-31"))
    assert all(e.date.weekday() < 5 for e in year) and len(year) >= 50
    assert sum(e.kind == "monthly" for e in year) == 12


def test_config_errors() -> None:
    with pytest.raises(ExpiryConfigError):
        rules_from_dict({"rules": []})
    with pytest.raises(ExpiryConfigError):
        rules_from_dict({"rules": [{"weekday": "Funday", "first_expiry_on_or_after": None}]})
    with pytest.raises(ExpiryConfigError):  # the first rule must hold from the beginning
        rules_from_dict({"rules": [{"weekday": "Tuesday", "first_expiry_on_or_after": "2025-09-01"}]})
    with pytest.raises(ExpiryConfigError):
        rules_from_dict({"rules": [{"weekday": "Tuesday", "first_expiry_on_or_after": "01-09-2025"}]})
    never = ExpiryCalendar(RULES, lambda d: False)
    with pytest.raises(ExpiryConfigError):
        never.weekly_in_week_of(D("2025-01-01"))
