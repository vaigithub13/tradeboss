"""Recompute the end-of-day check stored in paper day files, from the files and the stored bars. No backend, no network.

Only `eod_check` is replaced; signals, trades, fills and the rest of the file stay as they are. The new check records
when it was recomputed and by which commit (`eod_check.recomputed`). Live day files only (a replay has no check).
Stop the backend first (one Ctrl+C): a running backend rewrites its slots' day files when it shuts down.

    uv run python -m scripts.paper_recheck_eod --from 2026-10-08
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.paper.desk import PaperDesk

ROOT = Path(__file__).resolve().parents[2]
IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = "NIFTY50"

Check = Callable[[date, dict[str, Any]], dict[str, Any]]


def store_check(candles_dir: Path, options_dir: Path) -> Check:
    """The end-of-day check on a saved day, as the backend runs it after the reconcile."""
    from app.data.store import CandleStore
    from app.options.history import OptionHistoryStore
    from app.paper.check import end_of_day_check

    store, tape = CandleStore(candles_dir), OptionHistoryStore(options_dir)

    def run(day: date, saved: dict[str, Any]) -> dict[str, Any]:
        return end_of_day_check(
            day=day, signals=saved.get("signals", []), live_report=saved.get("report", []),
            live_bars={int(b["time"]): b for b in saved.get("bars", [])},
            incomplete=set(saved.get("incomplete_bars") or []), store=store, symbol=SYMBOL,
            strategy=saved["requested_strategy"], params=dict(saved.get("requested_params") or {}),
            timeframe=saved.get("timeframe", "5m"), basis="fill" if saved.get("uses_stops") else "decision",
            tape=tape,
        )

    return run


def recheck_file(path: Path, check: Check, *, commit: str, on: date) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Replace the file's `eod_check` with a fresh one; everything else is written back unchanged. (old, new)."""
    saved = json.loads(path.read_text(encoding="utf-8"))
    day = date.fromisoformat(saved["day"])
    old = saved.get("eod_check")
    new = {**check(day, saved), "recomputed": {"on": on.isoformat(), "commit": commit}}
    body = {**saved, "eod_check": new}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")  # as app.paper.store.save_day
    os.replace(tmp, path)
    return old, new


def outcome(check: dict[str, Any] | None) -> dict[str, Any]:
    """What a check says, without its bookkeeping: the counts and each difference's time, side, fields and kind."""
    if check is None:
        return {}
    def rows(key: str) -> list[tuple]:
        return [(d["time"], d["side"], d["source"], tuple(d["fields"]), d.get("kind", "difference"))
                for d in check.get(key) or []]
    return {k: check.get(k) for k in ("live_signals", "live_trades", "backtest_signals", "backtest_trades", "error",
                                      "warnings")} | {"differences": rows("differences"),
                                                      "fill_driven": rows("fill_driven")}


def live_day_files(paper_dir: Path, slots: list[str], since: date) -> list[tuple[str, Path]]:
    out = []
    for slot in slots:
        directory = PaperDesk.slot_dir(paper_dir, slot)
        for path in sorted(directory.glob("????-??-??.json")):
            if date.fromisoformat(path.stem) >= since:
                out.append((slot, path))
    return out


def head_commit() -> str:
    """The commit the check code comes from; refused when the backend's code has uncommitted changes."""
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "backend/app", "backend/scripts"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit(f"uncommitted changes in the backend code; commit them first:\n{dirty}")
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="since", required=True, help="first day, YYYY-MM-DD")
    p.add_argument("--slot", action="append", help="slot number; repeat for more (default: 1-4)")
    p.add_argument("--paper-dir", type=Path, default=ROOT / "data" / "paper")
    args = p.parse_args()
    commit = head_commit()
    check = store_check(ROOT / "data" / "candles", ROOT / "data" / "option_history")
    on = datetime.now(IST).date()
    for slot, path in live_day_files(args.paper_dir, args.slot or ["1", "2", "3", "4"], date.fromisoformat(args.since)):
        if json.loads(path.read_text(encoding="utf-8")).get("source", "live") != "live":
            continue
        old, new = recheck_file(path, check, commit=commit, on=on)
        before, after = outcome(old), outcome(new)
        status = "unchanged" if before == after else "CHANGED"
        print(f"slot {slot} {path.stem}: {status}; differences {len(after['differences'])}, "
              f"fill-driven {len(after['fill_driven'])}")
        if status == "CHANGED":
            print(f"  before: {json.dumps(before, default=str)}\n  after:  {json.dumps(after, default=str)}")


if __name__ == "__main__":
    main()
