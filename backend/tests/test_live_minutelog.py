"""The per-minute log keeps its close phase when the reconcile runs in a process that did not write it."""

from __future__ import annotations

from app.live.minutelog import read_phase, write_minute_log


def test_the_close_phase_written_before_a_restart_can_be_read_back(tmp_path) -> None:
    path = tmp_path / "2026-10-06.minutes.jsonl"
    close = [{"key": "NSE_INDEX|Nifty 50", "time": "15:29", "tick": {"close": 1.0}}]
    write_minute_log(path, close=close, reconciled=None, summary={"day": "2026-10-06"})
    assert read_phase(path, "close") == close

    write_minute_log(path, close=read_phase(path, "close"), reconciled=[{"key": "k", "time": "15:29"}], summary={})
    assert read_phase(path, "close") == close and read_phase(path, "reconciled") == [{"key": "k", "time": "15:29"}]


def test_a_missing_log_has_no_rows(tmp_path) -> None:
    assert read_phase(tmp_path / "none.jsonl", "close") == []
