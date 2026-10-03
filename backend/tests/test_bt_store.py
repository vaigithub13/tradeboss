"""The engine on the real candle store: 1m source, resampled; sessions; same bars as the chart."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backtest.engine import run_backtest
from app.backtest.sources import StoreSource
from app.data.importer import build_frame, write_parquet
from app.data.service import TimeframeUnavailable, get_candles
from app.data.store import CandleStore
from tests.bt_helpers import Scripted, cfg
from tests.conftest import ist_ts


def minute_session(y: int, mo: int, d: int, base: float, *, start: tuple[int, int] = (9, 15), bars: int = 375) -> list[dict]:
    t0 = ist_ts(y, mo, d, *start)
    return [
        {"t": (t0 + 60 * i) * 1000, "open": base + i * 0.1, "high": base + i * 0.1 + 0.5, "low": base + i * 0.1 - 0.5,
         "close": base + i * 0.1 + 0.05, "volume": 10}
        for i in range(bars)
    ]


@pytest.fixture
def one_minute_store(tmp_path: Path) -> CandleStore:
    raw = (
        minute_session(2024, 10, 28, 100)  # Monday, normal
        + minute_session(2024, 11, 1, 1000, start=(18, 0), bars=60)  # Diwali muhurat
        + minute_session(2024, 11, 4, 300)  # Monday, normal
    )
    df, _ = build_frame(raw, bar_minutes=1)
    write_parquet(df, tmp_path / "candles" / "NIFTY1M" / "1m.parquet")
    return CandleStore(tmp_path / "candles")


def seen(store: CandleStore, **kw) -> list[int]:  # noqa: ANN003
    s = Scripted()
    run_backtest(s, StoreSource(store, "NIFTY1M"), cfg(timeframe="5m", **kw))
    return s.seen


def test_sessions_come_from_the_store_label_and_the_default_filter_excludes_muhurat(one_minute_store: CandleStore) -> None:
    assert len(seen(one_minute_store)) == 150  # two normal days x 75 five-minute bars
    assert len(seen(one_minute_store, session_types=("normal", "weekend_full", "muhurat"))) == 162  # + 12 muhurat bars


def test_the_bars_the_strategy_sees_are_exactly_the_chart_bars(one_minute_store: CandleStore) -> None:
    for types in (("normal", "weekend_full"), ("normal", "weekend_full", "muhurat")):
        chart = get_candles(one_minute_store, "NIFTY1M", "5m", session_types=types).candles
        assert seen(one_minute_store, session_types=types) == [c["time"] for c in chart]


def test_start_and_end_select_ist_dates(one_minute_store: CandleStore) -> None:
    got = seen(one_minute_store, start="2024-11-04")
    assert len(got) == 75 and got[0] == ist_ts(2024, 11, 4, 9, 15)
    assert len(seen(one_minute_store, end="2024-10-28")) == 75


def test_a_5m_only_store_works_for_15m_and_says_so_but_cannot_make_1m(store: CandleStore) -> None:
    s = Scripted()
    res = run_backtest(s, StoreSource(store, "NIFTY50"), cfg(timeframe="15m"))
    assert len(s.seen) == 4 * 25  # four included sessions in conftest: 3 normal + 1 weekend_full, 25 fifteen-minute bars each
    assert any("5m" in w for w in res.warnings)
    with pytest.raises(TimeframeUnavailable):
        run_backtest(Scripted(), StoreSource(store, "NIFTY50"), cfg(timeframe="1m"))
