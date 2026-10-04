"""Historical 1m ingestion: only the missing date ranges, merged into <symbol>/1m.parquet.

Layout:  <candles>/<symbol_dir>/1m.parquet   candles (see importer.COLUMNS)
         <candles>/<symbol_dir>/meta.json    instrument info + the IST date ranges already fetched

* 1m is the single source of truth; every other timeframe is resampled from it.
* A fetched window is whole IST days. A window that returns bars is covered, including the holidays
  inside it. A window that returns nothing is source_empty (the date it was tried), not covered,
  and is asked again after a week.
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

from app.data.importer import COLUMNS, build_frame, write_parquet
from app.upstox.client import UpstoxClient, parse_candles
from app.upstox.instruments import NIFTY_INDEX_KEY, Instrument

IST = timezone(timedelta(hours=5, minutes=30))

#: Upstox minute data starts January 2022
MIN_HISTORY_DATE = date(2022, 1, 1)
#: Upstox: 1-15 minute candles are limited to ONE MONTH per request; stay clearly inside it
WINDOW_DAYS = 28
#: the session is considered "still running" until shortly after the 15:30 close
SESSION_DONE_AFTER = time(15, 45)
#: an empty Upstox answer is tried again after this many days, and not sooner
EMPTY_RETRY = timedelta(days=7)

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
    """Publish a candle file only when it contains rows. An empty frame changes nothing."""
    write_parquet(df, path)


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


@dataclass(frozen=True)
class SourceEmpty:
    """A window Upstox answered with no candles, and the IST date that answer was received."""

    start: date
    end: date
    tried: date


@dataclass
class SymbolMeta:
    instrument: dict[str, Any] = field(default_factory=dict)
    covered: list[DateRange] = field(default_factory=list)  # 1m date ranges that returned bars
    source_empty: list[SourceEmpty] = field(default_factory=list)


def meta_path(symbol_dir: Path) -> Path:
    return symbol_dir / "meta.json"


def _parse_source_empty(raw: object) -> list[SourceEmpty]:
    out: list[SourceEmpty] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            SourceEmpty(
                date.fromisoformat(str(item["from"])),
                date.fromisoformat(str(item["to"])),
                date.fromisoformat(str(item["tried"])),
            )
        )
    return _merge_source_empty(out)


def read_meta(symbol_dir: Path) -> SymbolMeta:
    p = meta_path(symbol_dir)
    if not p.is_file():
        return SymbolMeta()
    try:
        raw = json.loads(p.read_text())
        covered = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in raw.get("covered_1m", [])]
        return SymbolMeta(
            instrument=dict(raw.get("instrument", {})),
            covered=merge_ranges(covered),
            source_empty=_parse_source_empty(raw.get("source_empty", [])),
        )
    except (ValueError, TypeError, KeyError):
        return SymbolMeta()  # unreadable bookkeeping: refetch rather than trust it


def write_meta(symbol_dir: Path, meta: SymbolMeta) -> None:
    symbol_dir.mkdir(parents=True, exist_ok=True)
    p = meta_path(symbol_dir)
    tmp = p.with_name(p.name + ".tmp")
    rows = _merge_source_empty(meta.source_empty)
    tmp.write_text(
        json.dumps(
            {
                "instrument": meta.instrument,
                "covered_1m": [[a.isoformat(), b.isoformat()] for a, b in merge_ranges(meta.covered)],
                "source_empty": [
                    {"from": r.start.isoformat(), "to": r.end.isoformat(), "tried": r.tried.isoformat()} for r in rows
                ],
            },
            indent=1,
        )
    )
    os.replace(tmp, p)


def _merge_source_empty(rows: Iterable[SourceEmpty]) -> list[SourceEmpty]:
    """Join overlapping or adjacent holes that were tried on the same day."""
    out: list[SourceEmpty] = []
    for row in sorted(rows, key=lambda r: (r.tried, r.start, r.end)):
        if row.start > row.end:
            continue
        if out and out[-1].tried == row.tried and row.start <= out[-1].end + timedelta(days=1):
            if row.end > out[-1].end:
                out[-1] = SourceEmpty(out[-1].start, row.end, row.tried)
        else:
            out.append(row)
    return sorted(out, key=lambda r: (r.start, r.end, r.tried))


def clip_source_empty(rows: Iterable[SourceEmpty], window: DateRange) -> list[SourceEmpty]:
    """Drop the part of each hole that `window` now covers."""
    a, b = window
    out: list[SourceEmpty] = []
    for row in rows:
        if row.end < a or row.start > b:
            out.append(row)
            continue
        if row.start < a:
            out.append(SourceEmpty(row.start, a - timedelta(days=1), row.tried))
        if row.end > b:
            out.append(SourceEmpty(b + timedelta(days=1), row.end, row.tried))
    return _merge_source_empty(out)


def note_source_empty(rows: Iterable[SourceEmpty], window: DateRange, tried: date) -> list[SourceEmpty]:
    """Remember that `window` came back empty on `tried`."""
    return _merge_source_empty([*clip_source_empty(rows, window), SourceEmpty(window[0], window[1], tried)])


def ranges_to_fetch(
    covered: Iterable[DateRange],
    source_empty: Iterable[SourceEmpty],
    start: date,
    end: date,
    today: date,
) -> list[DateRange]:
    """Gaps still worth asking for. A hole tried less than a week ago waits."""
    held = list(covered)
    for row in source_empty:
        if today < row.tried + EMPTY_RETRY:
            held.append((row.start, row.end))
    return missing_ranges(held, start, end)


def reclassify_empty_coverage(symbol_dir: Path, today: date) -> SymbolMeta:
    """A covered range with no candles was an empty answer stored as success. Record it as a hole.

    A real file (any rows) is left alone: holidays inside a window that returned bars stay covered.
    """
    meta = read_meta(symbol_dir)
    if not meta.covered:
        return meta
    parquet = symbol_dir / "1m.parquet"
    if parquet.is_file() and parquet.stat().st_size >= 4096:
        return meta
    rows = len(read_parquet(parquet)) if parquet.is_file() else 0
    if rows > 0:
        return meta
    tried = today
    stamp = parquet if parquet.is_file() else meta_path(symbol_dir)
    if stamp.is_file():
        tried = datetime.fromtimestamp(stamp.stat().st_mtime, IST).date()
    for start, end in meta.covered:
        meta.source_empty = note_source_empty(meta.source_empty, (start, end), tried)
    meta.covered = []
    write_meta(symbol_dir, meta)
    if parquet.is_file():
        parquet.unlink()
    return read_meta(symbol_dir)


def source_empty_report(candles_dir: Path, *, today: date | None = None) -> list[dict[str, str]]:
    """Every contract whose 1m coverage is a hole, after empty files are reclassified."""
    when = today or datetime.now(IST).date()
    out: list[dict[str, str]] = []
    if not candles_dir.is_dir():
        return out
    for symbol_dir in sorted(p for p in candles_dir.iterdir() if p.is_dir()):
        meta = reclassify_empty_coverage(symbol_dir, when)
        inst = meta.instrument
        for row in meta.source_empty:
            out.append(
                {
                    "symbol": symbol_dir.name,
                    "instrument_key": str(inst.get("instrument_key") or ""),
                    "name": str(inst.get("symbol") or symbol_dir.name),
                    "expiry": str(inst.get("expiry") or ""),
                    "from": row.start.isoformat(),
                    "to": row.end.isoformat(),
                    "tried": row.tried.isoformat(),
                }
            )
    return out


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
    gaps = ranges_to_fetch(meta.covered, meta.source_empty, start, historical_end, today)
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
        if len(new) == 0:
            meta.source_empty = note_source_empty(meta.source_empty, window, today)
        else:
            merged = merge_window(read_parquet(parquet), new, window)
            write_parquet_atomic(merged, parquet)
            meta.covered = merge_ranges([*meta.covered, window])
            meta.source_empty = clip_source_empty(meta.source_empty, window)
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
