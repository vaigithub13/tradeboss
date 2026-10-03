"""Shared sliding-window rate limiter (thread-safe, blocking)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence


class SlidingWindowLimiter:
    """Allows at most `max_calls` per `window_s` for every (max_calls, window_s) pair.

    acquire() blocks (sleeps) until a call is allowed, then records it. `clock` and `sleep`
    are injectable so tests run instantly."""

    def __init__(
        self,
        limits: Sequence[tuple[int, float]],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not limits or any(n < 1 or w <= 0 for n, w in limits):
            raise ValueError("limits must be non-empty (max_calls >= 1, window_s > 0)")
        self._limits = tuple(limits)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._stamps: dict[float, deque[float]] = {w: deque() for _, w in self._limits}

    def _wait_needed(self, now: float) -> float:
        wait = 0.0
        for max_calls, window in self._limits:
            q = self._stamps[window]
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= max_calls:
                wait = max(wait, window - (now - q[0]))
        return wait

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                wait = self._wait_needed(now)
                if wait <= 0:
                    for _, window in self._limits:
                        self._stamps[window].append(now)
                    return
            self._sleep(wait)
