"""Recomputing a stored end-of-day check replaces only `eod_check` (scripts/paper_recheck_eod.py)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.paper_recheck_eod import live_day_files, outcome, recheck_file

SAVED = {
    "day": "2026-10-12", "source": "live", "state": "stopped", "requested_strategy": "log_xz",
    "signals": [{"time": 1, "side": "SELL", "status": "filled", "fill_price": 88.75}],
    "trades": [{"symbol": "NIFTY 22500 PE 13 OCT 26", "entry_price": 88.75, "exit_price": 123.2, "net": 2174.07}],
    "report": [{"contract": "NIFTY 22500 PE 13 OCT 26", "exit_time": 1791782493}],
    "summary": {"net": 2891.14},
    "eod_check": {"day": "2026-10-12", "live_trades": 1, "backtest_trades": 1, "warnings": [],
                  "differences": [{"time": "09:51", "side": "SELL", "source": "both", "fields": ["exit_time"]}]},
}
NEW = {"day": "2026-10-12", "live_trades": 1, "backtest_trades": 1, "warnings": [], "differences": [],
       "fill_driven": [{"time": "09:51", "side": "SELL", "source": "both", "fields": ["exit_time"],
                        "kind": "fill-driven level difference"}]}


def test_only_the_check_is_replaced_and_the_recompute_is_recorded(tmp_path: Path) -> None:
    path = tmp_path / "2026-10-12.json"
    path.write_text(json.dumps(SAVED, indent=2, sort_keys=True))
    seen = {}

    def fake(day: date, saved: dict) -> dict:
        seen["day"] = day
        return dict(NEW)

    old, new = recheck_file(path, fake, commit="abc1234", on=date(2026, 10, 12))
    after = json.loads(path.read_text())
    assert seen["day"] == date(2026, 10, 12)
    assert {k: v for k, v in after.items() if k != "eod_check"} == {k: v for k, v in SAVED.items() if k != "eod_check"}
    assert after["eod_check"] == {**NEW, "recomputed": {"on": "2026-10-12", "commit": "abc1234"}}
    assert old == SAVED["eod_check"]
    assert outcome(old) != outcome(new)
    assert outcome(new)["differences"] == [] and len(outcome(new)["fill_driven"]) == 1


def test_an_old_check_and_the_same_new_one_have_the_same_outcome() -> None:
    same = {**SAVED["eod_check"], "fill_driven": [], "recomputed": {"on": "2026-10-12", "commit": "x"},
            "differences": [{**SAVED["eod_check"]["differences"][0], "kind": "difference", "reason": "r"}]}
    assert outcome(SAVED["eod_check"]) == outcome(same)


def test_only_live_folders_from_the_first_day_are_listed(tmp_path: Path) -> None:
    for rel in ("2026-10-07.json", "2026-10-08.json", "slot3/2026-10-09.json", "slot3/replay/2026-10-08.json",
                "dry_runs.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("{}")
    got = [(slot, p.relative_to(tmp_path).as_posix()) for slot, p in
           live_day_files(tmp_path, ["1", "3"], date(2026, 10, 8))]
    assert got == [("1", "2026-10-08.json"), ("3", "slot3/2026-10-09.json")]
