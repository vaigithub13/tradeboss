"""Write a Learn session note and chart picture. Not an order, not a paper slot."""

from __future__ import annotations

import base64
import re
from pathlib import Path


def export_dir(data_dir: Path, day: str) -> Path:
    return data_dir / "learn" / "exports" / day


def save_export(data_dir: Path, day: str, markdown: str, png_b64: str) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("day must be YYYY-MM-DD")
    folder = export_dir(data_dir, day)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "session.md").write_text(markdown, encoding="utf-8")
    if png_b64:
        raw = base64.b64decode(png_b64)
        if len(raw) > 4_000_000:
            raise ValueError("chart picture is too large")
        (folder / "chart.png").write_bytes(raw)
    return folder
