"""Shared fixtures: a tiny synthetic NIFTY50 5m store (never touches real data)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.data.importer import build_frame, write_parquet
from app.data.store import CandleStore

IST = timezone(timedelta(hours=5, minutes=30))


def ist_ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def raw_5m_session(
    y: int, mo: int, d: int, base: float, *, start: tuple[int, int] = (9, 15), bars: int = 75
) -> list[dict[str, Any]]:
    """5m tape bars. i-th bar: open=base+i, high=base+i+2, low=base+i-1, close=base+i+1, vol 10."""
    t0 = ist_ts(y, mo, d, *start)
    return [
        {
            "t": (t0 + 300 * i) * 1000,
            "open": base + i,
            "high": base + i + 2,
            "low": base + i - 1,
            "close": base + i + 1,
            "volume": 10,
        }
        for i in range(bars)
    ]


def synthetic_raw_bars(volume: float = 10) -> list[dict[str, Any]]:
    """Mon 2024-10-28 normal (base 100), Tue 10-29 normal (200),
    Fri 11-01 muhurat 18:00-18:55 (1000), Mon 11-04 normal (300),
    Sat 11-16 FULL weekend session -> weekend_full (500),
    Sun 11-17 12 bars from 10:00 -> special_short (600)."""
    bars = (
        raw_5m_session(2024, 10, 28, 100)
        + raw_5m_session(2024, 10, 29, 200)
        + raw_5m_session(2024, 11, 1, 1000, start=(18, 0), bars=12)
        + raw_5m_session(2024, 11, 4, 300)
        + raw_5m_session(2024, 11, 16, 500)
        + raw_5m_session(2024, 11, 17, 600, start=(10, 0), bars=12)
    )
    for b in bars:
        b["volume"] = volume
    return bars


@pytest.fixture(autouse=True)
def _no_startup_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never download anything or read the real token."""
    from app.config import settings

    monkeypatch.setattr(settings, "snapshot_on_startup", False)
    monkeypatch.setattr(settings, "upstox_analytics_token", None)
    monkeypatch.setattr(settings, "live_feed_enabled", False)


@pytest.fixture
def candles_dir(tmp_path: Path) -> Path:
    df, _ = build_frame(synthetic_raw_bars())
    write_parquet(df, tmp_path / "candles" / "NIFTY50" / "5m.parquet")
    return tmp_path / "candles"


@pytest.fixture
def store(candles_dir: Path) -> CandleStore:
    return CandleStore(candles_dir)


@pytest.fixture
def zero_volume_store(tmp_path: Path) -> CandleStore:
    """Same sessions as `store` but every bar has volume 0 (index data like Nifty)."""
    df, _ = build_frame(synthetic_raw_bars(volume=0))
    write_parquet(df, tmp_path / "candles" / "NIFTY50" / "5m.parquet")
    return CandleStore(tmp_path / "candles")
