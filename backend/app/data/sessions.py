"""Trading-session labels, by TYPE of session (not by weekday).

normal          full-length Mon-Fri session                          (included by default)
weekend_full  FULL-length Saturday/Sunday session, e.g. Budget day, special Saturday (included by default)
special_short   short or broken session on any day, e.g. 2024-03-02  (excluded by default)
muhurat         Diwali Muhurat session, any shape, from the calendar (excluded by default)

Precedence: muhurat > (not full-length -> special_short) > (weekend -> weekend_full) > normal.
"Full-length" = first bar at/before 09:15, last bar ends at/after 15:30 and at least 90% of
the expected bars are present.

MUHURAT_DATES is a hard-coded NSE-calendar list for now. Phase 2 replaces it with Upstox
market timings / holidays data (see PROGRESS.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal

SessionType = Literal["normal", "weekend_full", "special_short", "muhurat"]
SESSION_TYPES: tuple[SessionType, ...] = ("normal", "weekend_full", "special_short", "muhurat")
DEFAULT_INCLUDED_SESSION_TYPES: tuple[SessionType, ...] = ("normal", "weekend_full")

SESSION_OPEN_MIN = 9 * 60 + 15  # 09:15
SESSION_CLOSE_MIN = 15 * 60 + 30  # 15:30
FULL_SESSION_MIN_COVERAGE = 0.9

MUHURAT_DATES: frozenset[date] = frozenset(
    {
        date(2022, 10, 24),
        date(2023, 11, 12),
        date(2024, 11, 1),
        date(2025, 10, 21),
    }
)


def is_full_length(minutes: Sequence[int], bar_minutes: int) -> bool:
    """`minutes` = minute-of-day (IST) of every bar START in the session."""
    if not minutes:
        return False
    expected = (SESSION_CLOSE_MIN - SESSION_OPEN_MIN) / bar_minutes
    return (
        min(minutes) <= SESSION_OPEN_MIN
        and max(minutes) + bar_minutes >= SESSION_CLOSE_MIN
        and len(minutes) >= FULL_SESSION_MIN_COVERAGE * expected
    )


def classify_session(d: date, minutes: Sequence[int], bar_minutes: int) -> SessionType:
    if d in MUHURAT_DATES:
        return "muhurat"
    if not is_full_length(minutes, bar_minutes):
        return "special_short"
    if d.weekday() >= 5:
        return "weekend_full"
    return "normal"
