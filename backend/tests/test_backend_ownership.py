"""Backend ownership file: session-start/stop recognise any backend (hand or launchd) by pid + start date."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.ownership import Ownership, clear, decide, path_for, read, write
from scripts.session_start_guard import decide_backend

IST = ZoneInfo("Asia/Kolkata")


def test_write_read_clear_round_trip(tmp_path) -> None:
    p = path_for(tmp_path)
    assert read(p) is None
    write(p, pid=4242, started_on=date(2026, 11, 16), started_at=datetime(2026, 11, 16, 8, 52, 48, tzinfo=IST))
    got = read(p)
    assert got == Ownership(pid=4242, started_on=date(2026, 11, 16),
                            started_at=datetime(2026, 11, 16, 8, 52, 48, tzinfo=IST))
    clear(p)
    assert read(p) is None


def test_ownership_from_today_means_leave_even_without_pgrep() -> None:
    # The old guard treated a listener with no matching app.serve pid as "unknown day".
    assert decide(listening=True, ownership=Ownership(pid=1, started_on=date(2026, 11, 16), started_at=None),
                  today=date(2026, 11, 16), pid_alive=lambda p: True) == "leave"


def test_ownership_from_yesterday_means_stop_then_start() -> None:
    assert decide(listening=True, ownership=Ownership(pid=1, started_on=date(2026, 11, 15), started_at=None),
                  today=date(2026, 11, 16), pid_alive=lambda p: True) == "stop_then_start"


def test_stale_file_with_dead_pid_means_start() -> None:
    assert decide(listening=False, ownership=Ownership(pid=1, started_on=date(2026, 11, 16), started_at=None),
                  today=date(2026, 11, 16), pid_alive=lambda p: False) == "start"


def test_no_file_falls_back_to_process_start_date() -> None:
    # Same rules as the previous decide_backend when ownership is missing.
    assert decide_backend(listening=True, started_on=date(2026, 11, 16), today=date(2026, 11, 16)) == "leave"
    assert decide(listening=True, ownership=None, today=date(2026, 11, 16),
                  pgrep_started_on=date(2026, 11, 15), pid_alive=lambda p: True) == "stop_then_start"
