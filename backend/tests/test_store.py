"""CandleStore: Parquet files read through DuckDB."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.data.importer import build_frame, write_parquet
from app.data.store import CandleStore, SymbolNotFound
from tests.conftest import ist_ts, raw_5m_session


def test_symbols_and_base_minutes(store: CandleStore) -> None:
    assert store.symbols() == ["NIFTY50"]
    assert store.base_minutes("NIFTY50") == 5


def test_unknown_symbol(store: CandleStore) -> None:
    with pytest.raises(SymbolNotFound):
        store.load("NOPE")
    with pytest.raises(SymbolNotFound):
        store.base_minutes("../etc")


def test_default_includes_normal_and_weekend_full_only(store: CandleStore) -> None:
    candles, src_min = store.load("NIFTY50")
    assert src_min == 5
    assert len(candles) == 75 * 4  # Mon, Tue, Mon, Sat(weekend_full)
    assert candles[-1]["time"] == ist_ts(2024, 11, 16, 15, 25)


@pytest.mark.parametrize(
    ("types", "expected_bars"),
    [
        (["normal"], 225),
        (["weekend_full"], 75),
        (["special_short"], 12),
        (["muhurat"], 12),
        (["normal", "muhurat"], 237),
        (["normal", "weekend_full", "special_short", "muhurat"], 75 * 4 + 24),
    ],
)
def test_session_type_filter(store: CandleStore, types: list[str], expected_bars: int) -> None:
    candles, _ = store.load("NIFTY50", session_types=types)
    assert len(candles) == expected_bars
    assert [c["time"] for c in candles] == sorted(c["time"] for c in candles)


def test_candle_shape(store: CandleStore) -> None:
    candles, _ = store.load("NIFTY50")
    first = candles[0]
    assert first == {
        "time": ist_ts(2024, 10, 28, 9, 15),
        "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0,
        "volume": 10.0, "oi": None,
    }  # fmt: skip
    assert isinstance(first["time"], int)


def test_time_range_filter_is_inclusive(store: CandleStore) -> None:
    lo = ist_ts(2024, 10, 29, 9, 15)
    hi = ist_ts(2024, 10, 29, 9, 25)
    candles, _ = store.load("NIFTY50", from_time=lo, to_time=hi)
    assert [c["time"] for c in candles] == [lo, lo + 300, lo + 600]


def test_dates_of_type(store: CandleStore) -> None:
    assert store.dates_of_type("NIFTY50", "muhurat") == {date(2024, 11, 1)}
    assert store.dates_of_type("NIFTY50", "weekend_full") == {date(2024, 11, 16)}
    assert store.dates_of_type("NIFTY50", "special_short") == {date(2024, 11, 17)}
    assert store.dates_of_type("NIFTY50", "normal") == {
        date(2024, 10, 28), date(2024, 10, 29), date(2024, 11, 4),
    }  # fmt: skip


def test_an_empty_parquet_has_no_time_range(tmp_path: Path) -> None:
    write_parquet(pd.DataFrame({"time": pd.Series([], dtype="int64")}), tmp_path / "EMPTY" / "1m.parquet")
    store = CandleStore(tmp_path)
    assert store.symbols() == ["EMPTY"]
    with pytest.raises(SymbolNotFound):
        store.time_range("EMPTY")


def test_time_range_of_symbol_covers_all_bars(store: CandleStore) -> None:
    first, last = store.time_range("NIFTY50")
    assert first == ist_ts(2024, 10, 28, 9, 15)
    assert last == ist_ts(2024, 11, 17, 10, 55)  # special_short Sunday, last 5m bar


def test_finest_available_base_timeframe_wins(tmp_path: Path) -> None:
    df5, _ = build_frame(raw_5m_session(2024, 10, 28, 100))
    write_parquet(df5, tmp_path / "X" / "5m.parquet")
    df1, _ = build_frame(raw_5m_session(2024, 10, 28, 100)[:3])  # stand-in 1m file
    write_parquet(df1, tmp_path / "X" / "1m.parquet")
    s = CandleStore(tmp_path)
    assert s.base_minutes("X") == 1
    assert len(s.load("X", session_types=["normal", "special_short"])[0]) == 3
