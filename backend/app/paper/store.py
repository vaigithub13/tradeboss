"""One JSON file per day under data/paper/, written atomically, and the weekly roll-up over those files."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TOTAL_KEYS = ("trades", "wins", "gross", "charges", "net", "modelled_legs", "signals", "unfilled")
EXIT_KEYS = ("target", "stop", "reversal", "square-off", "other")


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
        if saved is None or saved.get("source", "live") != "live":
            continue  # replays never count (they are written to replay/, which is not read here)
        s = saved.get("summary", {})
        exits = s.get("exits") or {}
        days.append({"date": d.isoformat(), **{k: s.get(k, 0) for k in TOTAL_KEYS},
                     "exits": {k: int(exits.get(k, 0)) for k in EXIT_KEYS}, "dry_run": marks.get(d.isoformat())})
    counted = [d for d in days if d["dry_run"] is None]
    totals = {k: round(sum(d[k] for d in counted), 2) for k in TOTAL_KEYS}
    exit_totals = {k: sum(d["exits"][k] for d in counted) for k in EXIT_KEYS}
    year, week, _ = any_day.isocalendar()
    return {"week": f"{year}-W{week:02d}", "days": days, "totals": totals, "exits": exit_totals,
            "excluded": [d["date"] for d in days if d["dry_run"] is not None]}


def mark_replay(directory: Path, day: date) -> Path:
    """Move a day file that a replay wrote into the live folder (a Start after the close, before replays were kept
    apart) to `replay/`, marked as a replay: the file, its signals and its trades. Returns the new path."""
    src = day_path(directory, day)
    if not src.exists():
        raise FileNotFoundError(f"no day file {src}")
    dst = day_path(directory / "replay", day)
    if dst.exists():
        raise FileExistsError(f"{dst} exists already")
    body = json.loads(src.read_text(encoding="utf-8"))
    body["source"] = "replay"
    if body.get("state") == "running":
        body["state"], body["ended_by"] = "stopped", "replay finished"
    for key in ("signals", "trades", "report"):
        body[key] = [{**item, "source": "replay"} for item in body.get(key, [])]
    if isinstance(body.get("open"), dict):
        body["open"] = {**body["open"], "source": "replay"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, dst)
    src.unlink()
    return dst
