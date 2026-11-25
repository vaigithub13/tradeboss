"""Magnitude checks for similar days. No candle files, no UI."""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from app.market_view.view import score_outcome
from app.similar.days import FEATURE_KEYS
from app.similar.magnitude import (
    _nearest,
    fhb_break,
    is_trend_day,
    rest_of_day_range,
    spearman,
    versus_baseline,
    walk_magnitude,
)

IST = ZoneInfo("Asia/Kolkata")


def test_rest_range_starts_after_the_clock():
    minute = np.array([565, 570, 575])
    high = np.array([20.0, 50.0, 12.0])
    low = np.array([10.0, 0.0, 10.0])
    assert rest_of_day_range(high, low, minute, 570) == 2.0


def test_trend_day_matches_the_market_view_outcome():
    open_ = np.array([100.0])
    high = np.array([110.0])
    low = np.array([90.0])
    close = np.array([108.0])
    candles = [{"open": 100.0, "high": 110.0, "low": 90.0, "close": 108.0}]
    assert is_trend_day(open_, high, low, close) is False
    assert score_outcome({"score": 0, "day_type": "mixed"}, candles)["actual_type"] == "range"
    close = np.array([112.0])
    candles[0]["close"] = 112.0
    assert is_trend_day(open_, high, low, close) is True
    assert score_outcome({"score": 0, "day_type": "mixed"}, candles)["actual_type"] == "trend"


def test_fhb_break_is_a_later_30m_close_outside_the_box():
    start = int(datetime(2024, 1, 8, 9, 15, tzinfo=IST).timestamp())
    times = start + np.arange(75, dtype=np.int64) * 300
    high = np.full(75, 100.0)
    low = np.full(75, 99.0)
    close = np.full(75, 99.5)
    assert fhb_break(times, high, low, close) is False
    close = close.copy()
    close[29] = 110.0
    assert fhb_break(times, high, low, close) is True
    assert fhb_break(times[1:], high[1:], low[1:], close[1:]) is None


def test_spearman_of_a_monotone_series_is_one():
    values = np.arange(10, dtype=np.float64)
    assert spearman(values, values * 3.0 + 1.0) == 1.0


def test_verdict_requires_a_real_gap_over_the_baseline():
    assert versus_baseline(70.0, 50.0, 50, panel_rho=0.4, baseline_rho=0.1, require_rank=True) == "beats the baseline"
    assert versus_baseline(52.0, 50.0, 80) == "no better than the baseline"
    assert versus_baseline(70.0, 50.0, 50, panel_rho=0.1, baseline_rho=0.4, require_rank=True).startswith("hit rate")


def test_holdout_day_is_neither_a_query_nor_a_match():
    rows = []
    facts = {}
    for i in range(40):
        day = 10_000 + i
        row = {
            "day": day,
            "holdout": i == 25,
            "features": {key: (float(i) if key == "gap_atr" else 1.0) for key in FEATURE_KEYS},
            "rest": 50.0 + i,
            "trend": i >= 20,
            "fhb": i % 2 == 0,
            "yday_range": 40.0 + i,
        }
        rows.append(row)
        facts[day] = {"rest": row["rest"], "trend": row["trend"], "fhb": row["fhb"], "yday_range": row["yday_range"]}
    nearest = _nearest(rows, 30)
    assert nearest
    assert all(row["day"] != 10_025 for row in nearest)
    report = walk_magnitude(rows, facts)
    assert report["range"]["sample_size"] == 39
    assert report["range"]["spearman"] is not None
    assert report["range"]["spearman"] > 0.5
