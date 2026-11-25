"""Swing Phase 1b worker: NSE bhavcopy universe, adjustments, filters.

Research only. This process is not the backend and it does not place orders.
Raw zips stay under data/bhavcopy/ and are not committed. Safe to stop and run again.

    cd backend && uv run python -m scripts.swing_phase1b
    uv run python -m scripts.swing_phase1b --download-only
    uv run python -m scripts.swing_phase1b --build-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import httpx
import pandas as pd

from app.config import settings
from app.data.history import read_parquet, symbol_dir_name
from app.swing.bhav_adjust import apply_adjustments, assign_lineages, compare_closes, one_row_per_day, parse_actions
from app.swing.bhav_pit import apply_pit, market_hours_block, render_report, summarize_years, weekday_absent
from app.swing.bhavcopy import (
    HISTORY_FROM,
    NseBlocked,
    archive_urls,
    classify_body,
    collapse_series,
    coverage_complete,
    iter_days,
    parse_bhav_zip,
    raw_path,
    trading_calendar,
    zip_ready,
)
from app.swing.daily import IST
from app.swing.quality import series_path

UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "app" / "swing" / "data" / "swing_universe_v1.json"
CA_URL = "https://www.nseindia.com/api/corporates-corporateActions"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}
PAUSE_S = 0.12
COMPARE_MIN_DAYS = 20
MISMATCH_MEDIAN = 0.01


def refuse_if_live() -> str | None:
    if settings.live_trading:
        return "LIVE_TRADING is true. Swing Phase 1b does not run."
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--from", dest="start", default=HISTORY_FROM.isoformat())
    parser.add_argument("--to", dest="end", default="")
    args = parser.parse_args(argv)
    blocked = refuse_if_live()
    if blocked:
        print(blocked, file=sys.stderr)
        return 2
    now = datetime.now(IST)
    hours = market_hours_block(now)
    if hours:
        print(hours, file=sys.stderr)
        return 2
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else now.date()
    root = settings.bhavcopy_dir
    root.mkdir(parents=True, exist_ok=True)
    if not args.build_only:
        try:
            download_bhav(root, start, end, now.date())
            download_actions(root, start, end)
        except NseBlocked as exc:
            print(str(exc), file=sys.stderr)
            return 3
    if args.download_only:
        print("download finished")
        return 0
    return build(root, start, end)


def download_bhav(root: Path, start: date, end: date, today: date) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    absent = manifest.get("absent", {})
    stored = skipped = newly_absent = errors = 0
    streak = 0
    with httpx.Client(timeout=45, follow_redirects=True, headers=HEADERS) as client:
        for count, day in enumerate(iter_days(start, end), start=1):
            dest = raw_path(root, day)
            if zip_ready(dest):
                skipped += 1
                continue
            if absent.get(day.isoformat()) == "absent" and (today - day).days > 7:
                newly_absent += 1
                continue
            outcome = _fetch_day(client, day, dest)
            time.sleep(PAUSE_S)
            if outcome == "stored":
                stored += 1
                streak = 0
                absent.pop(day.isoformat(), None)
            elif outcome == "absent":
                absent[day.isoformat()] = "absent"
                newly_absent += 1
                streak = 0
            else:
                errors += 1
                streak += 1
                if streak >= 20:
                    _write_manifest(manifest_path, absent)
                    raise NseBlocked(
                        "NSE stopped returning bhavcopy files. Stopped. No mirror was used."
                    )
            if count % 200 == 0:
                print(f"bhavcopy {day.isoformat()} stored {stored} skipped {skipped} absent {newly_absent}")
                _write_manifest(manifest_path, absent)
    _write_manifest(manifest_path, absent)
    print(f"bhavcopy done stored {stored} skipped {skipped} absent {newly_absent} errors {errors}")


def _fetch_day(client: httpx.Client, day: date, dest: Path) -> str:
    for url in archive_urls(day):
        for attempt in range(3):
            try:
                response = client.get(url)
            except httpx.HTTPError:
                time.sleep(1.0 + attempt)
                continue
            kind = classify_body(response.status_code, response.content)
            if kind == "blocked":
                raise NseBlocked(
                    "NSE refused the official bhavcopy archive. Stopped. No mirror was used."
                )
            if kind == "zip":
                _write_bytes(dest, response.content)
                return "stored"
            if kind == "absent":
                if response.status_code != 404:
                    return "error"
                break
            time.sleep(1.0 + attempt)
    return "absent"


def download_actions(root: Path, start: date, end: date) -> None:
    folder = root / "ca"
    folder.mkdir(parents=True, exist_ok=True)
    flag = folder / "_unavailable.txt"
    if flag.is_file():
        flag.unlink()
    with httpx.Client(timeout=45, follow_redirects=True, headers=HEADERS) as client:
        try:
            client.get("https://www.nseindia.com/", headers={**HEADERS, "Accept": "text/html"})
        except httpx.HTTPError:
            (folder / "_unavailable.txt").write_text("warmup failed\n", encoding="utf-8")
            print("corporate actions: NSE did not accept the warmup")
            return
        month = date(start.year, start.month, 1)
        last = date(end.year, end.month, 1)
        previous = _add_months(last, -1)
        while month <= last:
            path = folder / f"{month.year:04d}-{month.month:02d}.json"
            refresh = month >= previous
            if path.is_file() and not refresh:
                month = _add_months(month, 1)
                continue
            stop = min(_month_end(month), end)
            try:
                rows = _fetch_ca(client, month, stop)
            except NseBlocked:
                (folder / "_unavailable.txt").write_text("archive refused\n", encoding="utf-8")
                print("corporate actions: NSE refused the feed. Price ratios stay unconfirmed.")
                return
            except httpx.HTTPError as exc:
                print(f"corporate actions: {month.isoformat()} skipped ({type(exc).__name__})")
                month = _add_months(month, 1)
                continue
            if rows is None:
                (folder / "_unavailable.txt").write_text("archive refused\n", encoding="utf-8")
                print("corporate actions: NSE refused the feed. Price ratios stay unconfirmed.")
                return
            path.write_text(json.dumps(rows), encoding="utf-8")
            print(f"corporate actions {month.isoformat()} rows {len(rows)}")
            time.sleep(PAUSE_S)
            month = _add_months(month, 1)


def _fetch_ca(client: httpx.Client, start: date, end: date) -> list | None:
    params = {
        "index": "equities",
        "from_date": start.strftime("%d-%m-%Y"),
        "to_date": end.strftime("%d-%m-%Y"),
    }
    response = client.get(CA_URL, params=params, headers={**HEADERS, "Accept": "application/json"})
    if response.status_code in (401, 403):
        return None
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else []
    if len(rows) >= 900 and (end - start).days > 6:
        mid = start + timedelta(days=(end - start).days // 2)
        left = _fetch_ca(client, start, mid)
        right = _fetch_ca(client, mid + timedelta(days=1), end)
        if left is None or right is None:
            return None
        return left + right
    return rows


def build(root: Path, start: date, end: date) -> int:
    frames = []
    bad = 0
    seen = 0
    for year_dir in sorted((root / "raw").iterdir()):
        if not year_dir.is_dir():
            continue
        year_frames = []
        for path in sorted(year_dir.glob("*.zip")):
            seen += 1
            if not zip_ready(path):
                continue
            try:
                year_frames.append(parse_bhav_zip(path.read_bytes()))
            except (ValueError, zipfile.BadZipFile) as exc:
                bad += 1
                print(f"skip {path.name}: {type(exc).__name__}", flush=True)
        if year_frames:
            frames.append(pd.concat(year_frames, ignore_index=True))
            print(f"parsed {year_dir.name} files {seen} rows {len(frames[-1])}", flush=True)
    if bad > 30:
        print(f"{bad} bhavcopy files could not be parsed.", file=sys.stderr)
        return 2
    if not frames:
        print("No bhavcopy files to build from.", file=sys.stderr)
        return 2
    bars = collapse_series(pd.concat(frames, ignore_index=True))
    print(f"parsed rows {len(bars)}")
    research = one_row_per_day(assign_lineages(bars))
    actions, covered, ca_note = _load_actions(root)
    research, ledger = apply_adjustments(research, actions, covered)
    research = apply_pit(research)
    sessions = trading_calendar(research)
    if not coverage_complete(sessions[0] if sessions else None, sessions[-1] if sessions else None, start, end):
        first = sessions[0].isoformat() if sessions else "none"
        last = sessions[-1].isoformat() if sessions else "none"
        print(f"Bhavcopy coverage is incomplete ({first} through {last}). No quality table written.", file=sys.stderr)
        return 2
    universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    nifty_isins = {str(row["isin"]).upper() for row in universe["stocks"]}
    nifty_symbols = {str(row["symbol"]).upper() for row in universe["stocks"]}
    years = summarize_years(research, nifty_isins, nifty_symbols)
    mismatches = _compare(research, universe["stocks"])
    renamed = int((research.groupby("lineage")["symbol"].nunique() > 1).sum())
    report = render_report(
        sessions=sessions,
        weekday_absent=weekday_absent(start, end, set(sessions)),
        lineages=int(research["lineage"].nunique()),
        renamed=renamed,
        ledger=ledger,
        ca_note=ca_note,
        years=years,
        mismatches=mismatches,
    )
    out = settings.data_dir / "validation" / f"swing_phase1b_quality_{end.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    _write_json(root / "calendar.json", [day.isoformat() for day in sessions])
    _write_json(root / "adjustments.json", ledger)
    _write_frame(research, root / "universe.parquet")
    print(report)
    print(f"wrote {out}")
    return 0


def _load_actions(root: Path) -> tuple[list[dict], set[tuple[int, int]], str]:
    folder = root / "ca"
    if (folder / "_unavailable.txt").is_file() and not any(folder.glob("*.json")):
        return [], set(), (
            "NSE corporate-action feed did not answer. "
            "Price-ratio adjustments are marked as unconfirmed."
        )
    covered: set[tuple[int, int]] = set()
    rows: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        year, month = int(path.stem[:4]), int(path.stem[5:7])
        covered.add((year, month))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows.extend(payload)
    actions = _dedupe(parse_actions(rows))
    note = (
        f"NSE corporate-action months on file: {len(covered)}. "
        "A split or bonus is applied when the ex-date close is within 25% of the NSE factor. "
        "A split and a bonus on the same day use the product of the two factors. "
        "A price jump with no NSE split or bonus on a covered month is not applied."
    )
    if (folder / "_unavailable.txt").is_file():
        note += " Part of the feed was refused; those months can still match on price ratio alone."
    return actions, covered, note


def _dedupe(actions: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for action in actions:
        key = (action["symbol"], action["ex_date"], action["subject"])
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out


def _compare(frame: pd.DataFrame, stocks: list[dict]) -> dict:
    isin_lineage = dict(zip(frame["isin"].astype(str), frame["lineage"].astype(str), strict=True))
    wanted = {isin_lineage[row["isin"]] for row in stocks if row.get("isin") in isin_lineage}
    subset = frame.loc[frame["lineage"].astype(str).isin(wanted)]
    grouped = {str(key): part.set_index("date") for key, part in subset.groupby("lineage", sort=False)}
    adjusted_bad = raw_bad = compared = missing = 0
    worst = []
    root = settings.candles_daily_dir
    for stock in stocks:
        lineage = isin_lineage.get(str(stock.get("isin")))
        part = grouped.get(str(lineage)) if lineage else None
        path = series_path(root / symbol_dir_name(str(stock["instrument_key"])))
        if part is None or not path.is_file():
            missing += 1
            continue
        other = read_parquet(path)
        dates = pd.to_datetime(other["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
        closes = pd.Series(other["close"].astype(float).to_numpy(), index=pd.Index(dates))
        adj = compare_closes(part["adj_close"], closes)
        raw = compare_closes(part["close"], closes)
        if adj["overlap"] < COMPARE_MIN_DAYS:
            continue
        compared += 1
        if _bad(adj):
            adjusted_bad += 1
            worst.append({
                "symbol": stock["symbol"],
                "overlap": adj["overlap"],
                "median_abs_pct": 100.0 * (adj["median_abs_pct"] or 0.0),
                "over_tol": adj["over_tol"],
            })
        if _bad(raw):
            raw_bad += 1
    worst.sort(key=lambda row: row["median_abs_pct"], reverse=True)
    summary = (
        f"Compared {compared} names with at least {COMPARE_MIN_DAYS} overlapping days. "
        f"Adjusted close: {adjusted_bad} names have a median absolute difference above 1% "
        f"or more than 5% of days beyond 1%. Raw close: {raw_bad} names. "
        f"Names with no shared series: {missing}."
    )
    return {"summary": summary, "worst": worst[:8]}


def _bad(stats: dict) -> bool:
    overlap = stats["overlap"]
    if overlap < COMPARE_MIN_DAYS or stats["median_abs_pct"] is None:
        return False
    return stats["median_abs_pct"] > MISMATCH_MEDIAN or stats["over_tol"] / overlap > 0.05


def _write_manifest(path: Path, absent: dict) -> None:
    _write_json(path, {"absent": absent})


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(payload)
    tmp.replace(path)


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"])
    tmp = path.with_suffix(".parquet.tmp")
    escaped = str(tmp).replace("'", "''")
    con = duckdb.connect()
    try:
        con.register("bhav_frame", out)
        con.execute(f"COPY (SELECT * FROM bhav_frame) TO '{escaped}' (FORMAT PARQUET)")
    finally:
        con.close()
    tmp.replace(path)


def _month_end(day: date) -> date:
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def _add_months(day: date, count: int) -> date:
    month = day.month - 1 + count
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


if __name__ == "__main__":
    sys.exit(main())
