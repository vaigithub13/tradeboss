"""Drawing store spec. Written before the API. See PROJECT_PLAN.md.

Drawings are per symbol in SQLite. Anchors stay time + price across a restart.
Import replaces one symbol and leaves the others alone.
"""

from __future__ import annotations

import pytest

from app.drawings.store import DrawingStore


def _line(drawing_id: str, when: int = 1_790_839_020) -> dict:
    return {
        "id": drawing_id,
        "tool": "trend",
        "anchors": [
            {"time": when, "price": 22416.0},
            {"time": when + 60, "price": 22420.0},
        ],
        "knownAt": when,
        "text": "",
        "style": {
            "color": "#2962ff",
            "width": 1,
            "lineStyle": "solid",
            "extendLeft": False,
            "extendRight": False,
            "fill": None,
        },
    }


def test_a_symbol_keeps_its_anchors_across_a_restart_and_another_symbol_is_separate(tmp_path) -> None:
    path = tmp_path / "drawings.sqlite"
    store = DrawingStore(path)
    store.replace("NIFTY50", [_line("a")])
    store.replace("RELIANCE", [_line("b", 50)])

    again = DrawingStore(path)
    nifty = again.load("NIFTY50")
    assert nifty[0]["anchors"][0] == {"time": 1_790_839_020, "price": 22416.0}
    assert [row["id"] for row in again.load("RELIANCE")] == ["b"]
    assert again.load("BANKNIFTY") == []


def test_export_and_import_round_trip_one_symbol(tmp_path) -> None:
    path = tmp_path / "drawings.sqlite"
    store = DrawingStore(path)
    store.replace("NIFTY50", [_line("a")])
    store.replace("RELIANCE", [_line("b", 50)])

    payload = store.export_payload("NIFTY50")
    assert payload["symbol"] == "NIFTY50"
    assert payload["drawings"][0]["id"] == "a"

    payload["drawings"] = [_line("c")]
    assert store.import_payload(payload) == "NIFTY50"
    assert [row["id"] for row in store.load("NIFTY50")] == ["c"]
    assert [row["id"] for row in store.load("RELIANCE")] == ["b"]


def test_timeframe_visibility_and_object_flags_survive_a_restart(tmp_path) -> None:
    path = tmp_path / "drawings.sqlite"
    line = _line("fib")
    line["tool"] = "fib"
    line["drawnOn"] = "15m"
    line["showOn"] = ["15m", "1h"]
    line["hidden"] = True
    line["locked"] = True
    DrawingStore(path).replace("NIFTY50", [line])

    again = DrawingStore(path).load("NIFTY50")[0]
    assert again["drawnOn"] == "15m"
    assert again["showOn"] == ["15m", "1h"]
    assert again["hidden"] is True
    assert again["locked"] is True


def test_a_drawing_without_timeframe_fields_shows_on_every_timeframe(tmp_path) -> None:
    path = tmp_path / "drawings.sqlite"
    DrawingStore(path).replace("NIFTY50", [_line("a")])
    row = DrawingStore(path).load("NIFTY50")[0]
    assert row["drawnOn"] == ""
    assert row["showOn"] is None
    assert row["hidden"] is False
    assert row["locked"] is False


def test_bad_drawings_are_rejected(tmp_path) -> None:
    store = DrawingStore(tmp_path / "drawings.sqlite")
    bad = _line("a")
    bad["tool"] = "order"
    with pytest.raises(ValueError):
        store.replace("NIFTY50", [bad])
    missing = _line("a")
    del missing["anchors"][0]["price"]
    with pytest.raises(ValueError):
        store.replace("NIFTY50", [missing])
    with pytest.raises(ValueError):
        store.import_payload({"drawings": []})
    unknown = _line("a")
    unknown["showOn"] = ["2h"]
    with pytest.raises(ValueError):
        store.replace("NIFTY50", [unknown])
