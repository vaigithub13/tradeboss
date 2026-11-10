"""launchd session-start: leave today's backend alone; stop a prior-day leftover cleanly, then start fresh."""

from __future__ import annotations

from datetime import date

from scripts.session_start_guard import decide_backend, parse_lstart_date_ist


def test_parse_macos_lstart_to_ist_calendar_date() -> None:
    # ps -o lstart= on macOS: "Mon Nov  9 08:51:19 2026" (local wall clock; Mac is IST here)
    assert parse_lstart_date_ist("Mon Nov  9 08:51:19 2026") == date(2026, 11, 9)
    assert parse_lstart_date_ist("Sun Nov  8 13:04:53 2026") == date(2026, 11, 8)


def test_no_listener_means_start_fresh() -> None:
    assert decide_backend(listening=False, started_on=None, today=date(2026, 11, 9)) == "start"


def test_backend_started_today_is_left_alone() -> None:
    assert decide_backend(listening=True, started_on=date(2026, 11, 9), today=date(2026, 11, 9)) == "leave"


def test_backend_from_a_previous_day_is_stopped_then_started() -> None:
    assert decide_backend(listening=True, started_on=date(2026, 11, 8), today=date(2026, 11, 9)) == "stop_then_start"


def test_listening_but_unknown_start_date_is_treated_as_stale() -> None:
    # Safer to recycle than to leave a mystery overnight process.
    assert decide_backend(listening=True, started_on=None, today=date(2026, 11, 9)) == "stop_then_start"
