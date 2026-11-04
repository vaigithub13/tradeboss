"""Add shadow exits to paper day files written before paper recorded them. No backend, no network.

A trade under a premium rule gets the resting order's exit (app/paper/shadow.py) for the first minute it held from
start to end whose I1 bar crossed its stop or target while the minute's traded prices did not. The minutes are read
from `data/feed-recordings/<day>.minutes.jsonl` (the "close" phase: the tick-built bar and the I1 bar as paper's
feed had them). A trade that already has a shadow is left as it is. Each file's report rows and summary get the
shadow fields. Stop the backend first (one Ctrl+C): a running backend rewrites its slots' day files.

    uv run python -m scripts.paper_shadow_backfill --from 2026-10-08

Then `scripts.paper_recheck_eod` puts a matching exit-time difference in the check's `exchange_only` list.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.costs import load_default_cost_table
from app.paper.desk import PaperDesk
from app.paper.session import ENTRY_GRACE_MS
from app.paper.shadow import priced, report_shadow, shadow_exit, shadow_net
from app.paper.trades import _rule_of, trade_report_row

ROOT = Path(__file__).resolve().parents[2]

#: (option key, minute start in seconds) -> {"i1": {open, high, low, close} | None, "tick_low", "tick_high"}
MinuteBars = dict[tuple[str, int], dict[str, Any]]


def minute_bars(path: Path) -> MinuteBars:
    """The option minutes of one day's minute log, as paper's feed had them at each minute's close."""
    out: MinuteBars = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("phase") != "close" or not str(rec.get("key", "")).startswith("NSE_FO|"):
            continue
        tick = rec.get("tick") or {}
        out[(rec["key"], int(rec["minute"]) * 60)] = {"i1": rec.get("i1"), "tick_low": tick.get("low"),
                                                      "tick_high": tick.get("high")}
    return out


def _shadow_of(t: dict[str, Any], bars: MinuteBars, cost_table: Any, day: date) -> dict[str, Any] | None:
    entry_ms, exit_ms = int(t["entry_at_ms"]), int(t["exit_at_ms"])
    minute = entry_ms // 60_000 * 60
    while (minute + 60) * 1000 <= exit_ms:
        if entry_ms <= minute * 1000 + ENTRY_GRACE_MS:
            b = bars.get((t["key"], minute))
            if b is not None and b["i1"]:
                sh = shadow_exit(t.get("levels"), b["i1"], tick_low=b["tick_low"], tick_high=b["tick_high"])
                if sh is not None:
                    return priced(sh, minute=minute, entry=float(t["entry_price"]), units=int(t["units"]),
                                  charges_entry=float(t["charges_entry"]), cost_table=cost_table, day=day)
        minute += 60
    return None


def fill_shadows(saved: dict[str, Any], bars: MinuteBars, cost_table: Any, day: date) -> int:
    """Give the file's trades their shadows (in place), then its report rows and summary. Returns how many added."""
    rule = _rule_of(saved)
    added = 0
    trades = saved.get("trades", [])
    for t in trades:
        if rule is None or rule.kind != "premium" or t.get("shadow") or not t.get("levels"):
            continue
        sh = _shadow_of(t, bars, cost_table, day)
        if sh is not None:
            t["shadow"] = sh
            added += 1
    report = saved.get("report") or []
    if len(report) == len(trades):
        for row, t in zip(report, trades):
            row["shadow"], row["shadow_net"] = report_shadow(t), shadow_net(t)
    else:
        saved["report"] = [trade_report_row(t, rule) for t in trades]
    counted = [t for t in trades if t.get("source", "live") == "live"]
    net = round(sum(float(t["net"]) for t in counted), 2)
    shadow = round(sum(shadow_net(t) for t in counted), 2)
    summary = saved.setdefault("summary", {})
    summary["shadow_net"], summary["shadow_diff"] = shadow, round(shadow - net, 2)
    return added


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="since", required=True, help="first day, YYYY-MM-DD")
    p.add_argument("--slot", action="append", help="slot number; repeat for more (default: 1-4)")
    p.add_argument("--paper-dir", type=Path, default=ROOT / "data" / "paper")
    p.add_argument("--recordings-dir", type=Path, default=ROOT / "data" / "feed-recordings")
    args = p.parse_args()
    since = date.fromisoformat(args.since)
    cost_table = load_default_cost_table()
    for slot in args.slot or ["1", "2", "3", "4"]:
        for path in sorted(PaperDesk.slot_dir(args.paper_dir, slot).glob("????-??-??.json")):
            day = date.fromisoformat(path.stem)
            if day < since:
                continue
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("source", "live") != "live":
                continue
            log = args.recordings_dir / f"{day.isoformat()}.minutes.jsonl"
            added = fill_shadows(saved, minute_bars(log), cost_table, day)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(saved, indent=2, sort_keys=True), encoding="utf-8")  # as app.paper.store
            os.replace(tmp, path)
            s = saved["summary"]
            print(f"slot {slot} {day}: {added} shadow(s){'' if log.is_file() else ' (no minute log)'}; "
                  f"net {s.get('net')}, shadow net {s['shadow_net']}, difference {s['shadow_diff']}")


if __name__ == "__main__":
    main()
