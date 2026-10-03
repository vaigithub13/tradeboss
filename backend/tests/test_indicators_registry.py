"""Indicator registry spec: types, params + defaults, warm-up, compute().

The registry is the ONE entry point used by the chart API and (Phase 3) by backtests,
so both always run identical math.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.data.service import get_candles
from app.data.store import CandleStore
from app.indicators.frame import candles_to_frame
from app.indicators.registry import (
    INDICATOR_TYPES,
    OUTPUTS,
    PANES,
    compute,
    validate_params,
    warmup_bars,
)
from app.indicators.volume import VolumeRequired
from tests.conftest import ist_ts

# Warm-up proof tolerance: ABSOLUTE, in price units (1e-6 is unrealistic at Nifty's ~25000 level).
PRICE_TOLERANCE = 0.01


def test_types_panes_and_outputs() -> None:
    assert INDICATOR_TYPES == ("sma", "ema", "bb", "supertrend", "rsi", "macd", "vwap")
    assert PANES == {
        "sma": "price", "ema": "price", "bb": "price", "supertrend": "price", "vwap": "price",
        "rsi": "separate", "macd": "separate",
    }  # fmt: skip
    assert OUTPUTS == {
        "sma": ["sma"],
        "ema": ["ema"],
        "bb": ["basis", "upper", "lower"],
        "supertrend": ["supertrend", "direction"],
        "rsi": ["rsi"],
        "macd": ["macd", "signal", "hist"],
        "vwap": ["vwap"],
    }


# --------------------------------------------------------------------------- params
@pytest.mark.parametrize(
    ("itype", "expected"),
    [
        ("sma", {"length": 20, "source": "close"}),
        ("ema", {"length": 20, "source": "close"}),
        ("bb", {"length": 20, "mult": 2.0, "source": "close"}),
        ("supertrend", {"atr_length": 10, "multiplier": 3.0}),
        ("rsi", {"length": 14, "source": "close"}),
        ("macd", {"fast": 12, "slow": 26, "signal": 9, "source": "close"}),
        ("vwap", {"source": "hlc3"}),
    ],
)
def test_defaults_are_filled_in(itype: str, expected: dict[str, object]) -> None:
    assert validate_params(itype, {}) == expected


def test_overrides_are_kept() -> None:
    assert validate_params("ema", {"length": 50, "source": "hl2"}) == {"length": 50, "source": "hl2"}
    assert validate_params("supertrend", {"multiplier": 2}) == {"atr_length": 10, "multiplier": 2.0}


@pytest.mark.parametrize(
    ("itype", "params"),
    [
        ("nope", {}),  # unknown type
        ("ema", {"length": 0}),
        ("ema", {"length": -5}),
        ("ema", {"length": 100000}),
        ("ema", {"length": 2.5}),
        ("ema", {"source": "volume"}),
        ("ema", {"len": 20}),  # unknown parameter name
        ("bb", {"mult": 0}),
        ("rsi", {"length": 1}),
        ("supertrend", {"atr_length": 0}),
        ("supertrend", {"multiplier": -1}),
        ("macd", {"fast": 26, "slow": 12}),  # fast must be < slow
        ("macd", {"fast": 12, "slow": 12}),
        ("vwap", {"source": "nonsense"}),
    ],
)
def test_invalid_params_raise_value_error(itype: str, params: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_params(itype, params)


# --------------------------------------------------------------------------- warm-up
@pytest.mark.parametrize(
    ("itype", "params", "bar_minutes", "expected"),
    [
        # warm-up = max(per-indicator rule, 500); Supertrend: max(rule, 1000)
        ("sma", {"length": 20}, 5, 500),  # rule: 20
        ("bb", {"length": 20}, 5, 500),  # rule: 20
        ("ema", {"length": 20}, 5, 500),  # rule 8*(n+1) = 168
        ("rsi", {"length": 14}, 5, 500),  # rule 10*n = 140
        ("supertrend", {"atr_length": 10}, 5, 1000),  # rule 100 -> floor of 1000
        ("macd", {"fast": 12, "slow": 26, "signal": 9}, 5, 500),  # rule 8*27 + 8*10 = 296
        ("vwap", {}, 5, 500),  # rule: a full 24h of bars = 288
        ("vwap", {}, 60, 500),  # rule 24
        # long lengths: the rule wins over the floor
        ("sma", {"length": 600}, 5, 600),
        ("ema", {"length": 200}, 5, 1608),  # 8*201
        ("rsi", {"length": 100}, 5, 1000),  # 10*100
        ("supertrend", {"atr_length": 200}, 5, 2000),  # 10*200
        ("macd", {"fast": 50, "slow": 100, "signal": 50}, 5, 1216),  # 8*101 + 8*51
        ("vwap", {}, 1, 1440),  # a full 24h of 1m bars
    ],
)
def test_warmup_bars(itype: str, params: dict[str, object], bar_minutes: int, expected: int) -> None:
    assert warmup_bars(itype, validate_params(itype, params), bar_minutes) == expected


# --------------------------------------------------------------------------- compute()
def random_walk_frame(days: int = 40, seed: int = 7) -> pd.DataFrame:
    """`days` sessions of 75 five-minute bars with realistic-ish OHLCV and IST timestamps."""
    rng = np.random.default_rng(seed)
    n = days * 75
    close = 20000 + np.cumsum(rng.normal(0, 8, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0, 6, n)
    low = np.minimum(open_, close) - rng.uniform(0, 6, n)
    times = []
    day0 = ist_ts(2024, 1, 1, 9, 15)  # Monday 2024-01-01; weekdays only
    d = 0
    while len(times) < n:
        if (d % 7) < 5:
            times += [day0 + d * 86400 + 300 * i for i in range(75)]
        d += 1
    return pd.DataFrame(
        {
            "time": times[:n],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1000, 5000, n).astype(float),
        }
    )


def test_compute_returns_named_arrays_of_equal_length() -> None:
    df = random_walk_frame(days=3)
    for itype in INDICATOR_TYPES:
        out = compute(df, itype, validate_params(itype, {}))
        assert list(out) == OUTPUTS[itype]
        assert all(isinstance(v, np.ndarray) and len(v) == len(df) for v in out.values())


def test_compute_matches_the_underlying_function() -> None:
    from app.indicators.basic import ema

    df = random_walk_frame(days=3)
    out = compute(df, "ema", {"length": 5, "source": "hl2"})
    expected = ema((df["high"] + df["low"]) / 2, 5).to_numpy()
    np.testing.assert_allclose(out["ema"], expected)


def test_compute_vwap_without_volume_raises() -> None:
    df = random_walk_frame(days=2)
    df["volume"] = 0.0
    with pytest.raises(VolumeRequired):
        compute(df, "vwap", validate_params("vwap", {}))


@pytest.mark.parametrize(
    ("itype", "params"),
    [
        ("sma", {"length": 20}),
        ("ema", {"length": 20}),
        ("ema", {"length": 50, "source": "hlc3"}),
        ("bb", {"length": 20, "mult": 2}),
        ("supertrend", {"atr_length": 10, "multiplier": 3}),
        ("rsi", {"length": 14}),
        ("macd", {"fast": 12, "slow": 26, "signal": 9}),
        ("vwap", {}),
    ],
)
def test_warmup_makes_first_visible_values_equal_full_history_values(
    itype: str, params: dict[str, object]
) -> None:
    """Computing from (visible_start - warmup) gives the SAME numbers as computing from the
    very first bar, for every visible bar. This is what 'warm-up is long enough' means."""
    df = random_walk_frame(days=40)  # 3000 bars
    p = validate_params(itype, params)
    full = compute(df, itype, p)

    visible_start = 2500
    start = visible_start - warmup_bars(itype, p, bar_minutes=5)
    assert start > 0
    part = compute(df.iloc[start:].reset_index(drop=True), itype, p)

    for name in OUTPUTS[itype]:
        np.testing.assert_allclose(
            part[name][visible_start - start :],
            full[name][visible_start:],
            rtol=0,
            atol=PRICE_TOLERANCE,
            equal_nan=True,
            err_msg=f"{itype}.{name} still warming up at the first visible bar",
        )


# Proof on REAL Nifty data (skipped when the gitignored parquet is not present, e.g. in CI).
# For several visible-start points: compute from (start - warm-up) and compare against the
# full-history values at 0.01 absolute (price units) for every visible bar.
REAL_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "candles"
REAL_PARAMS: list[tuple[str, dict[str, object]]] = [
    ("sma", {"length": 20}),
    ("ema", {"length": 20}),
    ("ema", {"length": 200}),
    ("bb", {"length": 20, "mult": 2}),
    ("supertrend", {"atr_length": 10, "multiplier": 3}),
    ("supertrend", {"atr_length": 7, "multiplier": 2}),
    ("rsi", {"length": 14}),
    ("macd", {"fast": 12, "slow": 26, "signal": 9}),
    ("vwap", {}),
]


@pytest.mark.parametrize("timeframe", ["5m", "15m"])
@pytest.mark.parametrize(("itype", "params"), REAL_PARAMS, ids=lambda v: v if isinstance(v, str) else None)
def test_warmup_is_sufficient_on_real_nifty_data(
    itype: str, params: dict[str, object], timeframe: str
) -> None:
    if not (REAL_DATA_DIR / "NIFTY50" / "5m.parquet").exists():
        pytest.skip("real NIFTY50 parquet not imported")
    store = CandleStore(REAL_DATA_DIR)
    candles = get_candles(store, "NIFTY50", timeframe).candles  # the chart's default session filter
    df = candles_to_frame(candles)
    if itype == "vwap":
        # Nifty is an index: volume is all zero, VWAP is correctly refused (see service tests).
        df["volume"] = 1000.0 + (np.arange(len(df)) % 7)  # synthetic volume, math check only
    p = validate_params(itype, params)
    bar_minutes = int(timeframe[:-1])
    warm = warmup_bars(itype, p, bar_minutes)
    full = compute(df, itype, p)

    n = len(df)
    checked = 0
    for fraction in (0.1, 0.25, 0.4, 0.6, 0.8, 0.95):
        visible_start = int(n * fraction)
        start = visible_start - warm
        assert start > 0
        part = compute(df.iloc[start:].reset_index(drop=True), itype, p)
        for name in OUTPUTS[itype]:
            np.testing.assert_allclose(
                part[name][visible_start - start :],
                full[name][visible_start:],
                rtol=0,
                atol=PRICE_TOLERANCE,
                equal_nan=True,
                err_msg=f"{itype}{params}.{name} {timeframe}: still warming up at visible bar "
                f"{visible_start} ({candles[visible_start]['time']})",
            )
        checked += 1
    assert checked == 6


def test_too_little_warmup_would_be_wrong_sanity_check() -> None:
    """Guards the test above: with no warm-up an EMA(20) really does differ at the first bars."""
    df = random_walk_frame(days=40)
    p = validate_params("ema", {"length": 20})
    full = compute(df, "ema", p)["ema"]
    cold = compute(df.iloc[2500:].reset_index(drop=True), "ema", p)["ema"]
    assert abs(cold[0] - full[2500]) > 1.0
