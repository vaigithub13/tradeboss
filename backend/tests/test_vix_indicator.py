"""India VIX indicator: one VIX close per chart candle, its change, thresholds, cursor and live refresh."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.data.importer import build_frame, write_parquet
from app.data.store import CandleStore
from app.indicators.registry import IndicatorSpec, compute, validate_params, warmup_bars
from app.indicators.service import compute_indicators
from app.indicators.vix import VIX_SYMBOL, align_vix, vix_outputs
from app.live.service import symbols_dirtied_by
from tests.conftest import ist_ts, raw_5m_session, synthetic_raw_bars


@pytest.fixture
def store(tmp_path: Path) -> CandleStore:
    df, _ = build_frame(synthetic_raw_bars())
    write_parquet(df, tmp_path / "candles" / "NIFTY50" / "5m.parquet")
    # VIX only on the two Monday/Tuesday sessions; price = 13 + i/10 on Monday, 15 + i/10 on Tuesday
    vix = raw_5m_session(2024, 10, 28, 0) + raw_5m_session(2024, 10, 29, 0)
    for i, b in enumerate(vix):
        level = 13.0 if i < 75 else 15.0
        k = i % 75
        b.update(open=level + k / 10, high=level + k / 10 + 0.05, low=level + k / 10 - 0.05,
                 close=level + k / 10 + 0.02, volume=0)
    vdf, _ = build_frame(vix)
    write_parquet(vdf, tmp_path / "candles" / VIX_SYMBOL / "5m.parquet")
    return CandleStore(tmp_path / "candles")


def test_align_and_change() -> None:
    a = align_vix([100, 200, 300], [{"time": 100, "close": 13.5}, {"time": 300, "close": 15.2}])
    assert a[0] == 13.5 and np.isnan(a[1]) and a[2] == 15.2
    out = vix_outputs(np.array([13.0, 13.5, np.nan, 14.0]))
    assert np.isnan(out["change"][0]) and out["change"][1] == pytest.approx(0.5)
    assert np.isnan(out["change"][2]) and np.isnan(out["change"][3])


def test_params_and_registry() -> None:
    assert validate_params("vix", {}) == {"low": 13.0, "high": 15.0, "shade": "on"}
    with pytest.raises(ValueError, match="low level"):
        validate_params("vix", {"low": 16, "high": 15})
    assert warmup_bars("vix", validate_params("vix", {}), 5) == 500  # the global floor
    import pandas as pd

    frame = pd.DataFrame({"time": [1], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [0.0]})
    with pytest.raises(ValueError, match="vix_close"):
        compute(frame, "vix", validate_params("vix", {}))


def test_every_chart_candle_gets_the_vix_close_at_the_same_time(store: CandleStore) -> None:
    mon = ist_ts(2024, 10, 28, 9, 15)
    tue_end = ist_ts(2024, 10, 29, 15, 25)
    res = compute_indicators(store, "NIFTY50", "5m", [IndicatorSpec("v", "vix", validate_params("vix", {}))],
                             from_time=mon, to_time=tue_end)
    out = res.indicators[0].outputs
    assert res.times[0] == mon and len(res.times) == 150
    assert out["vix"][0] == pytest.approx(13.02)
    assert out["vix"][75] == pytest.approx(15.02)  # Tuesday 09:15
    assert out["change"][75] == pytest.approx(15.02 - (13.0 + 7.4 + 0.02))
    assert out["change"][1] == pytest.approx(0.1)


def test_a_day_without_vix_bars_is_empty(store: CandleStore) -> None:
    nov4 = ist_ts(2024, 11, 4, 9, 15)
    res = compute_indicators(store, "NIFTY50", "5m", [IndicatorSpec("v", "vix", validate_params("vix", {}))],
                             from_time=nov4, to_time=nov4 + 600)
    assert res.indicators[0].outputs["vix"] == [None, None, None]


def test_15m_uses_the_15m_vix_bar(store: CandleStore) -> None:
    mon = ist_ts(2024, 10, 28, 9, 15)
    res = compute_indicators(store, "NIFTY50", "15m", [IndicatorSpec("v", "vix", validate_params("vix", {}))],
                             from_time=mon, to_time=mon + 900)
    # the 09:15 15m bar closes with the 09:25 5m bar: 13 + 0.2 + 0.02
    assert res.indicators[0].outputs["vix"][0] == pytest.approx(13.22)


def test_a_replay_cursor_never_shows_a_later_vix(store: CandleStore) -> None:
    mon = ist_ts(2024, 10, 28, 9, 15)
    cursor = mon + 300  # the 09:20 bar is the last one seen
    res = compute_indicators(store, "NIFTY50", "5m", [IndicatorSpec("v", "vix", validate_params("vix", {}))],
                             from_time=mon, cursor=cursor)
    assert max(res.times) <= cursor
    assert res.indicators[0].outputs["vix"][-1] == pytest.approx(13.12)


def test_a_vix_tick_refreshes_the_nifty_chart() -> None:
    vix = "NSE_INDEX_India_VIX"
    assert symbols_dirtied_by({vix}, future_dir=None, vix_dir=vix) == {vix, "NIFTY50"}
    assert symbols_dirtied_by({"NIFTY50"}, future_dir=None, vix_dir=vix) == {"NIFTY50"}
