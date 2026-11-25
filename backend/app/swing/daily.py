"""Resumable daily OHLCV. Upstox allows one decade of daily bars per request.

A daily print is stored as one bar at 09:15 IST on its session date. The intraday
session filter is not used: Upstox stamps daily candles at midnight.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.data.history import (
    SESSION_DONE_AFTER,
    DateRange,
    clip_source_empty,
    merge_ranges,
    note_source_empty,
    ranges_to_fetch,
    read_parquet,
    split_windows,
    symbol_dir_name,
    write_parquet_atomic,
)
from app.data.importer import COLUMNS
from app.data.publish import publish_file
from app.data.sessions import MUHURAT_DATES
from app.research.corporate_actions import apply_adjustments
from app.upstox.client import UpstoxClient, parse_candles
from app.upstox.instruments import Instrument

IST = timezone(timedelta(hours=5, minutes=30))
HISTORY_FROM = date(2015, 1, 1)
DECADE_DAYS = 365 * 9
DAILY_NAME = "daily.parquet"
ADJ_NAME = "daily_adj.parquet"
LEDGER_NAME = "adjustments.json"

# Phase A check, repeated on the daily series: Kotak 1:5 split, ex 2026-01-14.
KOTAK_SPLIT = {
    "symbol": "KOTAKBANK",
    "isin": "INE237A01028",
    "ex_date": date(2026, 1, 14),
    "ratio": "1:5",
    "price_factor": 0.2,
}


def session_stamp(day: date) -> int:
    """09:15 IST, so the bar sits on the session date."""
    return int(datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST).timestamp())


def daily_session_type(day: date) -> str:
    if day in MUHURAT_DATES:
        return "muhurat"
    if day.weekday() >= 5:
        return "weekend_full"
    return "normal"


def bars_to_frame(raw_rows: list) -> pd.DataFrame:
    """One row per IST date. A midnight stamp stays on that date instead of being dropped."""
    parsed = parse_candles(raw_rows)
    by_day: dict[date, dict[str, Any]] = {}
    for bar in parsed:
        local = datetime.fromtimestamp(int(bar["t"]) // 1000, IST).date()
        by_day[local] = {
            "time": session_stamp(local),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar["volume"] or 0),
            "oi": float("nan") if bar.get("oi") is None else float(bar["oi"]),
            "session_type": daily_session_type(local),
        }
    if not by_day:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame([by_day[day] for day in sorted(by_day)], columns=COLUMNS)
    frame["oi"] = frame["oi"].astype("float64")
    return frame


def adjustment_verdict(pre_close: float, post_close: float, price_factor: float) -> str:
    """Same check as Phase A: a raw 1:5 series jumps about 5x; an adjusted series does not."""
    if pre_close <= 0 or post_close <= 0 or price_factor <= 0:
        return "unknown"
    ratio = pre_close / post_close
    unadjusted = 1.0 / price_factor
    if abs(ratio - unadjusted) / unadjusted <= 0.25:
        return "unadjusted"
    if 0.75 <= ratio <= 1.25:
        return "already_adjusted"
    return "unknown"


def _meta_path(symbol_dir: Path) -> Path:
    return symbol_dir / "meta.json"


def read_daily_meta(symbol_dir: Path) -> dict[str, Any]:
    path = _meta_path(symbol_dir)
    if not path.is_file():
        return {"instrument": {}, "covered_daily": [], "source_empty": []}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"instrument": {}, "covered_daily": [], "source_empty": []}
    return raw


def write_daily_meta(symbol_dir: Path, meta: dict[str, Any]) -> None:
    symbol_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(meta, indent=1, sort_keys=True) + "\n"

    def _write(tmp: Path) -> None:
        tmp.write_text(payload, encoding="utf-8")

    publish_file(_meta_path(symbol_dir), _write, accept=lambda tmp: tmp.is_file() and tmp.stat().st_size > 0)


def _covered(meta: dict[str, Any]) -> list[DateRange]:
    out = []
    for pair in meta.get("covered_daily") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            out.append((date.fromisoformat(str(pair[0])), date.fromisoformat(str(pair[1]))))
    return merge_ranges(out)


def _empty(meta: dict[str, Any]):
    from app.data.history import SourceEmpty

    rows = []
    for item in meta.get("source_empty") or []:
        if isinstance(item, dict):
            rows.append(SourceEmpty(
                date.fromisoformat(str(item["from"])),
                date.fromisoformat(str(item["to"])),
                date.fromisoformat(str(item["tried"])),
            ))
    return rows


def _dump_empty(rows) -> list[dict[str, str]]:
    return [
        {"from": row.start.isoformat(), "to": row.end.isoformat(), "tried": row.tried.isoformat()}
        for row in rows
    ]


def trim_open_tail(covered: list[DateRange], last_bar: date | None, today: date) -> list[DateRange]:
    """A range that runs up to today but has no bar yet is asked again. Older ranges stay covered."""
    if last_bar is None:
        return list(covered)
    horizon = today - timedelta(days=7)
    out: list[DateRange] = []
    for start, end in covered:
        if end >= horizon and end > last_bar:
            end = last_bar
        if start <= end:
            out.append((start, end))
    return merge_ranges(out)


def _last_bar_date(path: Path) -> date | None:
    if not path.is_file():
        return None
    q = str(path).replace("'", "''")
    con = duckdb.connect()
    try:
        row = con.execute(
            f"SELECT MAX(CAST(timezone('Asia/Kolkata', to_timestamp(time)) AS DATE)) FROM read_parquet('{q}')"
        ).fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        return None
    value = row[0]
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def sync_daily(
    client: UpstoxClient,
    root: Path,
    instrument: Instrument,
    *,
    from_date: date = HISTORY_FROM,
    to_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch missing daily windows. Coverage is saved after each window."""
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    end = min(to_date or today, today)
    if now.time() < SESSION_DONE_AFTER:
        end = min(end, today - timedelta(days=1))
    start = from_date
    folder = root / symbol_dir_name(instrument.key)
    parquet = folder / DAILY_NAME
    meta = read_daily_meta(folder)
    meta["instrument"] = instrument.as_dict()
    covered = trim_open_tail(_covered(meta), _last_bar_date(parquet), today)
    empty = _empty(meta)
    gaps = ranges_to_fetch(covered, empty, start, end, today) if start <= end else []
    windows = [w for gap in gaps for w in split_windows(gap, DECADE_DAYS)]
    fetched = 0
    for window in windows:
        rows = client.historical_candles(instrument.key, window[0], window[1], unit="days", interval=1)
        frame = bars_to_frame(rows)
        if len(frame) == 0:
            empty = note_source_empty(empty, window, today)
        else:
            merged = _merge(read_parquet(parquet), frame, window)
            write_parquet_atomic(merged, parquet)
            last_day = datetime.fromtimestamp(int(frame["time"].max()), IST).date()
            cover_end = last_day if window[1] >= today - timedelta(days=7) and last_day < window[1] else window[1]
            covered = merge_ranges([*covered, (window[0], cover_end)])
            empty = clip_source_empty(empty, window)
        meta["covered_daily"] = [[a.isoformat(), b.isoformat()] for a, b in covered]
        meta["source_empty"] = _dump_empty(empty)
        write_daily_meta(folder, meta)
        fetched += 1
    if not windows:
        meta["covered_daily"] = [[a.isoformat(), b.isoformat()] for a, b in covered]
        meta["source_empty"] = _dump_empty(empty)
        write_daily_meta(folder, meta)
    total = int(len(read_parquet(parquet))) if parquet.is_file() else 0
    return {"folder": folder.name, "windows": fetched, "bars": total}


