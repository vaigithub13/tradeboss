"""Closed N-minute bars from live 1-minute bars, anchored to 09:15 IST like the resampler.

No look-ahead: a minute is closed only when a later minute has arrived, so a bar is emitted only
after its last minute is final. A bucket with a missing minute is recorded in `incomplete` and never
emitted, so the strategy never trades a bar it cannot trust.
"""

from __future__ import annotations

from typing import Any

IST_OFFSET_S = 19_800
OPEN_IST_MIN = 9 * 60 + 15


def _anchor(t: int) -> int:
    """Unix seconds of 09:15 IST on the IST day that `t` falls in."""
    day_start_utc = (t + IST_OFFSET_S) // 86_400 * 86_400 - IST_OFFSET_S
    return day_start_utc + OPEN_IST_MIN * 60


class ClosedBars:
    def __init__(self, bar_minutes: int = 5) -> None:
        if bar_minutes < 1:
            raise ValueError("bar_minutes must be >= 1")
        self.bar_minutes = bar_minutes
        self._width = bar_minutes * 60
        self._pending: dict[int, dict[str, Any]] = {}  # minutes not yet known to be closed
        self._buckets: dict[int, dict[int, dict[str, Any]]] = {}
        self._last_closed: int | None = None
        self.incomplete: set[int] = set()
        self.pre_open_ignored = 0

    def on_minute(self, bar: dict[str, Any]) -> list[dict[str, Any]]:
        """Feed one 1-minute bar (a forming update or a new minute). Returns the 5m bars that just closed."""
        t = int(bar["time"])
        out: list[dict[str, Any]] = []
        for k in sorted(k for k in self._pending if k < t):
            out += self._close_minute(self._pending.pop(k))
        if self._last_closed is None or t > self._last_closed:
            self._pending[t] = dict(bar)  # a forming update of the same minute replaces it
        return out

    def end_of_day(self) -> list[dict[str, Any]]:
        """Close every minute still pending (the session has ended)."""
        out: list[dict[str, Any]] = []
        for k in sorted(self._pending):
            out += self._close_minute(self._pending.pop(k))
        for start in sorted(self._buckets):
            self.incomplete.add(start)
        self._buckets.clear()
        return out

    def _close_minute(self, minute: dict[str, Any]) -> list[dict[str, Any]]:
        t = int(minute["time"])
        anchor = _anchor(t)
        if t < anchor:
            self.pre_open_ignored += 1
            return []
        self._last_closed = t
        start = anchor + (t - anchor) // self._width * self._width
        for s in [s for s in self._buckets if s < start]:
            self.incomplete.add(s)
            del self._buckets[s]
        bucket = self._buckets.setdefault(start, {})
        bucket[t] = minute
        if len(bucket) == self.bar_minutes:
            del self._buckets[start]
            return [_aggregate(start, bucket)]
        if t >= start + self._width - 60:  # its last minute has closed and it is still short
            self.incomplete.add(start)
            del self._buckets[start]
        return []


def _aggregate(start: int, bucket: dict[int, dict[str, Any]]) -> dict[str, Any]:
    minutes = [bucket[k] for k in sorted(bucket)]
    return {
        "time": start,
        "open": float(minutes[0]["open"]),
        "high": max(float(m["high"]) for m in minutes),
        "low": min(float(m["low"]) for m in minutes),
        "close": float(minutes[-1]["close"]),
        "volume": sum(float(m.get("volume") or 0) for m in minutes),
    }
