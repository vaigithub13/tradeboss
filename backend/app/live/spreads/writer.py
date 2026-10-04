"""data/spreads/YYYY-MM-DD.parquet. A restart the same day continues in a part file."""

from __future__ import annotations

from datetime import date, datetime, time as dtime
from pathlib import Path

import duckdb
import pandas as pd

from app.data.importer import parquet_rows
from app.data.publish import publish_file
from app.live.model import IST
from app.live.spreads.rows import COLUMNS

_OPEN = dtime(9, 0)
_CLOSE = dtime(16, 5)


class SpreadWriter:
    def __init__(self, directory: Path, *, enabled: bool = True) -> None:
        self.dir = directory
        self.enabled = enabled
        self._rows: list[dict] = []
        self._path: Path | None = None
        self._day: date | None = None

    @property
    def day(self) -> date | None:
        return self._day

    def append(self, row: dict | None, *, market_open: bool) -> None:
        if not self.enabled or not market_open or not row:
            return
        ts = int(row["ts_ms"])
        when = datetime.fromtimestamp(ts / 1000, IST)
        if not (_OPEN <= when.time() < _CLOSE):
            return
        day = when.date()
        if self._path is None or self._day != day:
            self.flush()
            self._day = day
            self._path = _chunk_path(self.dir, day)
            self._rows = []
        self._rows.append(row)

    def flush(self) -> None:
        if not self._rows or self._path is None:
            return
        _write(pd.DataFrame(self._rows, columns=COLUMNS), self._path)
        self._rows = []
        # The next flush of this same open file continues as a new part, so a
        # crash keeps what was already written and a rewrite never duplicates it.
        self._path = _chunk_path(self.dir, self._day) if self._day is not None else None

    def close(self) -> None:
        self.flush()


def _chunk_path(directory: Path, day: date) -> Path:
    primary = directory / f"{day.isoformat()}.parquet"
    if not primary.exists():
        return primary
    n = 1
    while (directory / f"{day.isoformat()}.part-{n}.parquet").exists():
        n += 1
    return directory / f"{day.isoformat()}.part-{n}.parquet"


def _write(frame: pd.DataFrame, path: Path) -> None:
    if len(frame) == 0:
        return

    def copy(tmp: Path) -> None:
        escaped = str(tmp).replace("'", "''")
        con = duckdb.connect()
        try:
            con.register("spreads_df", frame)
            con.execute(f"COPY (SELECT * FROM spreads_df ORDER BY ts_ms) TO '{escaped}' (FORMAT PARQUET)")
        finally:
            con.close()

    if not publish_file(path, copy, accept=lambda tmp: parquet_rows(tmp) > 0):
        raise OSError(f"refusing to publish an empty {path.name}")


def read_spreads(directory: Path, day: date) -> pd.DataFrame:
    paths = sorted(directory.glob(f"{day.isoformat()}*.parquet"))
    if not paths:
        return pd.DataFrame(columns=COLUMNS)
    frames = []
    con = duckdb.connect()
    try:
        for path in paths:
            escaped = str(path).replace("'", "''")
            frames.append(con.execute(f"SELECT * FROM read_parquet('{escaped}')").df())
    finally:
        con.close()
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values("ts_ms").reset_index(drop=True)
