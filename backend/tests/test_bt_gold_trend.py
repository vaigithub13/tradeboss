"""GOLD TREND research ports: catalog, session resampler, EA maths, entry gates."""

from __future__ import annotations

import pytest

from app.backtest.catalog import build_strategy, strategy_catalog
from app.strategies.gold_trend import GoldTrend2, GoldTrend9, GoldTrend14
from app.strategies.goldtrend_math import EMAx, RMA, SessionResampler, SupertrendEA
from tests.bt_helpers import MON, TUE, day_bars, fills, run


def test_in_catalog_with_defaults() -> None:
    names = {s["name"] for s in strategy_catalog()}
    assert {"gold_trend_9", "gold_trend_2", "gold_trend_14"} <= names
    g9 = build_strategy({"strategy": "gold_trend_9", "params": {}})
    g2 = build_strategy({"strategy": "gold_trend_2", "params": {}})
    g14 = build_strategy({"strategy": "gold_trend_14", "params": {}})
    assert isinstance(g9, GoldTrend9) and g9.stop_mult == 1.5 and g9.signal_minutes == 20
    assert isinstance(g2, GoldTrend2) and g2.stop_mult == 2.0 and g2.signal_minutes == 30
    assert isinstance(g14, GoldTrend14) and g14.stop_mult == 3.0 and g14.signal_minutes == 20


def test_rma_running_average_seed_then_wilder() -> None:
    r = RMA(3)
    assert r.update(3.0) == 3.0
    assert r.update(6.0) == 4.5
    assert r.update(9.0) == 6.0
    # Wilder: 6 + (12-6)/3 = 8
    assert r.update(12.0) == pytest.approx(8.0)


def test_ema_seeded_with_first_value() -> None:
    e = EMAx(3)
    assert e.update(10.0) == 10.0
    # k=0.5 → 20*0.5 + 10*0.5 = 15
    assert e.update(20.0) == pytest.approx(15.0)


def test_session_resampler_20m_from_5m_anchored_0915() -> None:
    r = SessionResampler(20, source_minutes=5)
    # 09:15, 09:20, 09:25, 09:30 → one 20m bar (step_min=5)
    rows = day_bars(TUE, [(100.0, 101.0, 99.0, 100.0 + i * 0.1) for i in range(4)], step_min=5)
    done: list = []
    for bar in rows:
        done.extend(r.on_bar(bar))
    assert len(done) == 1
    assert done[0].minutes == 20
    assert done[0].open == 100.0
    assert done[0].close == pytest.approx(100.3)


def test_supertrend_ea_returns_direction() -> None:
    st = SupertrendEA(3, 2.0)
    d = None
    for h, l, c in [(10, 9, 9.5), (11, 9.5, 10.5), (12, 10, 11.5), (11, 9, 9.2), (8, 7, 7.5)]:
        d = st.update(h, l, c)
    assert d in (-1, 1)


def test_no_entry_before_warmup() -> None:
    s = GoldTrend2(warmup_bars=5000, warmup_days=1, st_atr=3, st_mult=1.0)
    rows = [(100.0 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.2 + i * 0.1) for i in range(375)]
    res = run(s, day_bars(MON, rows) + day_bars(TUE, rows), square_off="15:15")
    assert fills(res) == []


def test_mode_long_only_rejects_short_construction() -> None:
    s = build_strategy({"strategy": "gold_trend_14", "params": {"mode": "long_only"}})
    assert s.mode == "long_only"


def test_bad_stop_rejected() -> None:
    with pytest.raises(ValueError, match="stop_mult"):
        GoldTrend9(stop_mult=0)
    with pytest.raises(ValueError, match="tenkan"):
        GoldTrend9(tenkan=30, kijun=26)
