"""Learn notes stay in data/learn/notes and round-trip without touching the live feed."""

from __future__ import annotations

from app.learn_notes import load_notes, notes_path, save_notes


def test_notes_round_trip(tmp_path):
    save_notes(tmp_path, {
        "notes": [{"id": "1", "time": 1, "price": 24180.5, "text": "opening balance", "symbol": "NIFTY50"}],
        "reads": {"2026-11-27": "Range day. Stay patient."},
    })
    path = notes_path(tmp_path)
    assert path == tmp_path / "learn" / "notes" / "notes.json"
    assert path.is_file()
    got = load_notes(tmp_path)
    assert got["notes"][0]["text"] == "opening balance"
    assert got["reads"]["2026-11-27"] == "Range day. Stay patient."


def test_missing_file_is_empty(tmp_path):
    assert load_notes(tmp_path) == {"notes": [], "reads": {}}
