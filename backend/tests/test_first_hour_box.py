"""Python First Hour Box must match shared JSON fixtures (TS parity)."""

from __future__ import annotations

import json
from pathlib import Path

from app.strategies.first_hour_box import compute_first_hour_box

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "first_hour_box"


def _load():
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        yield json.loads(path.read_text())


def test_fixtures_match_expected():
    for fx in _load():
        result = compute_first_hour_box(
            fx["candles"],
            as_of=fx.get("asOf"),
            max_width=fx.get("maxWidth", 150),
        )
        expected = fx["expected"]["boxes"]
        assert len(result.boxes) == len(expected), fx["name"]
        for got, exp in zip(result.boxes, expected, strict=True):
            assert got.day == exp["day"], fx["name"]
            assert got.high == exp["high"], fx["name"]
            assert got.low == exp["low"], fx["name"]
            assert got.mid == exp["mid"], fx["name"]
            assert got.width == exp["width"], fx["name"]
            assert got.tooWide == exp["tooWide"], fx["name"]
            assert got.forming == exp["forming"], fx["name"]
            assert [m.kind for m in got.markers] == exp["markerKinds"], fx["name"]
        times = [m.time for m in result.markers]
        assert times == sorted(times)
        assert len(times) == len(set(times))
