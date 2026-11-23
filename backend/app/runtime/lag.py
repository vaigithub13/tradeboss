"""Event-loop lag shared with the scanner thread.

The probe runs on the asyncio loop. Scanner work runs on its own thread and calls
`pace` so a lag over 100 ms yields the GIL instead of stalling live ticks.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("tradeboss.loop")

_lock = threading.Lock()
_lag_ms = 0.0
_max_ms = 0.0
_last_log = 0.0
_last_yield = 0.0


def note_loop_lag(lag_ms: float) -> None:
    """Record one probe sample. Logs when the loop is late, and every new maximum."""
    global _lag_ms, _max_ms, _last_log
    lag_ms = max(0.0, float(lag_ms))
    with _lock:
        _lag_ms = lag_ms
        fresh_max = lag_ms > _max_ms + 1
        if fresh_max:
            _max_ms = lag_ms
    now = time.monotonic()
    if fresh_max and lag_ms >= 100:
        log.warning("event-loop lag %.0f ms (new max)", lag_ms)
        _last_log = now
    elif lag_ms >= 100 and now - _last_log >= 5:
        log.warning("event-loop lag %.0f ms", lag_ms)
        _last_log = now


def loop_lag_ms() -> float:
    with _lock:
        return _lag_ms


def max_loop_lag_ms() -> float:
    with _lock:
        return _max_ms


def pace() -> None:
    """Give the event loop a turn. Sleep longer when it is already more than 100 ms behind."""
    global _last_yield
    now = time.perf_counter()
    if now - _last_yield < 0.05:
        return
    _last_yield = now
    lag = loop_lag_ms()
    if lag > 100:
        pause = min(0.2, lag / 1000)
        log.info("scanner yielded %.0f ms because event-loop lag was %.0f ms", pause * 1000, lag)
        time.sleep(pause)
    else:
        time.sleep(0)


async def loop_lag_probe(period: float = 0.1) -> None:
    """Sleep `period` and record how late the wake was. Cancelled with the app."""
    import asyncio

    while True:
        started = time.perf_counter()
        await asyncio.sleep(period)
        late_ms = (time.perf_counter() - started - period) * 1000
        note_loop_lag(max(0.0, late_ms))
