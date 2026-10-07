"""One JSON file per day under data/paper/, written atomically, and the weekly roll-up over those files."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TOTAL_KEYS = ("trades", "wins", "gross", "charges", "net", "modelled_legs", "signals", "unfilled")


def day_path(directory: Path, day: date) -> Path:
    return directory / f"{day.isoformat()}.json"


def save_day(directory: Path, day: date, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = day_path(directory, day)
    tmp = path.with_suffix(".json.tmp")
    body = {"day": day.isoformat(), **payload}
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_day(directory: Path, day: date) -> dict[str, Any] | None:
    path = day_path(directory, day)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


DRY_RUNS_FILE = "dry_runs.json"


def dry_runs(directory: Path) -> dict[str, str]:
    """Days marked as dry runs ({ISO date: reason}). Kept apart from the day files, which the runner rewrites."""
    path = directory / DRY_RUNS_FILE
    if not path.exists():
        return {}
    return {str(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def mark_dry_run(directory: Path, day: date, reason: str) -> None:
    """Leave `day` out of the forward-test totals (it is still listed, with the reason)."""
    marks = {**dry_runs(directory), day.isoformat(): reason}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / DRY_RUNS_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(marks.items())), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def weekly_summary(directory: Path, any_day: date) -> dict[str, Any]:
    """The ISO week (Mon-Sun) that `any_day` falls in: each saved day, then the totals of the forward-test days.
    A day marked as a dry run is listed with its reason and left out of the totals (`excluded`)."""
    monday = any_day - timedelta(days=any_day.weekday())
    marks = dry_runs(directory)
    days = []
    for offset in range(7):
        d = monday + timedelta(days=offset)
        saved = load_day(directory, d)
        if saved is None:
            continue
        s = saved.get("summary", {})
        days.append({"date": d.isoformat(), **{k: s.get(k, 0) for k in TOTAL_KEYS}, "dry_run": marks.get(d.isoformat())})
    counted = [d for d in days if d["dry_run"] is None]
    totals = {k: round(sum(d[k] for d in counted), 2) for k in TOTAL_KEYS}
    year, week, _ = any_day.isocalendar()
    return {"week": f"{year}-W{week:02d}", "days": days, "totals": totals,
            "excluded": [d["date"] for d in days if d["dry_run"] is not None]}
