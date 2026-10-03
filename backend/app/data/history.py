"""Historical 1m ingestion: only the missing date ranges, merged into <symbol>/1m.parquet.

Layout:  <candles>/<symbol_dir>/1m.parquet   candles (see importer.COLUMNS)
         <candles>/<symbol_dir>/meta.json    instrument info + the IST date ranges already fetched

* 1m is the single source of truth; every other timeframe is resampled from it.
* A fetched window is whole IST days. A day with no bars (holiday / not listed yet) still counts
  as covered, so it is never asked for again.
* "Today" is never marked covered (the day is not finished): it is refreshed from the intraday
  endpoint every time. Everything before today comes from the historical endpoint, in windows of
  at most WINDOW_DAYS (Upstox allows one month per request for 1-15 minute data).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.data.importer import COLUMNS, build_frame
from app.upstox.client import UpstoxClient, parse_candles
from app.upstox.instruments import NIFTY_INDEX_KEY, Instrument

IST = timezone(timedelta(hours=5, minutes=30))

#: Upstox minute data starts January 2022
MIN_HISTORY_DATE = date(2022, 1, 1)
#: Upstox: 1-15 minute candles are limited to ONE MONTH per request; stay clearly inside it
WINDOW_DAYS = 28
#: the session is considered "still running" until shortly after the 15:30 close
SESSION_DONE_AFTER = time(15, 45)

#: keep the folder of the original Nifty sample so it stays a fixture and cross-check source
DIR_ALIASES = {NIFTY_INDEX_KEY: "NIFTY50"}

DateRange = tuple[date, date]


# ------------------------------------------------------------------ naming
def symbol_dir_name(instrument_key: str) -> str:
    """Filename-safe folder name for an instrument key (NSE_EQ|INE002A01018 -> NSE_EQ_INE002A01018)."""
    alias = DIR_ALIASES.get(instrument_key)
    if alias:
        return alias
    name = re.sub(r"[^A-Za-z0-9]+", "_", instrument_key).strip("_")
    if not name:
        raise ValueError(f"cannot make a folder name from {instrument_key!r}")
    return name


# ------------------------------------------------------------------ date-range arithmetic (pure)
def merge_ranges(ranges: Iterable[DateRange]) -> list[DateRange]:
    """Sort and merge overlapping or adjacent inclusive date ranges."""
    out: list[DateRange] = []
    for a, b in sorted(r for r in ranges if r[0] <= r[1]):
        if out and a <= out[-1][1] + timedelta(days=1):
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def missing_ranges(covered: Iterable[DateRange], start: date, end: date) -> list[DateRange]:
    """The parts of [start, end] (inclusive) not inside `covered`, ascending."""
    if start > end:
        return []
    gaps: list[DateRange] = []
    cursor = start
    for a, b in merge_ranges(covered):
        if b < cursor:
            continue
        if a > end:
            break
        if a > cursor:
            gaps.append((cursor, min(a - timedelta(days=1), end)))
        cursor = max(cursor, b + timedelta(days=1))
        if cursor > end:
            break
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def split_windows(rng: DateRange, max_days: int = WINDOW_DAYS) -> list[DateRange]:
    """Cut a range into consecutive windows of at most `max_days` days (ascending)."""
    a, b = rng
    out: list[DateRange] = []
    while a <= b:
        e = min(b, a + timedelta(days=max_days - 1))
        out.append((a, e))
        a = e + timedelta(days=1)
    return out


# ------------------------------------------------------------------ files
def _q(path: Path) -> str:
    return str(path).replace("'", "''")


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=COLUMNS).astype(
            {"time": "int64", "oi": "float64", "volume": "float64"}
        )
    con = duckdb.connect()
    try:
        return con.execute(f"SELECT * FROM read_parquet('{_q(path)}') ORDER BY time").df()
    finally:
        con.close()


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    con = duckdb.connect()
    try:
        con.register("candles_df", df)
        con.execute(f"COPY (SELECT * FROM candles_df ORDER BY time) TO '{_q(tmp)}' (FORMAT PARQUET)")
    finally:
        con.close()
    os.replace(tmp, path)


def day_start_ts(d: date) -> int:
    """Unix seconds of 00:00 IST on `d`."""
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())


def merge_window(existing: pd.DataFrame, new: pd.DataFrame, window: DateRange) -> pd.DataFrame:
    """Replace everything stored for the IST days in `window` with `new` (whole-day windows)."""
    lo, hi = day_start_ts(window[0]), day_start_ts(window[1] + timedelta(days=1))
    keep = existing[(existing["time"] < lo) | (existing["time"] >= hi)]
    frames = [f for f in (keep, new) if len(f)]
    if not frames:
        return existing.iloc[0:0]
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset="time", keep="last").sort_values("time", ignore_index=True)
    merged["oi"] = merged["oi"].astype("float64")
    return merged[COLUMNS]


@dataclass
class SymbolMeta:
    instrument: dict[str, Any] = field(default_factory=dict)
    covered: list[DateRange] = field(default_factory=list)  # 1m date ranges already fetched


def meta_path(symbol_dir: Path) -> Path:
    return symbol_dir / "meta.json"


def read_meta(symbol_dir: Path) -> SymbolMeta:
    p = meta_path(symbol_dir)
    if not p.is_file():
        return SymbolMeta()
    try:
        raw = json.loads(p.read_text())
        covered = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in raw.get("covered_1m", [])]
        return SymbolMeta(instrument=dict(raw.get("instrument", {})), covered=merge_ranges(covered))
    except (ValueError, TypeError, KeyError):
        return SymbolMeta()  # unreadable bookkeeping: refetch rather than trust it


def write_meta(symbol_dir: Path, meta: SymbolMeta) -> None:
    symbol_dir.mkdir(parents=True, exist_ok=True)
    p = meta_path(symbol_dir)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "instrument": meta.instrument,
                "covered_1m": [[a.isoformat(), b.isoformat()] for a, b in merge_ranges(meta.covered)],
            },
            indent=1,
        )
    )
    os.replace(tmp, p)


# ------------------------------------------------------------------ sync
@dataclass
class SyncProgress:
    windows_total: int = 0
    windows_done: int = 0
    bars_added: int = 0
    message: str = ""


@dataclass(frozen=True)
class SyncResult:
    symbol_dir: str
    windows_fetched: int
    bars_in_file: int
    today_refreshed: bool
    requested: DateRange


def _bar_count(df: pd.DataFrame) -> int:
    return int(len(df))


def sync_symbol(
    client: UpstoxClient,
    candles_dir: Path,
    instrument: Instrument,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    now: datetime | None = None,
    on_progress: Callable[[SyncProgress], None] | None = None,
) -> SyncResult:
    """Fetch the 1m candles missing between from_date and to_date (default: all history up to
    now) and merge them into the symbol's 1m.parquet. Safe to interrupt: coverage is saved after
    every window, so the next run continues where this one stopped."""
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    start = max(from_date or MIN_HISTORY_DATE, MIN_HISTORY_DATE)
    end = min(to_date or today, today)
    sdir = candles_dir / symbol_dir_name(instrument.key)
    parquet = sdir / "1m.parquet"
    meta = read_meta(sdir)
    meta.instrument = instrument.as_dict()

    historical_end = min(end, today - timedelta(days=1))
    gaps = missing_ranges(meta.covered, start, historical_end)
    windows: list[DateRange] = [w for g in gaps for w in split_windows(g)]
    windows.sort(reverse=True)  # newest first: the most useful data lands first
    do_today = end >= today and start <= today
    progress = SyncProgress(windows_total=len(windows) + (1 if do_today else 0))
    in_progress_date = today if now.time() < SESSION_DONE_AFTER else None

    def report(msg: str) -> None:
        progress.message = msg
        if on_progress:
            on_progress(SyncProgress(**vars(progress)))

    report("starting")
    fetched = 0
    for window in windows:
        report(f"fetching {window[0]} .. {window[1]}")
        rows = client.historical_candles(instrument.key, window[0], window[1])
        new, _ = build_frame(
            parse_candles(rows), bar_minutes=1, keep_oi=instrument.has_oi, in_progress_date=in_progress_date
        )
        merged = merge_window(read_parquet(parquet), new, window)
        write_parquet_atomic(merged, parquet)
        meta.covered = merge_ranges([*meta.covered, window])
        write_meta(sdir, meta)  # resumable
        fetched += 1
        progress.windows_done += 1
        progress.bars_added += _bar_count(new)
        report(f"fetched {window[0]} .. {window[1]} ({len(new)} bars)")

    today_done = False
    if do_today:
        report("fetching today (intraday)")
        rows = client.intraday_candles(instrument.key)
        new, _ = build_frame(
            parse_candles(rows), bar_minutes=1, keep_oi=instrument.has_oi, in_progress_date=in_progress_date
        )
        if len(new):
            merged = merge_window(read_parquet(parquet), new, (today, today))
            write_parquet_atomic(merged, parquet)
        write_meta(sdir, meta)  # also persists instrument info
        today_done = True
        progress.windows_done += 1
        progress.bars_added += _bar_count(new)
    else:
        write_meta(sdir, meta)

    total = int(len(read_parquet(parquet))) if parquet.is_file() else 0
    report("done")
    return SyncResult(
        symbol_dir=sdir.name,
        windows_fetched=fetched,
        bars_in_file=total,
        today_refreshed=today_done,
        requested=(start, end),
    )
