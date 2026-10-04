"""Lazy loading: indicators computed chunk by chunk equal the full-history values (within 0.01).

The chart asks for the newest ~2000 bars, then older chunks as the user scrolls left. Each chunk
is computed with the usual warm-up, so values at chunk boundaries must be continuous.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.data.importer import build_frame, write_parquet
from app.data.service import get_candle_page, get_candles
from app.data.store import CandleStore
from app.indicators.frame import candles_to_frame
from app.indicators.registry import OUTPUTS, IndicatorSpec, compute, validate_params
from app.indicators.service import compute_indicators
from tests.conftest import ist_ts

TOLERANCE = 0.01  # absolute, price units
REAL_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "candles"

SPECS: list[tuple[str, str, dict[str, object]]] = [
    ("sma", "sma", {"length": 20}),
    ("ema20", "ema", {"length": 20}),
    ("ema200", "ema", {"length": 200}),
    ("bb", "bb", {"length": 20, "mult": 2}),
    ("st", "supertrend", {"atr_length": 10, "multiplier": 3}),
    ("rsi", "rsi", {"length": 14}),
    ("stoch", "stoch", {"k_length": 14, "k_smoothing": 1, "d_smoothing": 3}),
    ("macd", "macd", {"fast": 12, "slow": 26, "signal": 9}),
]


def spec(id_: str, type_: str, params: dict[str, object]) -> IndicatorSpec:
    return IndicatorSpec(id=id_, type=type_, params=validate_params(type_, params))


def chunks_newest_first(store: CandleStore, symbol: str, tf: str, size: int, count: int) -> list[list[int]]:
    out: list[list[int]] = []
    before: int | None = None
    for _ in range(count):
        page = get_candle_page(store, symbol, tf, limit=size, before=before)
        if not page.candles:
            break
        out.append([c["time"] for c in page.candles])
        if not page.has_more:
            break
        before = page.candles[0]["time"]
    return out


def assert_chunks_match_full(store: CandleStore, symbol: str, tf: str, specs: list[IndicatorSpec],
                             size: int, count: int) -> int:  # fmt: skip
    candles = get_candles(store, symbol, tf).candles
    frame = candles_to_frame(candles)
    index = {c["time"]: i for i, c in enumerate(candles)}
    full = {s.id: compute(frame, s.type, s.params) for s in specs}

    chunks = chunks_newest_first(store, symbol, tf, size, count)
    for times in chunks:
        res = compute_indicators(store, symbol, tf, specs, from_time=times[0], to_time=times[-1])
        assert res.times == times
        rows = [index[t] for t in times]
        for out in res.indicators:
            for name in OUTPUTS[out.type]:
                got = np.array([np.nan if v is None else v for v in out.outputs[name]])
                np.testing.assert_allclose(
                    got, full[out.id][name][rows], rtol=0, atol=TOLERANCE, equal_nan=True,
                    err_msg=f"{out.id}.{name} {tf} chunk starting {times[0]}",
                )  # fmt: skip
    return len(chunks)


@pytest.mark.skipif(not (REAL_DATA_DIR / "NIFTY50" / "5m.parquet").exists(), reason="no real data")
@pytest.mark.parametrize("tf", ["5m", "15m"])
def test_real_nifty_chunks_equal_full_history(tf: str) -> None:
    store = CandleStore(REAL_DATA_DIR)
    specs = [spec(*s) for s in SPECS]
    n = assert_chunks_match_full(store, "NIFTY50", tf, specs, size=2000, count=6)
    assert n == 6  # six consecutive chunks, boundaries included


@pytest.fixture
def volume_store(tmp_path: Path) -> CandleStore:
    """40 weekday sessions of 75 five-minute bars with a seeded random walk and volume."""
    rng = np.random.default_rng(11)
    bars = []
    price = 20000.0
    day, made = 0, 0
    while made < 40:
        d = ist_ts(2024, 1, 1, 9, 15) + day * 86400
        day += 1
        if (day - 1) % 7 >= 5:
            continue
        made += 1
        for i in range(75):
            o = price
            price = o + rng.normal(0, 8)
            bars.append(
                {
                    "t": (d + 300 * i) * 1000,
                    "open": o,
                    "high": max(o, price) + rng.uniform(0, 6),
                    "low": min(o, price) - rng.uniform(0, 6),
                    "close": price,
                    "volume": int(rng.integers(1000, 5000)),
                }
            )
    df, _ = build_frame(bars)
    write_parquet(df, tmp_path / "candles" / "NIFTY50" / "5m.parquet")
    return CandleStore(tmp_path / "candles")


@pytest.mark.parametrize("size", [300, 450])
def test_chunks_equal_full_history_including_vwap(volume_store: CandleStore, size: int) -> None:
    specs = [spec(*s) for s in SPECS] + [spec("vwap", "vwap", {})]
    n = assert_chunks_match_full(volume_store, "NIFTY50", "5m", specs, size=size, count=10)
    assert n >= 6


def test_chunk_boundary_values_are_continuous(volume_store: CandleStore) -> None:
    """The last bar of the older chunk and the first bar of the newer chunk come from different
    requests; their values must still line up with the full-history series."""
    s = spec("ema", "ema", {"length": 50})
    candles = get_candles(volume_store, "NIFTY50", "5m").candles
    full = compute(candles_to_frame(candles), s.type, s.params)["ema"]
    newer, older = chunks_newest_first(volume_store, "NIFTY50", "5m", 600, 2)
    res_new = compute_indicators(volume_store, "NIFTY50", "5m", [s], from_time=newer[0], to_time=newer[-1])
    res_old = compute_indicators(volume_store, "NIFTY50", "5m", [s], from_time=older[0], to_time=older[-1])
    boundary = len(candles) - 600  # index of the newer chunk's first bar
    new_first = res_new.indicators[0].outputs["ema"][0]
    old_last = res_old.indicators[0].outputs["ema"][-1]
    assert new_first == pytest.approx(full[boundary], abs=TOLERANCE)
    assert old_last == pytest.approx(full[boundary - 1], abs=TOLERANCE)


def test_tiny_first_chunk_is_still_converged(volume_store: CandleStore) -> None:
    """A 20-bar newest chunk still gets the full warm-up from the older history."""
    s = spec("st", "supertrend", {"atr_length": 10, "multiplier": 3})
    candles = get_candles(volume_store, "NIFTY50", "5m").candles
    full = compute(candles_to_frame(candles), s.type, s.params)
    (times,) = chunks_newest_first(volume_store, "NIFTY50", "5m", 20, 1)
    res = compute_indicators(volume_store, "NIFTY50", "5m", [s], from_time=times[0], to_time=times[-1])
    for name in ("supertrend", "direction"):
        got = np.array([np.nan if v is None else v for v in res.indicators[0].outputs[name]])
        np.testing.assert_allclose(got, full[name][-20:], rtol=0, atol=TOLERANCE)