def _merge(existing: pd.DataFrame, new: pd.DataFrame, window: DateRange) -> pd.DataFrame:
    lo = session_stamp(window[0])
    hi = session_stamp(window[1] + timedelta(days=1))
    if len(existing):
        keep = existing[(existing["time"] < lo) | (existing["time"] >= hi)]
    else:
        keep = existing
    frames = [frame for frame in (keep, new) if len(frame)]
    if not frames:
        return existing.iloc[0:0]
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset="time", keep="last").sort_values("time", ignore_index=True)
    merged["oi"] = merged["oi"].astype("float64")
    return merged[COLUMNS]


def closes_around(path: Path, ex_date: date) -> tuple[float, float] | None:
    """Last close before the ex-date and the first close on or after it."""
    if not path.is_file():
        return None
    q = str(path).replace("'", "''")
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"""
            SELECT CAST(timezone('Asia/Kolkata', to_timestamp(time)) AS DATE) AS d, close
            FROM read_parquet('{q}')
            ORDER BY time
            """
        ).fetchall()
    finally:
        con.close()
    before = [row for row in rows if _as_date(row[0]) < ex_date]
    after = [row for row in rows if _as_date(row[0]) >= ex_date]
    if not before or not after:
        return None
    return float(before[-1][1]), float(after[0][1])


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def write_daily_adjusted(
    symbol_dir: Path,
    *,
    as_of: date,
    verdict: str,
    reason: str,
    actions: list | None = None,
) -> dict[str, Any]:
    """Identity copy when Upstox daily is already adjusted. Apply factors only when it is not."""
    raw = symbol_dir / DAILY_NAME
    if not raw.is_file():
        raise FileNotFoundError(raw)
    actions = actions or []
    applied: list[dict[str, Any]] = []
    if verdict == "unadjusted":
        frame = read_parquet(raw)
        adjusted = apply_adjustments(frame, actions, as_of=as_of)
        write_parquet_atomic(adjusted, symbol_dir / ADJ_NAME)
        applied = [
            {"kind": a.kind, "ex_date": a.ex_date.isoformat(), "ratio": a.ratio, "price_factor": a.price_factor}
            for a in actions
            if a.price_factor is not None and a.kind.lower() in ("split", "bonus")
        ]
        source = "applied_factors"
    else:
        def _copy(tmp: Path) -> None:
            shutil.copyfile(raw, tmp)

        publish_file(symbol_dir / ADJ_NAME, _copy, accept=lambda p: p.is_file() and p.stat().st_size > 0)
        source = "identity_copy"
    ledger = {
        "as_of": as_of.isoformat(),
        "source": source,
        "verdict": verdict,
        "reason": reason,
        "raw": DAILY_NAME,
        "adjusted": ADJ_NAME,
        "applied_splits_bonuses": applied,
    }
    payload = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    publish_file(
        symbol_dir / LEDGER_NAME,
        lambda path: path.write_text(payload, encoding="utf-8"),
        accept=lambda path: path.is_file() and path.stat().st_size > 0,
    )
    return ledger
