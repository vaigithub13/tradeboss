"""In-memory copy of today's live bars, merged into candle reads.

`CandleStore.load` / `time_range` ask the provider set with `store.set_overlay()` for extra rows, so `/api/candles`,
`/api/indicators` and the live hub all see one consistent picture without rewriting the 440k-row
Parquet file every minute. The overlay is replaced wholesale per instrument by the live service.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import datetime

from app.data.sessions import MUHURAT_DATES
from app.live.model import IST, Bar


def session_type_of(time_s: int) -> str:
    d = datetime.fromtimestamp(time_s, IST).date()
    if d in MUHURAT_DATES:
        return "muhurat"
    return "weekend_full" if d.weekday() >= 5 else "normal"


class LiveOverlay:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, dict[int, dict]] = {}

    @staticmethod
    def _row(b: Bar) -> dict:
        return {
                "time": b.time_s,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": 0.0 if b.volume is None else float(b.volume),
                "oi": b.oi,
                "session_type": session_type_of(b.time_s),
        }

    def replace(self, dir_name: str, bars: Iterable[Bar]) -> None:
        rows = {b.time_s: self._row(b) for b in bars}
        with self._lock:
            if rows:
                self._rows[dir_name] = rows
            else:
                self._rows.pop(dir_name, None)

    def upsert(self, dir_name: str, bars: Iterable[Bar]) -> None:
        new = {b.time_s: self._row(b) for b in bars}
        if new:
            with self._lock:
                self._rows.setdefault(dir_name, {}).update(new)

    def rows(self, dir_name: str) -> list[dict]:
        with self._lock:
            d = self._rows.get(dir_name)
            return [d[t] for t in sorted(d)] if d else []

    def clear(self, dir_name: str | None = None) -> None:
        with self._lock:
            if dir_name is None:
                self._rows.clear()
            else:
                self._rows.pop(dir_name, None)
