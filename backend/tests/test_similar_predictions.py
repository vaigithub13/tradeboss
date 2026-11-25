"""Prediction log: append-only, no look-ahead, hypothetical points."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from app.similar.features import ist_day, window_ends
from app.similar.predictions import (
    CANDLE_HORIZON,
    CANDLE_WINDOW,
    DAY_CLOCKS,
    INSUFFICIENT,
    LIBRARY_VERSION,
    SNAPSHOT_CLOCKS,
    append_prediction,
    direction_of,
    log_path,
    review_verdict,
    score_record,
    snapshot_cursor,
    tally,
    take_snapshots,
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


def _weekdays(count: int) -> list[date]:
    day = date(2024, 1, 8)
    found: list[date] = []
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day)
        day += timedelta(days=1)
    return found


def _series(days: list[date], step: int, per_day: int) -> dict[str, np.ndarray]:
    parts = []
    for i, day in enumerate(days):
        start = int(datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST).timestamp())
        times = start + np.arange(per_day, dtype=np.int64) * step
        close = 100.0 + i * 3.0 + np.arange(per_day) * (0.2 + i * 0.01)
        parts.append({
            "times": times,
            "open": close - 0.15,
            "high": close + 0.4,
            "low": close - 0.35,
            "close": close,
        })
    return {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}


def _same_session(start: int, end: int) -> bool:
    days = ist_day(np.array([start, end], dtype=np.int64))
    return int(days[0]) == int(days[1]) and end - start <= (CANDLE_WINDOW - 1) * 900


def test_each_clock_uses_the_studied_window(monkeypatch):
    days = _weekdays(8)
    bars_15 = _series(days, 900, 25)
    bars_5 = _series(days, 300, 75)
    empty = {"times": np.empty(0, dtype=np.int64), "open": np.empty(0), "high": np.empty(0), "low": np.empty(0), "close": np.empty(0)}

    def fake_load(store, symbol, timeframe, as_of=None):
        if "VIX" in symbol:
            return empty
        source = bars_15 if timeframe == "15m" else bars_5
        if as_of is None:
            return source
        keep = int(np.searchsorted(source["times"], int(as_of), side="right"))
        return {key: value[:keep] for key, value in source.items()}

    monkeypatch.setattr("app.similar.predictions.load_normal", fake_load)
    last = days[-1]
    assert len(window_ends(bars_15["times"], 30, "15m")) == 0
    for clock in SNAPSHOT_CLOCKS:
        rows = take_snapshots(object(), day=last, clock=clock, symbols=("NIFTY50",))  # type: ignore[arg-type]
        candle = next(row for row in rows if row["kind"] == "candles")
        assert candle["settings"] == {
            "timeframe": "15m",
            "window": CANDLE_WINDOW,
            "horizon_bars": CANDLE_HORIZON,
            "library_version": LIBRARY_VERSION,
            "span_sessions": False,
        }
        assert candle["window"] == 8
        assert candle["spans_gap"] is False
        assert candle["query"]["end"] <= snapshot_cursor(last, clock)
        if clock in ("09:30", "10:15"):
            assert candle["status"] == INSUFFICIENT
            assert candle["direction"] == "none"
            assert candle["matches"] == []
        else:
            assert candle["status"] == "call"
            assert candle["matches"]
            assert _same_session(candle["query"]["start"], candle["query"]["end"])
            for match in candle["matches"]:
                assert _same_session(match["start"], match["end"])
                assert match["end"] <= candle["query"]["start"] - 86_400
        if clock in DAY_CLOCKS:
            day_row = next(row for row in rows if row["kind"] == "days")
            assert day_row["settings"]["library_version"] == LIBRARY_VERSION
            assert day_row["settings"]["span_sessions"] is False
            assert day_row["status"] == "call"


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
    version = totals["versions"][0]
    assert version["overall"]["excluded"]["predictions"] == 40
    assert version["overall"]["included"]["predictions"] == 50
    assert version["overall"]["excluded"]["points"] == 80.0
    assert version["overall"]["included"]["points"] == 90.0
    assert totals["points_label"].startswith("Hypothetical index points")


def test_review_keeps_each_settings_version_separate():
    def row(version: str, hit: bool) -> dict:
        return {
            "day": "2024-01-08",
            "slot": "11:15",
            "direction": "up",
            "hit": hit,
            "points": 1.0,
            "raw_points": 1.0,
            "base_rate": 50.0,
            "status": "call",
            "settings": {
                "timeframe": "15m",
                "window": 8 if version == LIBRARY_VERSION else 30,
                "horizon_bars": 4,
                "library_version": version,
                "span_sessions": version != LIBRARY_VERSION,
            },
        }

    current = row(LIBRARY_VERSION, False)
    rows = [row("legacy-n30", True) for _ in range(50)]
    rows.extend(current for _ in range(50))
    rows.append({
        "day": "2024-01-08",
        "slot": "09:30",
        "direction": "none",
        "status": INSUFFICIENT,
        "hit": None,
        "points": None,
        "raw_points": None,
        "base_rate": None,
        "settings": current["settings"],
    })
    by_version = {item["settings"]["library_version"]: item for item in tally(rows)["versions"]}
    assert by_version["legacy-n30"]["overall"]["excluded"]["hit_rate"] == 100.0
    assert by_version["legacy-n30"]["overall"]["excluded"]["predictions"] == 50
    assert by_version[LIBRARY_VERSION]["overall"]["excluded"]["hit_rate"] == 0.0
    assert by_version[LIBRARY_VERSION]["overall"]["excluded"]["predictions"] == 50
    assert by_version[LIBRARY_VERSION]["insufficient"] == 1
    assert by_version[LIBRARY_VERSION]["overall"]["included"]["predictions"] == 50


def test_session_stop_records_the_review_before_it_looks_for_the_backend(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(session_stop, "_record_prediction_review", lambda: calls.append("review"))
    monkeypatch.setattr(session_stop, "_pgrep", lambda: [])
    assert session_stop.stop(state_dir=tmp_path) == 0
    assert calls == ["review"]
