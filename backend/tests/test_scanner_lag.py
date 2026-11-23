"""Scanner work yields the GIL so a live tick thread is not stalled for more than a second."""

from __future__ import annotations

import threading
import time

from app.runtime.lag import note_loop_lag, pace
from app.scanner.service import scanner_executor


def test_scanner_executor_has_one_worker() -> None:
    assert scanner_executor()._max_workers == 1


def test_paced_scan_keeps_tick_gaps_under_one_second() -> None:
    note_loop_lag(500)
    gaps: list[float] = []
    stop = threading.Event()

    def ticks() -> None:
        last = time.perf_counter()
        while not stop.wait(0.01):
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    thread = threading.Thread(target=ticks, daemon=True)
    thread.start()
    try:
        deadline = time.perf_counter() + 0.6
        while time.perf_counter() < deadline:
            started = time.perf_counter()
            while time.perf_counter() - started < 0.04:
                pass
            pace()
    finally:
        stop.set()
        thread.join(timeout=1)
        note_loop_lag(0)
    assert gaps
    assert max(gaps) <= 1.0
