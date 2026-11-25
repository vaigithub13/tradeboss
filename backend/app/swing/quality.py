"""Coverage and how many stocks clear the filters at each year end."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from app.swing.daily import ADJ_NAME, DAILY_NAME


def _dates(path: Path) -> list[date]:
    if not path.is_file():
        return []
    q = str(path).replace("'", "''")
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"""
            SELECT DISTINCT CAST(timezone('Asia/Kolkata', to_timestamp(time)) AS DATE) AS d
            FROM read_parquet('{q}')
            ORDER BY d
            """
        ).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        value = row[0]
        out.append(value if isinstance(value, date) else date.fromisoformat(str(value)[:10]))
    return out


def coverage(path: Path, *, holiday_from: date | None = None, holidays: set[date] | None = None) -> dict[str, Any]:
    """Missing weekdays between the first and last bar. Holidays are removed only where the file covers them."""
    days = _dates(path)
    if not days:
        return {"bars": 0, "first": None, "last": None, "missing_weekdays": None, "largest_gap_days": None}
    first, last = days[0], days[-1]
    present = set(days)
    holidays = holidays or set()
    missing = 0
    cursor = first
    while cursor <= last:
        if cursor.weekday() < 5 and cursor not in present:
            if holiday_from is None or cursor < holiday_from or cursor not in holidays:
                missing += 1
        cursor += timedelta(days=1)
    largest = max((days[i] - days[i - 1]).days for i in range(1, len(days))) if len(days) > 1 else 0
    return {
        "bars": len(days),
        "first": first.isoformat(),
        "last": last.isoformat(),
        "missing_weekdays": missing,
        "largest_gap_days": largest,
    }


def year_end_passes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """How many stocks pass on the last stored session of each year, by segment.

    `rows` are `{segment, date, passes}` for equity sessions. A later year does not change an earlier count.
    """
    by_year: dict[int, dict[date, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_year[row["date"].year][row["date"]].append(row)
    out = []
    for year in sorted(by_year):
        last = max(by_year[year])
        bucket: dict[str, int] = defaultdict(int)
        for row in by_year[year][last]:
            if row["passes"]:
                bucket[str(row["segment"])] += 1
        out.append({"year": year, "as_of": last.isoformat(), **{key: bucket.get(key, 0) for key in ("large", "mid", "small", "other")}})
    return out


def render_report(universe: dict[str, Any], symbols: list[dict[str, Any]], years: list[dict[str, Any]], *, adjustment: dict[str, Any]) -> str:
    counts = universe["counts"]
    lines = [
        f"# Swing Phase 1 quality ({universe['as_of']})",
        "",
        universe["survivorship_note"],
        "",
        f"Departed or delisted names added since 2015: {counts['departed_since_2015_added']}.",
        f"Nifty 500 names on the CSV: {counts['nifty500']}. Resolved on Upstox: {counts['resolved']}. Not found: {counts['not_found']}.",
        f"Indices stored: {counts['indices']}. Index keys missing from the snapshot: {counts['indices_missing']}.",
        "",
        universe["asm_gsm_note"],
        universe["circuit_note"],
        "",
        f"Daily adjustment: {adjustment.get('verdict')} — {adjustment.get('reason')}",
        "",
        "Missing weekdays use the shipped NSE holiday list, which starts on 2022-01-26. "
        "Earlier holidays are counted as missing, so a median near 100 with a largest gap of a few days is that residue.",
        "Listed at least one year is measured from the first stored daily bar. Every name fails that filter through 2015.",
        "",
        "## Segments",
        "",
        "| Segment | Resolved | Bars median | Missing weekdays median | Largest gap median |",
        "|---|---:|---:|---:|---:|",
    ]
    for segment in ("large", "mid", "small", "other", "index"):
        group = [row for row in symbols if row.get("segment") == segment and row.get("bars")]
        lines.append(
            "| "
            + " | ".join([
                segment,
                str(len(group)),
                _median(row.get("bars") for row in group),
                _median(row.get("missing_weekdays") for row in group),
                _median(row.get("largest_gap_days") for row in group),
            ])
            + " |"
        )
    lines.extend(["", "## Stocks passing on the last session of the year", ""])
    lines.append("| Year | As of | Large | Mid | Small |")
    lines.append("|---:|---|---:|---:|---:|")
    for row in years:
        lines.append(f"| {row['year']} | {row['as_of']} | {row['large']} | {row['mid']} | {row['small']} |")
    if universe.get("not_found"):
        lines.extend(["", "## Not found on Upstox", ""])
        for row in universe["not_found"]:
            lines.append(f"- {row['symbol']} {row['isin']} ({row.get('reason')})")
    failed = [row for row in symbols if row.get("error")]
    if failed:
        lines.extend(["", "## Download errors", ""])
        for row in failed:
            lines.append(f"- {row.get('symbol')}: {row['error']}")
    lines.append("")
    return "\n".join(lines)


def _median(values) -> str:
    nums = sorted(int(v) for v in values if v is not None)
    if not nums:
        return "—"
    mid = len(nums) // 2
    if len(nums) % 2:
        return str(nums[mid])
    return str(round((nums[mid - 1] + nums[mid]) / 2))


def series_path(folder: Path) -> Path:
    adj = folder / ADJ_NAME
    if adj.is_file():
        return adj
    return folder / DAILY_NAME
