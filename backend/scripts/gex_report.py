"""The end-of-day GEX study. Runs by itself at 15:40 IST on a session day (app/options/gex_scheduler.py),
or by hand:

    cd backend && uv run python -m scripts.gex_report [--day 2026-11-09] [--only C2,C11]

For every recorded GEX day (data/gex/*.jsonl) it
  1. reads the regime at 09:45 (spot vs gamma flip) and how far Nifty then moved, and
  2. runs the pre-registered candidates (app/backtest/candidates.py, options mode, after costs) over the
     recorded days and splits their trades by the regime of the last snapshot at or before entry.
Writes data/validation/gex_<day>.md and data/gex/report_latest.json. No network.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.backtest.candidates import CANDIDATES
from app.backtest.holdout import load_holdout
from app.config import settings
from app.options.gex_history import (
    day_stats,
    load_day,
    recorded_days,
    render_report,
    summarise_tagged,
    tag_trades,
)

#: indicators such as ATR(14), NR7 and the 20-day CPR mean need history before the first recorded day
WARMUP_DAYS = 45

RunCandidate = Callable[[str, dict[str, Any], date, date], list[dict[str, Any]]]


def run_candidate(strategy: str, params: dict[str, Any], start: date, end: date) -> list[dict[str, Any]]:
    from app.backtest.catalog import parse_config
    from app.backtest.execute import execute_run

    config = parse_config({
        "strategy": strategy, "params": params, "symbol": "NIFTY50", "timeframe": "5m",
        "start": start.isoformat(), "end": end.isoformat(), "sessions": ["normal"], "mode": "options",
    })
    out = execute_run(config, lambda _event: None, hash_data=False)
    return list(out["result"]["trades"])


def backtest_start(first_day: date) -> date:
    """Warm-up start that never reaches back into the fixed holdout."""
    _, holdout_end = load_holdout()
    return max(first_day - timedelta(days=WARMUP_DAYS), holdout_end + timedelta(days=1))


def build(
    gex_dir: Path,
    *,
    upto: date,
    run: RunCandidate = run_candidate,
    only: set[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    days = [d for d in recorded_days(gex_dir) if d <= upto]
    snaps_by_day = {d: load_day(gex_dir, d) for d in days}
    snaps_by_day = {d: s for d, s in snaps_by_day.items() if s}
    stats = [s for s in (day_stats(snaps_by_day[d]) for d in sorted(snaps_by_day)) if s is not None]
    candidates: list[dict[str, Any]] = []
    if snaps_by_day:
        first, last = min(snaps_by_day), max(snaps_by_day)
        start = backtest_start(first)
        for cid, strategy, params in CANDIDATES:
            if only and cid not in only:
                continue
            name = strategy + " " + ", ".join(f"{k}={v}" for k, v in params.items())
            row: dict[str, Any] = {"id": cid, "name": name}
            try:
                trades = run(strategy, params, start, last)
                tagged = tag_trades(trades, snaps_by_day)
                row["by_regime"] = summarise_tagged(tagged)
                row["tagged_trades"] = len(tagged)
            except Exception as e:  # one failing candidate must not stop the report
                row["error"] = f"{type(e).__name__}: {e}"[:200]
            candidates.append(row)
    text = render_report(stats, candidates, written_for=upto)
    payload = {"day": upto.isoformat(), "days": stats, "candidates": candidates}
    return text, payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="End-of-day GEX study")
    parser.add_argument("--day", default=date.today().isoformat(), help="last day to include (default today)")
    parser.add_argument("--only", default="", help="comma-separated candidate ids, e.g. C2,C11")
    args = parser.parse_args(argv)
    upto = date.fromisoformat(args.day)
    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    gex_dir = settings.data_dir / "gex"
    text, payload = build(gex_dir, upto=upto, only=only)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gex_{upto.isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    gex_dir.mkdir(parents=True, exist_ok=True)
    (gex_dir / "report_latest.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(text)
    print(f"written {path}")


if __name__ == "__main__":
    main()
