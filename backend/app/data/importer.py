"""Convert raw tape bars into labeled Candle rows and write Parquet.

Rules (reported, never silent):
  * sessions on MUHURAT dates are kept in full (any clock time)
  * every other bar must START inside the NSE session [09:15, 15:30) IST; pre-open
    (09:10), closing (15:30) and post-close prints are dropped
  * each IST date is one session; its label comes from sessions.classify_session
    (normal / weekend_full / special_short / muhurat) and is stored on every bar
  * sorted ascending by time, de-duplicated on time (last one wins)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.data.publish import publish_file

from app.data.sessions import (
    MUHURAT_DATES,
    SESSION_CLOSE_MIN,
    SESSION_OPEN_MIN,
    classify_session,
)

IST = timezone(timedelta(hours=5, minutes=30))

COLUMNS = ["time", "open", "high", "low", "close", "volume", "oi", "session_type"]


@dataclass(frozen=True)
class ImportReport:
    read: int
    kept: int
    dropped_out_of_session: int
    duplicates: int
    sessions_by_type: dict[str, int] = field(default_factory=dict)


def build_frame(
    raw_bars: Iterable[Mapping[str, Any]],
    bar_minutes: int = 5,
    *,
    keep_oi: bool = False,
    in_progress_date: date | None = None,
) -> tuple[pd.DataFrame, ImportReport]:
    """raw bar = {"t": unix ms, "open", "high", "low", "close", "volume"[, "oi"]}.

    keep_oi:          store the bar's "oi" (futures / options); otherwise oi stays null.
    in_progress_date: an IST date whose session is still running (partial data). It is labeled by
                      its weekday (normal / weekend_full) instead of special_short, as long as it
                      opened on time; the real label is computed once the day is complete.
    """
    rows: dict[int, dict[str, Any]] = {}
    read = dropped = duplicates = 0

    for c in raw_bars:
        read += 1
        t = int(c["t"]) // 1000
        local = datetime.fromtimestamp(t, IST)
        minute = local.hour * 60 + local.minute
        if local.date() not in MUHURAT_DATES and not (
            SESSION_OPEN_MIN <= minute < SESSION_CLOSE_MIN
        ):
            dropped += 1
            continue
        if t in rows:
            duplicates += 1
        rows[t] = {
            "time": t,
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c["volume"]),
            "oi": float(c["oi"]) if keep_oi and c.get("oi") is not None else None,
            "session_type": "",
        }

    # label per session (IST date), from the kept bars only
    minutes_by_day: dict[date, list[int]] = {}
    for t in rows:
        local = datetime.fromtimestamp(t, IST)
        minutes_by_day.setdefault(local.date(), []).append(local.hour * 60 + local.minute)
    label_by_day = {d: classify_session(d, m, bar_minutes) for d, m in minutes_by_day.items()}
    if (
        in_progress_date in label_by_day
        and label_by_day[in_progress_date] == "special_short"
        and min(minutes_by_day[in_progress_date]) <= SESSION_OPEN_MIN
    ):
        label_by_day[in_progress_date] = "weekend_full" if in_progress_date.weekday() >= 5 else "normal"
    for t, row in rows.items():
        row["session_type"] = label_by_day[datetime.fromtimestamp(t, IST).date()]

    df = pd.DataFrame([rows[k] for k in sorted(rows)], columns=COLUMNS)
    df["oi"] = df["oi"].astype("float64")
    return df, ImportReport(
        read=read,
        kept=len(df),
        dropped_out_of_session=dropped,
        duplicates=duplicates,
        sessions_by_type=dict(Counter(label_by_day.values())),
    )


def parquet_rows(path: Path) -> int:
    """Row count of a parquet file. Unreadable files raise; they are not published."""
    escaped = str(path).replace("'", "''")
    con = duckdb.connect()
    try:
        row = con.execute(f"SELECT count(*) FROM read_parquet('{escaped}')").fetchone()
    finally:
        con.close()
    return 0 if row is None else int(row[0])


def _copy_parquet(df: pd.DataFrame, path: Path) -> None:
    escaped = str(path).replace("'", "''")
    con = duckdb.connect()
    try:
        con.register("candles_df", df)
        con.execute(f"COPY (SELECT * FROM candles_df ORDER BY time) TO '{escaped}' (FORMAT PARQUET)")
    finally:
        con.close()


def write_parquet(df: pd.DataFrame, path: Path, *, allow_empty: bool = False) -> None:
    """Publish ``path`` only when the temp parquet is readable.

    An empty frame is not a download result: the destination is left untouched
    unless ``allow_empty`` is set (the store test that plants a zero-row file).
    """
    if len(df) == 0 and not allow_empty:
        return

    def accept(tmp: Path) -> bool:
        rows = parquet_rows(tmp)
        return rows > 0 or (allow_empty and rows == 0)

    if not publish_file(path, lambda tmp: _copy_parquet(df, tmp), accept=accept):
        raise OSError(f"refusing to publish {path.name}: the write was empty or unreadable")
