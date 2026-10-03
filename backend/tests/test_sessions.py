"""Session labeling spec (labels by TYPE of session, not by weekday).

normal          full-length Mon-Fri session                          (included by default)
weekend_full  FULL-length Saturday/Sunday session, e.g. Budget day (included by default)
special_short   short or broken session (any day), e.g. 2024-03-02   (excluded by default)
muhurat         Diwali Muhurat session, any shape, from the calendar (excluded by default)

Precedence: muhurat > (not full-length -> special_short) > (weekend -> weekend_full) > normal.
"Full-length" = first bar at/before 09:15, last bar ends at/after 15:30 and at least
90% of the expected bars are present.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.data.sessions import (
    DEFAULT_INCLUDED_SESSION_TYPES,
    MUHURAT_DATES,
    SESSION_TYPES,
    classify_session,
    is_full_length,
)

OPEN = 9 * 60 + 15  # 555


def full_session(bar_minutes: int = 5) -> list[int]:
    """Bar start minutes-of-day for a complete 09:15-15:30 session."""
    return list(range(OPEN, 15 * 60 + 30, bar_minutes))


def broken_session() -> list[int]:
    """2024-03-02 style: 09:15-10:00 then 11:30-12:15 (21 five-minute bars)."""
    return list(range(OPEN, 10 * 60, 5)) + list(range(11 * 60 + 30, 12 * 60 + 15, 5))


def test_constants() -> None:
    assert SESSION_TYPES == ("normal", "weekend_full", "special_short", "muhurat")
    assert DEFAULT_INCLUDED_SESSION_TYPES == ("normal", "weekend_full")


class TestIsFullLength:
    def test_complete_session(self) -> None:
        assert len(full_session()) == 75
        assert is_full_length(full_session(), 5)
        assert is_full_length(list(range(OPEN, 15 * 60 + 30)), 1)  # 375 one-minute bars

    def test_tolerates_a_few_missing_bars(self) -> None:
        bars = full_session()
        del bars[10:14]  # 4 of 75 missing -> 94.7%
        assert is_full_length(bars, 5)

    def test_too_many_missing_bars(self) -> None:
        assert not is_full_length(full_session()[::2], 5)  # 38 of 75

    def test_late_start_or_early_end_is_short(self) -> None:
        assert not is_full_length(full_session()[1:], 5)  # starts 09:20
        assert not is_full_length(full_session()[:-6], 5)  # ends 15:00

    def test_broken_and_empty(self) -> None:
        assert not is_full_length(broken_session(), 5)
        assert not is_full_length([], 5)


@pytest.mark.parametrize(
    ("d", "minutes", "expected"),
    [
        # full-length weekdays
        (date(2024, 1, 1), full_session(), "normal"),  # Monday
        (date(2026, 9, 30), full_session(), "normal"),  # Wednesday
        # full-length weekend sessions -> weekend_full
        (date(2025, 2, 1), full_session(), "weekend_full"),  # Saturday (Budget)
        (date(2026, 2, 1), full_session(), "weekend_full"),  # Sunday (Budget)
        (date(2024, 1, 20), full_session(), "weekend_full"),  # full Saturday session
        # short / broken sessions
        (date(2024, 3, 2), broken_session(), "special_short"),  # Saturday, 21 bars
        (date(2024, 5, 18), broken_session(), "special_short"),  # Saturday, 21 bars
        (date(2024, 12, 3), list(range(13 * 60 + 45, 14 * 60 + 45, 5)), "special_short"),  # weekday
        (date(2024, 12, 4), full_session()[:30], "special_short"),  # early close
        # Muhurat wins whatever the shape or weekday
        (date(2022, 10, 24), list(range(18 * 60 + 15, 19 * 60 + 15, 5)), "muhurat"),
        (date(2024, 11, 1), list(range(18 * 60, 19 * 60, 5)), "muhurat"),
        (date(2025, 10, 21), list(range(13 * 60 + 45, 14 * 60 + 45, 5)), "muhurat"),
        (date(2023, 11, 12), list(range(18 * 60 + 15, 19 * 60 + 15, 5)), "muhurat"),  # Sunday
        (date(2025, 10, 21), full_session(), "muhurat"),  # even if it looked full
    ],
)
def test_classify_session(d: date, minutes: list[int], expected: str) -> None:
    assert classify_session(d, minutes, 5) == expected


def test_known_muhurat_dates_from_sample_data() -> None:
    assert {
        date(2022, 10, 24),
        date(2023, 11, 12),
        date(2024, 11, 1),
        date(2025, 10, 21),
    } <= MUHURAT_DATES
