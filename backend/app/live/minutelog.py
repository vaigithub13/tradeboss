"""Per-minute log: tick-built bar | I1 bar | official bar, side by side.

`data/feed-recordings/YYYY-MM-DD.minutes.jsonl` - one JSON object per line:

* `{"phase": "close", ...}`       every instrument-minute at the end of the session (no official yet)
* `{"phase": "reconciled", ...}`  the same after the reconcile (official bar + differences)
* `{"phase": "summary", ...}`     counts that answer the open questions: is I1 the forming or the
                                  last completed bar, which 09:15 volume baseline matches official,
                                  latency, dropped ticks
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def minutes_path(directory: Path, day_iso: str) -> Path:
    return directory / f"{day_iso}.minutes.jsonl"


def reconcile_path(directory: Path, day_iso: str) -> Path:
    return directory / f"{day_iso}.reconcile.jsonl"


def write_minute_log(
    path: Path, *, close: list[dict], reconciled: list[dict] | None, summary: dict
) -> None:
    """Rewrite the whole file atomically (so a replay or a second call never duplicates lines)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for phase, rows in (("close", close), ("reconciled", reconciled or [])):
            for r in rows:
                fh.write(json.dumps({"phase": phase, **r}, separators=(",", ":"), default=str) + "\n")
        fh.write(json.dumps({"phase": "summary", **summary}, separators=(",", ":"), default=str) + "\n")
    os.replace(tmp, path)


def read_phase(path: Path, phase: str) -> list[dict]:
    """The rows of one phase, without the `phase` field. A restart loses the close rows held in memory;
    the reconcile reads them back from here so its rewrite keeps them."""
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.pop("phase", None) == phase:
                rows.append(row)
    return rows


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":"), default=str) + "\n")
