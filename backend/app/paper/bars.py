"""Closed N-minute bars from exchange-final 1-minute bars, anchored to 09:15 IST like the resampler.

A minute is accepted only when it is exchange-final (source `i1`, the feed's completed-minute bar, or
`official` after the reconcile). A bar is emitted only when every one of its minutes has arrived, with
those minutes' OHLC. A bucket that is still short when its last minute has arrived is recorded in
`incomplete` and never emitted, so the strategy never trades a bar it cannot trust.
"""

from __future__ import annotations

from typing import Any

IST_OFFSET_S = 19_800
OPEN_IST_MIN = 9 * 60 + 15
FINAL_SOURCES = ("i1", "session_end", "official")


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
        self._buckets: dict[int, dict[int, dict[str, Any]]] = {}
        self._last_minute: int | None = None
        self.incomplete: set[int] = set()
        self.pre_open_ignored = 0
        self.not_final_ignored = 0

    def on_minute(self, bar: dict[str, Any]) -> list[dict[str, Any]]:
        """Feed one exchange-final 1-minute bar. Returns the bars that just closed."""
        if bar.get("source") not in FINAL_SOURCES:
            self.not_final_ignored += 1
            return []
        t = int(bar["time"])
        if self._last_minute is not None and t <= self._last_minute:
            return []  # a repeat or an out-of-order minute: the first final copy wins
        anchor = _anchor(t)
        if t < anchor:
            self.pre_open_ignored += 1
            return []
        self._last_minute = t
        start = anchor + (t - anchor) // self._width * self._width
        for s in [s for s in self._buckets if s < start]:
            self.incomplete.add(s)
            del self._buckets[s]
        bucket = self._buckets.setdefault(start, {})
        bucket[t] = bar
        if len(bucket) == self.bar_minutes:
            del self._buckets[start]
            return [_aggregate(start, bucket)]
        if t >= start + self._width - 60:  # its last minute has arrived and it is still short
            self.incomplete.add(start)
            del self._buckets[start]
        return []

    def end_of_day(self) -> None:
        """The session has ended: any bucket still open can never complete."""
        self.incomplete.update(self._buckets)
        self._buckets.clear()


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
