"""Learn-tab notes. One JSON file under data/learn/notes/. Nothing here is a live view."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def notes_path(data_dir: Path) -> Path:
    return data_dir / "learn" / "notes" / "notes.json"


def load_notes(data_dir: Path) -> dict[str, Any]:
    path = notes_path(data_dir)
    if not path.is_file():
        return {"notes": [], "reads": {}}
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        return {"notes": [], "reads": {}}
    notes = body.get("notes")
    reads = body.get("reads")
    return {
        "notes": notes if isinstance(notes, list) else [],
        "reads": reads if isinstance(reads, dict) else {},
    }


def save_notes(data_dir: Path, body: dict[str, Any]) -> None:
    path = notes_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"notes": body.get("notes", []), "reads": body.get("reads", {})}, indent=2), encoding="utf-8")
