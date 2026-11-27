"""Learn exports and practice scores stay under data/learn and off the order path."""

from __future__ import annotations

import base64

from app.learn_export import export_dir, save_export
from app.learn_practice import load_practice, save_practice


def test_export_writes_the_note_and_the_picture(tmp_path):
    png = base64.b64encode(b"\x89PNG\r\n").decode("ascii")
    save_export(tmp_path, "2026-11-27", "# Learn session 2026-11-27\n\nWatch only.\n", png)
    folder = export_dir(tmp_path, "2026-11-27")
    assert (folder / "session.md").read_text(encoding="utf-8").startswith("# Learn session")
    assert (folder / "chart.png").read_bytes().startswith(b"\x89PNG")


def test_practice_round_trip(tmp_path):
    save_practice(tmp_path, [{
        "id": "1", "day": "2026-11-27", "symbol": "NIFTY50", "direction": "up",
        "reason": "higher low", "tag": "trend pullback", "level": None,
        "directionHit": True, "levelHit": None,
    }])
    assert load_practice(tmp_path)["reads"][0]["reason"] == "higher low"
