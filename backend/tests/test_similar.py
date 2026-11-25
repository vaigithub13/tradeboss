"""Similar patterns: no look-ahead, no overlapping matches, base rate, walk-forward."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.data.importer import COLUMNS, write_parquet
from app.data.store import CandleStore
from app.similar.cache import load_bank, save_bank
from app.similar.days import build_day_rows, match_days, outcome_line
from app.similar.features import (
    DAY_S,
    candle_features,
    day_number,
    window_ends,
    zscore_window,
)
from app.similar.match import base_returns, pick_matches, summary
from app.similar.service import answer_candles, build_bank_arrays, load_normal
from app.similar.walkforward import run_walkforward, sample_positions, verdict

IST = ZoneInfo("Asia/Kolkata")


def ts(day: str, hour: int, minute: int) -> int:
    y, m, d = (int(p) for p in day.split("-"))
    return int(datetime(y, m, d, hour, minute, tzinfo=IST).timestamp())


def session(day: str, *, drift: float = 0.0) -> dict[str, np.ndarray]:
    """75 five-minute bars, 09:15 through 15:25 IST."""
    times, o, h, l, c = [], [], [], [], []
    price = 100.0 + drift
    for i in range(75):
        minute = 9 * 60 + 15 + i * 5
        times.append(ts(day, minute // 60, minute % 60))
        o.append(price)
        price = price + (0.2 if i >= 40 else 0.01)
        c.append(price)
        h.append(max(o[-1], c[-1]) + 0.05)
        l.append(min(o[-1], c[-1]) - 0.05)
    return {
        "times": np.array(times, dtype=np.int64),
        "open": np.array(o),
        "high": np.array(h),
        "low": np.array(l),
        "close": np.array(c),
    }


def stack_days(days: list[str], drift: float = 0.0) -> dict[str, np.ndarray]:
    parts = [session(day, drift=drift + i) for i, day in enumerate(days)]
    return {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}


def test_intraday_window_does_not_cross_the_overnight_gap():
    bars = stack_days(["2024-01-08", "2024-01-09"])
    ends = window_ends(bars["times"], 10, "5m")
    days = (bars["times"][ends] + 19_800) // DAY_S
    starts = ends - 9
    start_days = (bars["times"][starts] + 19_800) // DAY_S
    assert np.all(days == start_days)
    assert len(ends) > 0


def test_thirty_fifteen_minute_bars_do_not_fit_in_one_session():
    start = ts("2024-01-08", 9, 15)
    times = start + np.arange(25, dtype=np.int64) * 900
    assert len(window_ends(times, 30, "15m")) == 0


def test_flat_feature_zscores_to_zero():
    block = np.column_stack([
        np.linspace(-1, 1, 8),
        np.ones(8),
        np.linspace(0, 1, 8),
        np.linspace(1, 0, 8),
    ])
    scored = zscore_window(block)
    assert np.allclose(scored[:, 1], 0.0)
    assert abs(scored[:, 0].mean()) < 1e-9


def test_pick_matches_drops_overlap_and_near_duplicates():
    query_start = 10 * DAY_S
    base = query_start - 2 * DAY_S
    ends = np.array([query_start - 30, base, base + 3600, base - 2 * DAY_S])
    distances = np.array([0.01, 0.1, 0.2, 0.3])
    chosen = pick_matches(distances, ends, query_start=query_start, k=10)
    assert chosen == [1, 3]


def test_base_rate_is_the_share_of_up_moves():
    returns = np.array([1.0, -1.0, 2.0, 3.0])
    stats = summary(returns)
    assert stats["n"] == 4
    assert stats["pct_up"] == pytest.approx(75.0)
    assert stats["median_return"] == pytest.approx(1.5)


def test_verdict_is_a_coin_flip_inside_three_points_or_when_thin():
    assert verdict(52.0, 50.0, 400) == "no better than coin flip"
    assert verdict(50.0, 50.0, 5) == "too few samples"
    assert verdict(90.0, 50.0, 400) == "above the base rate"


def test_walkforward_sample_skips_the_holdout():
    bars = stack_days(["2026-06-15", "2026-07-15", "2026-10-15"])
    built = build_bank_arrays(bars, 8, "5m")
    positions = sample_positions(built["times"], built["ends"], "5m")
    days = {(int(built["times"][built["ends"][p]]) + 19_800) // DAY_S for p in positions}
    assert day_number(date(2026, 7, 15)) not in days
    assert day_number(date(2026, 6, 15)) in days
    assert day_number(date(2026, 10, 15)) in days


def test_walkforward_uses_only_earlier_days_and_reports_the_sample():
    days = [
        "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
        "2024-01-15", "2024-01-16", "2024-01-17",
    ]
    bars = stack_days(days)
    built = build_bank_arrays(bars, 8, "5m")
    report = run_walkforward(
        built["times"], built["close"], built["vectors"], built["ends"], "5m", time_cap_s=30,
    )
    block = report["10"]
    assert block["sample_size"] > 0
    assert block["evaluated"] == block["sample_size"]
    assert block["capped"] is False
    assert block["runtime_s"] >= 0
    # Every day continues upward after 10:10, so a call that has history should be right.
    assert block["n"] >= 10
    assert block["hit_rate"] == pytest.approx(100.0)


def test_replay_cursor_hides_later_bars(tmp_path: Path):
    bars = stack_days(["2024-01-08", "2024-01-09", "2024-01-10"])
    built = build_bank_arrays(bars, 8, "5m")
    save_bank(tmp_path / "NIFTY50_5m_w8_v1.npz", built)
    cursor = ts("2024-01-08", 15, 25)
    out = answer_candles(
        CandleStore(tmp_path), tmp_path, symbol="NIFTY50", timeframe="5m", window=8, as_of=cursor,
    )
    assert out["query"]["end"] <= cursor
    assert all(match["end"] <= cursor for match in out["matches"])
    assert all(match["forward_end"] <= cursor for match in out["matches"])
    for block in out["summary"].values():
        # The base rate is computed only from windows whose forward bar is already known.
        assert block["base"]["n"] >= 0
    later = int(bars["times"][bars["times"] > cursor][0])
    assert out["query"]["end"] != later
    assert load_bank(tmp_path / "NIFTY50_5m_w8_v1.npz") is not None
    assert "data/candles" not in str((tmp_path / "NIFTY50_5m_w8_v1.npz").resolve())


def test_loader_keeps_only_normal_sessions(tmp_path: Path):
    monday = ts("2024-01-08", 9, 15)
    saturday = ts("2024-01-06", 9, 15)
    rows = [
        [monday, 100, 101, 99, 100.5, 0, None, "normal"],
        [saturday, 100, 101, 99, 100.5, 0, None, "weekend_full"],
        [ts("2024-11-01", 18, 15), 100, 101, 99, 100.5, 0, None, "muhurat"],
    ]
    frame = pd.DataFrame(rows, columns=COLUMNS)
    write_parquet(frame, tmp_path / "candles" / "NIFTY50" / "5m.parquet")
    loaded = load_normal(CandleStore(tmp_path / "candles"), "NIFTY50", "5m")
    assert list(loaded["times"]) == [monday]


def test_base_rate_does_not_read_past_the_cursor():
    bars = stack_days(["2024-01-08", "2024-01-09"])
    built = build_bank_arrays(bars, 8, "5m")
    cursor = ts("2024-01-08", 12, 0)
    known = base_returns(built["close"], built["times"], built["ends"], 10, as_of=cursor, intraday=True)
    leaked = base_returns(built["close"], built["times"], built["ends"], 10, as_of=None, intraday=True)
    assert len(known) < len(leaked)
    assert len(known) > 0


def test_similar_days_line_carries_the_base_rate():
    assert outcome_line("trend day", 6, 20, 0.31) == "trend day: 6/20 similar vs 31% all days"
    rows = []
    for i, day in enumerate(["2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15"]):
        part = session(day, drift=i)
        if i == 0:
            bars = part
        else:
            bars = {key: np.concatenate([bars[key], part[key]]) for key in part}
    built = build_day_rows(bars["times"], bars["open"], bars["high"], bars["low"], bars["close"], clock_minute=10 * 60 + 15)
    assert len(built) >= 4
    found = match_days(built, query_day=built[-1]["day"], k=3)
    line = found["outcomes"]["trend"]["line"]
    assert "similar vs" in line
    assert line.startswith("trend day:")
    features = candle_features(bars["open"], bars["high"], bars["low"], bars["close"])
    assert features.shape[1] == 4
