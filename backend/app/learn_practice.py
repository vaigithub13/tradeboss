"""Learn practice scores. One JSON file under data/learn/practice/. Not a paper journal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def practice_path(data_dir: Path) -> Path:
    return data_dir / "learn" / "practice" / "scores.json"


def load_practice(data_dir: Path) -> dict[str, Any]:
    path = practice_path(data_dir)
    if not path.is_file():
        return {"reads": []}
    body = json.loads(path.read_text(encoding="utf-8"))
    reads = body.get("reads") if isinstance(body, dict) else None
    return {"reads": reads if isinstance(reads, list) else []}


def save_practice(data_dir: Path, reads: list[dict[str, Any]]) -> None:
    path = practice_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reads": reads}, indent=2), encoding="utf-8")
