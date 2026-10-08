"""Closed N-minute bars from exchange-final 1-minute bars, anchored to 09:15 IST like the resampler.

A minute is accepted only when it is exchange-final (source `i1`, the feed's completed-minute bar, `official`
after the reconcile, or `backfill`, the intraday API's completed minute fetched for a feed gap). A bar is emitted
only when every one of its minutes has arrived, with those minutes' OHLC, and bars are emitted strictly in time
order, so the strategy never sees a bar it cannot trust or a bar out of order.

Feed gaps: a bar still short after its end waits for its missing minutes (the reconnect backfill), and the bars
after it queue behind it. When the gap fills, the rebuilt bar and the queued bars are emitted in order, flagged
`late_after_gap` (so is a bar holding a backfilled minute). A gap still open GAP_WAIT_S after the bar's end, by
the newest minute's exchange time, is given up: the bar is `incomplete` and listed in `given_up`, and the bars
behind it go on. Nothing is skipped silently.
"""

from __future__ import annotations

from typing import Any

IST_OFFSET_S = 19_800
OPEN_IST_MIN = 9 * 60 + 15
FINAL_SOURCES = ("i1", "session_end", "official", "backfill")
GAP_WAIT_S = 180  # the service retries a backfill every 15 s and stops withholding after 60 s


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
        self._next: int | None = None  # start of the next bar to emit (or give up)
        self._newest: int | None = None  # newest minute seen
        self._waited: set[int] = set()  # bars that were short after their end, or queued behind one
        self.incomplete: set[int] = set()
        self.late: set[int] = set()  # bars emitted late after a gap (or holding a backfilled minute)
        self.given_up: list[int] = []  # bars whose gap never filled, in order
        self.pre_open_ignored = 0
        self.not_final_ignored = 0
        self.after_give_up_ignored = 0

    @property
    def waiting(self) -> int | None:
        """Start of the bar the builder is waiting on (short after its end), or None."""
        if self._next is None or self._newest is None:
            return None
        return self._next if self._newest + 60 >= self._next + self._width else None

    def on_minute(self, bar: dict[str, Any]) -> list[dict[str, Any]]:
        """Feed one exchange-final 1-minute bar, in any order. Returns the bars that this minute let close."""
        if bar.get("source") not in FINAL_SOURCES:
            self.not_final_ignored += 1
            return []
        t = int(bar["time"])
        anchor = _anchor(t)
        if t < anchor:
            self.pre_open_ignored += 1
            return []
        start = anchor + (t - anchor) // self._width * self._width
        if self._next is None:
            self._next = start
        if start < self._next:
            if start in self.incomplete:
                self.after_give_up_ignored += 1
            return []  # that bar was emitted or given up: a repeat, or too late to use
        bucket = self._buckets.setdefault(start, {})
        bucket.setdefault(t, bar)  # the first final copy wins
        self._newest = t if self._newest is None else max(self._newest, t)
        return self._release()

    def end_of_day(self) -> None:
        """The session has ended: every bar not emitted yet can never be traded."""
        if self._next is not None and self._newest is not None:
            s = self._next
            while s <= self._newest:
                self.incomplete.add(s)
                s += self._width
        self.incomplete.update(self._buckets)
        self._buckets.clear()
        self._next = None

    def _release(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        assert self._next is not None and self._newest is not None
        while self._next <= self._newest:
            s = self._next
            bucket = self._buckets.get(s, {})
            ended = self._newest + 60 >= s + self._width  # a minute at or after this bar's last has arrived
            if len(bucket) == self.bar_minutes:
                self._buckets.pop(s, None)
                late = s in self._waited or any(m.get("source") == "backfill" for m in bucket.values())
                if late:
                    self.late.add(s)
                out.append(_aggregate(s, bucket, late))
                self._waited.discard(s)
                self._next = s + self._width
                continue
            if not ended:
                break
            if self._newest + 60 >= s + self._width + GAP_WAIT_S:
                self._buckets.pop(s, None)
                self._waited.discard(s)
                self.incomplete.add(s)
                self.given_up.append(s)
                self._next = s + self._width
                continue
            # waiting on this gap: a bar behind it that is already due is held, and late when released
            q = s
            while q <= self._newest:
                if self._newest + 60 >= q + self._width:
                    self._waited.add(q)
                q += self._width
            break
        return out


def _aggregate(start: int, bucket: dict[int, dict[str, Any]], late: bool) -> dict[str, Any]:
    minutes = [bucket[k] for k in sorted(bucket)]
    out = {
        "time": start,
        "open": float(minutes[0]["open"]),
        "high": max(float(m["high"]) for m in minutes),
        "low": min(float(m["low"]) for m in minutes),
        "close": float(minutes[-1]["close"]),
        "volume": sum(float(m.get("volume") or 0) for m in minutes),
    }
    if late:
        out["late_after_gap"] = True
    return out
