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


def weekly_summary(directory: Path, any_day: date) -> dict[str, Any]:
    """The ISO week (Mon-Sun) that `any_day` falls in: each saved day, then the totals."""
    monday = any_day - timedelta(days=any_day.weekday())
    days = []
    for offset in range(7):
        d = monday + timedelta(days=offset)
        saved = load_day(directory, d)
        if saved is None:
            continue
        s = saved.get("summary", {})
        days.append({"date": d.isoformat(), **{k: s.get(k, 0) for k in TOTAL_KEYS}})
    totals = {k: round(sum(d[k] for d in days), 2) for k in TOTAL_KEYS}
    year, week, _ = any_day.isocalendar()
    return {"week": f"{year}-W{week:02d}", "days": days, "totals": totals}
