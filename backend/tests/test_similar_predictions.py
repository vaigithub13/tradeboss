"""Prediction log: append-only, no look-ahead, hypothetical points."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np

from app.similar.features import window_ends
from app.similar.predictions import (
    append_prediction,
    build_candle_record,
    direction_of,
    log_path,
    review_verdict,
    score_record,
    snapshot_cursor,
    tally,
    write_review_if_due,
)
from scripts import session_stop

IST = ZoneInfo("Asia/Kolkata")


def _session(day: str, *, drift: float) -> dict[str, np.ndarray]:
    y, m, d = (int(part) for part in day.split("-"))
    start = int(datetime(y, m, d, 9, 15, tzinfo=IST).timestamp())
    n = 25
    times = start + np.arange(n, dtype=np.int64) * 900
    close = 100.0 + drift + np.arange(n) * 0.4
    return {
        "times": times,
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.3,
        "close": close,
    }


def _days(count: int) -> dict[str, np.ndarray]:
    parts = [_session(f"2024-01-{8 + i:02d}", drift=float(i)) for i in range(count)]
    return {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}


def _clip(bars: dict[str, np.ndarray], as_of: int) -> dict[str, np.ndarray]:
    keep = int(np.searchsorted(bars["times"], as_of, side="right"))
    return {key: value[:keep] for key, value in bars.items()}


def test_direction_is_none_inside_the_band():
    assert direction_of(45.0) == "none"
    assert direction_of(55.0) == "none"
    assert direction_of(55.1) == "up"
    assert direction_of(44.9) == "down"
    assert direction_of(None) == "none"


def test_thirty_bar_snapshot_may_span_the_previous_session():
    start = int(datetime(2024, 1, 8, 9, 15, tzinfo=IST).timestamp())
    times = np.concatenate([
        start + np.arange(25, dtype=np.int64) * 900,
        start + 86_400 + np.arange(25, dtype=np.int64) * 900,
    ])
    assert len(window_ends(times, 30, "15m")) == 0
    assert len(window_ends(times, 30, "15m", span_sessions=True)) > 0
    bars = _clip(_days(4), snapshot_cursor(date(2024, 1, 11), "11:15"))
    record = build_candle_record(bars, symbol="NIFTY50", day="2024-01-11", clock="11:15", as_of=snapshot_cursor(date(2024, 1, 11), "11:15"))
    assert record is not None
    assert record["spans_gap"] is True
    assert record["horizon_label"] == "1h"
    assert record["query"]["end"] <= snapshot_cursor(date(2024, 1, 11), "11:15")
    for match in record["matches"]:
        assert match["end"] <= record["query"]["start"] - 86_400


def test_append_never_rewrites_a_saved_prediction(tmp_path):
    record = {"id": "2024-01-08|NIFTY50|candles|09:30", "day": "2024-01-08", "direction": "up"}
    assert append_prediction(tmp_path, record) is True
    original = log_path(tmp_path, "2024-01-08").read_text()
    changed = dict(record, direction="down")
    assert append_prediction(tmp_path, changed) is False
    assert log_path(tmp_path, "2024-01-08").read_text() == original
    assert "down" not in original


def test_score_waits_for_the_horizon_and_counts_points_in_the_called_direction():
    start = int(datetime(2024, 1, 8, 9, 15, tzinfo=IST).timestamp())
    times = start + np.arange(6, dtype=np.int64) * 900
    close = np.array([100, 101, 99, 102, 110, 111], dtype=np.float64)
    bars = {"times": times, "high": close + 2, "low": close - 4, "close": close}
    record = {
        "id": "2024-01-08|NIFTY50|candles|09:30",
        "day": "2024-01-08",
        "symbol": "NIFTY50",
        "kind": "candles",
        "slot": "09:30",
        "direction": "up",
        "base_rate": 51.0,
        "query": {"start": int(times[0]), "end": int(times[0])},
    }
    assert score_record(record, {key: value[:3] for key, value in bars.items()}) is None
    assert score_record(record, bars, closed_through=int(times[4])) is None
    scored = score_record(record, bars)
    assert scored is not None
    assert scored["hit"] is True
    assert scored["points"] == 10.0
    assert scored["adverse_1h"] == 5.0
    record["direction"] = "down"
    scored = score_record(record, bars)
    assert scored["hit"] is False
    assert scored["points"] == -10.0


def test_review_is_written_once_and_leaves_the_log_alone(tmp_path):
    record = {
        "id": "2024-01-08|NIFTY50|candles|09:30",
        "day": "2024-01-08",
        "symbol": "NIFTY50",
        "kind": "candles",
        "slot": "09:30",
        "direction": "none",
        "base_rate": 50.0,
        "query": {"end": int(datetime(2024, 1, 8, 9, 15, tzinfo=IST).timestamp())},
        "up_count": 5,
        "match_n": 10,
    }
    append_prediction(tmp_path, record)
    before = log_path(tmp_path, "2024-01-08").read_bytes()
    morning = datetime(2024, 1, 8, 10, 0, tzinfo=IST)
    assert write_review_if_due(tmp_path, store=None, now=morning) is False  # type: ignore[arg-type]
    series = _session("2024-01-08", drift=0.0)

    class Store:
        pass

    def fake_bars(store, symbol, day):
        return {"times": series["times"], "high": series["high"], "low": series["low"], "close": series["close"]}

    import app.similar.predictions as predictions

    original = predictions._day_bars
    predictions._day_bars = fake_bars
    try:
        evening = datetime(2024, 1, 8, 16, 0, tzinfo=IST)
        assert write_review_if_due(tmp_path, Store(), now=evening) is True
        saved = (tmp_path / "predictions" / "reviews" / "2024-01-08.json").read_bytes()
        assert write_review_if_due(tmp_path, Store(), now=evening) is False
        assert (tmp_path / "predictions" / "reviews" / "2024-01-08.json").read_bytes() == saved
    finally:
        predictions._day_bars = original
    assert log_path(tmp_path, "2024-01-08").read_bytes() == before


def test_verdict_waits_for_fifty_calls_and_splits_none():
    assert review_verdict(80.0, 50.0, 49) is None
    assert review_verdict(70.0, 50.0, 50) == "better than base rate"
    assert review_verdict(51.0, 50.0, 80) == "no better than coin flip"
    rows = []
    for i in range(40):
        rows.append({"day": "2024-01-08", "slot": "09:30", "direction": "up", "hit": True, "points": 2.0, "raw_points": 2.0, "base_rate": 50.0})
    for i in range(10):
        rows.append({"day": "2024-01-08", "slot": "10:15", "direction": "none", "hit": None, "points": None, "raw_points": 1.0, "base_rate": 50.0})
    totals = tally(rows)
    assert totals["overall"]["excluded"]["predictions"] == 40
    assert totals["overall"]["included"]["predictions"] == 50
    assert totals["overall"]["excluded"]["points"] == 80.0
    assert totals["overall"]["included"]["points"] == 90.0
    assert totals["points_label"].startswith("Hypothetical index points")


def test_session_stop_records_the_review_before_it_looks_for_the_backend(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(session_stop, "_record_prediction_review", lambda: calls.append("review"))
    monkeypatch.setattr(session_stop, "_pgrep", lambda: [])
    assert session_stop.stop(state_dir=tmp_path) == 0
    assert calls == ["review"]
