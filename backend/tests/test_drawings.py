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


def _position(drawing_id: str, tool: str = "long_position") -> dict:
    row = _line(drawing_id)
    row["tool"] = tool
    row["anchors"] = [
        {"time": 1_790_826_300, "price": 24000.0},
        {"time": 1_790_839_800, "price": 24480.0},
        {"time": 1_790_839_800, "price": 23760.0},
    ]
    row["position"] = {
        "accountSize": 250000,
        "riskMode": "rupees",
        "riskPercent": 1,
        "riskRupees": 5000,
        "lotSize": 65,
        "priceMode": "points",
        "profitColor": "#089981",
        "stopColor": "#f23645",
        "compact": True,
        "options": True,
    }
    row["drawnOn"] = "15m"
    row["showOn"] = ["15m", "1h"]
    return row


def test_a_position_keeps_its_three_anchors_and_settings_across_a_restart(tmp_path) -> None:
    path = tmp_path / "drawings.sqlite"
    DrawingStore(path).replace("NIFTY50", [_position("long"), _position("short", "short_position"), _line("trend")])
    again = DrawingStore(path).load("NIFTY50")
    assert [(row["id"], row["tool"]) for row in again] == [("long", "long_position"), ("short", "short_position"), ("trend", "trend")]
    assert again[0]["anchors"] == [
        {"time": 1_790_826_300, "price": 24000.0},
        {"time": 1_790_839_800, "price": 24480.0},
        {"time": 1_790_839_800, "price": 23760.0},
    ]
    assert again[0]["position"]["accountSize"] == 250000
    assert again[0]["position"]["riskMode"] == "rupees"
    assert again[0]["position"]["lotSize"] == 65
    assert again[0]["position"]["priceMode"] == "points"
    assert again[0]["position"]["compact"] is True
    assert again[0]["position"]["options"] is True
    assert again[0]["drawnOn"] == "15m"
    assert again[0]["showOn"] == ["15m", "1h"]
    assert "position" not in again[2]


def test_a_position_without_settings_stores_the_defaults(tmp_path) -> None:
    path = tmp_path / "drawings.sqlite"
    row = _position("long")
    del row["position"]
    DrawingStore(path).replace("NIFTY50", [row])
    stored = DrawingStore(path).load("NIFTY50")[0]["position"]
    assert stored == {
        "accountSize": 1000000,
        "riskMode": "percent",
        "riskPercent": 1,
        "riskRupees": 10000,
        "lotSize": None,
        "priceMode": "price",
        "profitColor": "#089981",
        "stopColor": "#f23645",
        "compact": False,
        "options": False,
    }


def test_a_position_with_the_wrong_shape_is_rejected(tmp_path) -> None:
    store = DrawingStore(tmp_path / "drawings.sqlite")
    short = _position("long")
    short["anchors"] = short["anchors"][:2]
    with pytest.raises(ValueError, match="3 anchors"):
        store.replace("NIFTY50", [short])
    bad_mode = _position("long")
    bad_mode["position"]["riskMode"] = "lots"
    with pytest.raises(ValueError, match="riskMode"):
        store.replace("NIFTY50", [bad_mode])
    fraction = _position("long")
    fraction["position"]["lotSize"] = 65.5
    with pytest.raises(ValueError, match="lotSize"):
        store.replace("NIFTY50", [fraction])


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
