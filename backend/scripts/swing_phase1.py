"""Swing Phase 1 worker: universe, daily bars, filters, quality report.

Research only. This process is not the backend and it does not place orders.
Safe to stop and run again: each daily window is saved before the next one starts.

    cd backend && uv run python -m scripts.swing_phase1
    uv run python -m scripts.swing_phase1 --universe-only
    uv run python -m scripts.swing_phase1 --skip-sync
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd

from app.config import settings
from app.data.history import symbol_dir_name
from app.data.importer import write_parquet
from app.research.corporate_actions import fetch_corporate_actions
from app.swing.daily import (
    HISTORY_FROM,
    KOTAK_SPLIT,
    adjustment_verdict,
    closes_around,
    sync_daily,
    write_daily_adjusted,
)
from app.swing.filters import apply_filters
from app.swing.quality import coverage, render_report, series_path, year_end_passes
from app.swing.universe import (
    ASM_NOTE,
    CIRCUIT_NOTE,
    CONSTITUENT_URLS,
    build_universe,
    master_equities,
)
from app.upstox.client import UpstoxError
from app.upstox.deps import make_client
from app.upstox.instruments import IST, Instrument, current_index, latest_snapshot
from app.upstox.redact import redact

ASM_URL = "https://nsearchives.nseindia.com/content/cm/REG_IND.csv"
CIRCUIT_URL = "https://nsearchives.nseindia.com/content/equities/sec_list.csv"
UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "app" / "swing" / "data" / "swing_universe_v1.json"


def refuse_if_live() -> str | None:
    if settings.live_trading:
        return "LIVE_TRADING is true. Swing Phase 1 does not run."
    return None


def fetch_texts() -> tuple[dict[str, str], dict[str, str], str, str]:
    headers = {"User-Agent": "TradeBossResearch/1.0"}
    texts: dict[str, str] = {}
    errors: dict[str, str] = {}
    asm_note = ASM_NOTE
    circuit_note = CIRCUIT_NOTE
    with httpx.Client(timeout=40, follow_redirects=True, headers=headers) as http:
        for name, url in CONSTITUENT_URLS.items():
            try:
                response = http.get(url)
                response.raise_for_status()
                texts[name] = response.text
            except httpx.HTTPError as exc:
                errors[name] = redact(str(exc))[:160]
        asm_note = _probe(http, ASM_URL, "ASM/GSM", asm_note)
        circuit_note = _probe(http, CIRCUIT_URL, "circuit band", circuit_note)
    return texts, errors, asm_note, circuit_note


def _probe(http: httpx.Client, url: str, label: str, fallback: str) -> str:
    try:
        response = http.get(url)
    except httpx.HTTPError:
        return fallback
    if response.status_code != 200 or "symbol" not in response.text.lower():
        return fallback
    rows = max(0, response.text.count("\n") - 1)
    return (
        f"A current {label} file answered ({rows} rows) but it is not a history. "
        "Past days are not flagged with today's membership."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe-only", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    args = parser.parse_args(argv)
    blocked = refuse_if_live()
    if blocked:
        print(blocked, file=sys.stderr)
        return 2
    today = datetime.now(IST).date()
    index = current_index(settings.instruments_dir)
    if index is None:
        print("No instrument snapshot.", file=sys.stderr)
        return 2
    texts, errors, asm_note, circuit_note = fetch_texts()
    if "nifty500" not in texts:
        print("Nifty 500 constituent list did not download.", file=sys.stderr)
        return 2
    extras = []
    latest = latest_snapshot(settings.instruments_dir)
    if latest is not None and latest[1].is_file():
        extras = master_equities(latest[1])
    universe = build_universe(texts, index, as_of=today, list_errors=errors, extra_equities=extras)
    universe["asm_gsm_note"] = asm_note
    universe["circuit_note"] = circuit_note
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_PATH.write_text(json.dumps(universe, indent=2) + "\n", encoding="utf-8")
    counts = universe["counts"]
    print(
        f"universe {universe['as_of']}: nifty500 {counts['nifty500']} "
        f"resolved {counts['resolved']} not_found {counts['not_found']} "
        f"departed_added {counts['departed_since_2015_added']}",
        flush=True,
    )
    for row in universe["not_found"]:
        print(f"  not found: {row['symbol']} {row['isin']}", flush=True)
    if args.universe_only:
        return 0

    client = None if args.skip_sync else make_client()
    if client is None and not args.skip_sync:
        print("No analytics token.", file=sys.stderr)
        return 2
    root = settings.candles_daily_dir
    root.mkdir(parents=True, exist_ok=True)
    jobs = [("equity", row) for row in universe["stocks"]] + [("index", row) for row in universe["indices"]]
    errors: dict[str, str] = {}
    if not args.skip_sync:
        assert client is not None
        for i, (_role, row) in enumerate(jobs, start=1):
            inst = _instrument(index, row)
            label = row.get("symbol") or row.get("name")
            if inst is None:
                errors[row["instrument_key"]] = "key missing from snapshot"
                continue
            print(f"[{i}/{len(jobs)}] {label}", flush=True)
            try:
                result = sync_daily(client, root, inst, from_date=HISTORY_FROM, now=datetime.now(IST))
            except (UpstoxError, OSError) as exc:
                message = redact(str(exc))[:180]
                print(f"  error {message}", file=sys.stderr, flush=True)
                errors[row["instrument_key"]] = message
                continue
            print(f"  {result['bars']} bars, {result['windows']} windows", flush=True)

    adjustment = _adjust_all(universe, root, today, client if not args.skip_sync else None)
    pass_rows: list[dict] = []
    filter_frames = []
    symbol_rows = []
    holidays = _holidays()
    for role, item in jobs:
        folder = root / symbol_dir_name(item["instrument_key"])
        path = series_path(folder)
        stats = coverage(path, holiday_from=date(2022, 1, 26), holidays=holidays)
        symbol_rows.append({**_brief(item), **stats, "error": errors.get(item["instrument_key"])})
        if role != "equity" or not path.is_file():
            continue
        flags = apply_filters(_read(path))
        if flags.empty:
            continue
        flags = flags.copy()
        flags["symbol"] = item.get("symbol")
        flags["segment"] = item.get("segment")
        filter_frames.append(flags)
        for flag in flags.itertuples(index=False):
            pass_rows.append({"segment": item["segment"], "date": flag.date, "passes": bool(flag.passes)})
    if filter_frames:
        out = root / "_filters" / "daily.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_parquet(pd.concat(filter_frames, ignore_index=True), out)
    years = year_end_passes(pass_rows)
    text = render_report(universe, symbol_rows, years, adjustment=adjustment)
    report_dir = settings.data_dir / "validation"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"swing_phase1_quality_{today.isoformat()}.md"
    report.write_text(text, encoding="utf-8")
    print(text, flush=True)
    print(f"wrote {report}", flush=True)
    return 0


def _instrument(index, row: dict):
    found = index.get(row["instrument_key"])
    if found is not None:
        return found
    key = row.get("instrument_key")
    if not isinstance(key, str) or "|" not in key:
        return None
    symbol = str(row.get("upstox_symbol") or row.get("symbol") or row.get("name") or "")
    role = "index" if row.get("role") == "index" else "equity"
    return Instrument(
        key, symbol, str(row.get("name") or symbol), role,
        "NSE_INDEX" if role == "index" else "NSE_EQ",
        "INDEX" if role == "index" else "EQ",
    )


def _brief(row: dict) -> dict:
    return {
        "symbol": row.get("symbol") or row.get("name"),
        "segment": row.get("segment"),
        "instrument_key": row.get("instrument_key"),
    }


def _holidays() -> set[date]:
    from app.backtest.expiry import DEFAULT_HOLIDAYS_FILE

    if not DEFAULT_HOLIDAYS_FILE.is_file():
        return set()
    raw = json.loads(DEFAULT_HOLIDAYS_FILE.read_text())
    return {date.fromisoformat(value) for value in raw.get("holidays", [])}


def _read(path: Path) -> pd.DataFrame:
    from app.data.history import read_parquet

    return read_parquet(path)


def _adjust_all(universe: dict, root: Path, today: date, client) -> dict:
    verdict, reason = _verify_kotak(universe, root)
    for row in universe["stocks"]:
        folder = root / symbol_dir_name(row["instrument_key"])
        if not (folder / "daily.parquet").is_file():
            continue
        actions = []
        use = verdict
        if verdict == "unadjusted" and client is not None:
            isin = str(row.get("isin") or "")
            try:
                actions = fetch_corporate_actions(client, isin) if isin else []
            except UpstoxError as exc:
                print(f"  corporate actions {row.get('symbol')}: {redact(str(exc))[:120]}", file=sys.stderr)
                continue
        elif verdict != "already_adjusted":
            continue
        try:
            write_daily_adjusted(folder, as_of=today, verdict=use, reason=reason, actions=actions)
        except (OSError, FileNotFoundError) as exc:
            print(f"  adjust {row.get('symbol')}: {redact(str(exc))[:120]}", file=sys.stderr, flush=True)
    for row in universe["indices"]:
        folder = root / symbol_dir_name(row["instrument_key"])
        if not (folder / "daily.parquet").is_file():
            continue
        write_daily_adjusted(
            folder, as_of=today, verdict="already_adjusted", reason="Index series. No split or bonus is applied.",
        )
    return {"verdict": verdict, "reason": reason}


def _verify_kotak(universe: dict, root: Path) -> tuple[str, str]:
    match = next((row for row in universe["stocks"] if row.get("symbol") == KOTAK_SPLIT["symbol"] or row.get("isin") == KOTAK_SPLIT["isin"]), None)
    if match is None:
        return "unknown", "KOTAKBANK is not in this universe, so the daily series was not checked."
    path = root / symbol_dir_name(match["instrument_key"]) / "daily.parquet"
    pair = closes_around(path, KOTAK_SPLIT["ex_date"])
    if pair is None:
        return "unknown", "KOTAKBANK daily bars do not cover the 2026-01-14 split."
    pre, post = pair
    verdict = adjustment_verdict(pre, post, float(KOTAK_SPLIT["price_factor"]))
    reason = (
        f"KOTAKBANK {KOTAK_SPLIT['ratio']} ex {KOTAK_SPLIT['ex_date'].isoformat()}: "
        f"close {pre:.2f} then {post:.2f} ({verdict}). "
        "Same check as Phase A. Factors are not applied again when the series is already adjusted."
    )
    if verdict == "unadjusted":
        reason += " Split and bonus factors are applied to each equity."
    return verdict, reason


if __name__ == "__main__":
    sys.exit(main())
