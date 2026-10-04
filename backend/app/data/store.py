"""Candle storage: Parquet files read through DuckDB.

Layout:  <base_dir>/<SYMBOL>/<N>m.parquet   (columns: see importer.COLUMNS)
The finest available file (smallest N) is the base for resampling.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from app.data.candle import Candle
from app.data.history import SymbolMeta, read_meta
from app.data.sessions import DEFAULT_INCLUDED_SESSION_TYPES, SESSION_TYPES

_IST = timezone(timedelta(hours=5, minutes=30))
_FILE_RE = re.compile(r"^(\d+)m\.parquet$")


class SymbolNotFound(KeyError):
    pass


#: provider of live (not yet persisted) rows for a symbol folder; set by the live service.
#: Each row: {"time","open","high","low","close","volume","oi","session_type"}
OverlayProvider = Callable[[str], list[dict]]
_overlay: OverlayProvider | None = None


def set_overlay(provider: OverlayProvider | None) -> None:
    global _overlay  # noqa: PLW0603
    _overlay = provider


def _overlay_rows(symbol: str, minutes: int) -> list[dict]:
    # live rows are 1-minute bars: only meaningful on top of a 1m base file
    return _overlay(symbol) if _overlay is not None and minutes == 1 else []


def _q(path: Path) -> str:
    return str(path).replace("'", "''")


class CandleStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    # ------------------------------------------------------------------ discovery
    def symbols(self) -> list[str]:
        if not self.base_dir.is_dir():
            return []
        return sorted(p.name for p in self.base_dir.iterdir() if p.is_dir() and self._files(p))

    @staticmethod
    def _files(symbol_dir: Path) -> dict[int, Path]:
        found: dict[int, Path] = {}
        for p in symbol_dir.glob("*.parquet"):
            m = _FILE_RE.match(p.name)
            if m:
                found[int(m.group(1))] = p
        return found

    def _base_file(self, symbol: str) -> tuple[int, Path]:
        if symbol not in self.symbols():  # also blocks path traversal
            raise SymbolNotFound(symbol)
        files = self._files(self.base_dir / symbol)
        minutes = min(files)
        return minutes, files[minutes]

    def meta(self, symbol: str) -> SymbolMeta:
        """Instrument info / fetch bookkeeping for a symbol (empty if none was written)."""
        if symbol not in self.symbols():
            raise SymbolNotFound(symbol)
        return read_meta(self.base_dir / symbol)

    def base_minutes(self, symbol: str) -> int:
        return self._base_file(symbol)[0]

    # ------------------------------------------------------------------ queries
    def load(
        self,
        symbol: str,
        *,
        from_time: int | None = None,
        to_time: int | None = None,
        session_types: Iterable[str] = DEFAULT_INCLUDED_SESSION_TYPES,
    ) -> tuple[list[Candle], int]:
        """Candles (ascending, inclusive range on bar start time) and the base bar size.

        Only sessions whose label is in `session_types` are returned."""
        types = list(session_types)
        unknown = [t for t in types if t not in SESSION_TYPES]
        if unknown:
            raise ValueError(f"Unknown session types: {unknown}")
        minutes, path = self._base_file(symbol)
        where: list[str] = []
        params: list[int | str] = []
        if from_time is not None:
            where.append("time >= ?")
            params.append(from_time)
        if to_time is not None:
            where.append("time <= ?")
            params.append(to_time)
        if len(types) < len(SESSION_TYPES):
            if not types:
                return [], minutes
            where.append(f"session_type IN ({', '.join('?' for _ in types)})")
            params.extend(types)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT time, open, high, low, close, volume, oi "
                f"FROM read_parquet('{_q(path)}') {clause} ORDER BY time",
                params,
            ).fetchall()
        finally:
            con.close()
        candles: list[Candle] = [
            {
                "time": int(t),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
                "oi": oi,
            }
            for t, o, h, lo, c, v, oi in rows
        ]
        extra = [
            r
            for r in _overlay_rows(symbol, minutes)
            if (from_time is None or r["time"] >= from_time)
            and (to_time is None or r["time"] <= to_time)
            and r["session_type"] in types
        ]
        if extra:
            by_time = {c["time"]: c for c in candles}
            for r in extra:
                by_time[int(r["time"])] = {
                    "time": int(r["time"]),
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "volume": r["volume"],
                    "oi": r["oi"],
                }
            candles = [by_time[t] for t in sorted(by_time)]
        return candles, minutes

    def dates_of_type(self, symbol: str, session_type: str) -> set[date]:
        """IST dates of sessions with the given label in the stored data."""
        _, path = self._base_file(symbol)
        con = duckdb.connect()
        try:
            times = con.execute(
                f"SELECT DISTINCT time FROM read_parquet('{_q(path)}') WHERE session_type = ?",
                [session_type],
            ).fetchall()
        finally:
            con.close()
        return {datetime.fromtimestamp(int(t), _IST).date() for (t,) in times}

    def time_range(self, symbol: str) -> tuple[int, int]:
        """(first, last) bar start time over ALL stored bars."""
        _, path = self._base_file(symbol)
        con = duckdb.connect()
        try:
            row = con.execute(
                f"SELECT min(time), max(time) FROM read_parquet('{_q(path)}')"
            ).fetchone()
        finally:
            con.close()
        if row is None or row[0] is None or row[1] is None:
            raise SymbolNotFound(symbol)
        first, last = int(row[0]), int(row[1])
        live = _overlay_rows(symbol, self.base_minutes(symbol))
        if live:
            first = min(first, min(int(r["time"]) for r in live))
            last = max(last, max(int(r["time"]) for r in live))
        return first, last
