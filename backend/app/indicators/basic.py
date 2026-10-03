"""Moving averages, standard deviation, Bollinger Bands, price sources (Pine ta.* semantics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

SOURCES: tuple[str, ...] = ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4")


def source_series(df: pd.DataFrame, source: str) -> pd.Series:
    """Price series for a Pine-style `source` name."""
    if source in ("open", "high", "low", "close"):
        return df[source].astype(float)
    if source == "hl2":
        return (df["high"] + df["low"]) / 2
    if source == "hlc3":
        return (df["high"] + df["low"] + df["close"]) / 3
    if source == "ohlc4":
        return (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    raise ValueError(f"Unknown source {source!r}; expected one of {list(SOURCES)}")


def sma(src: pd.Series, length: int) -> pd.Series:
    """ta.sma: mean of the last `length` values; NaN for the first length-1 bars."""
    return src.rolling(length, min_periods=length).mean()


def ema(src: pd.Series, length: int) -> pd.Series:
    """ta.ema: alpha = 2/(length+1), seeded with the first available source value."""
    return src.ewm(alpha=2.0 / (length + 1), adjust=False).mean()


def rma(src: pd.Series, length: int) -> pd.Series:
    """ta.rma (Wilder): alpha = 1/length, seeded with sma(src, length); NaN before the seed."""
    values = src.to_numpy(dtype=float)
    seed = sma(src, length).to_numpy()
    valid = np.flatnonzero(~np.isnan(seed))
    out = np.full(len(values), np.nan)
    if len(valid) == 0:
        return pd.Series(out, index=src.index)
    first = int(valid[0])
    tail = values[first:].copy()
    tail[0] = seed[first]
    out[first:] = pd.Series(tail).ewm(alpha=1.0 / length, adjust=False).mean().to_numpy()
    return pd.Series(out, index=src.index)


def stdev(src: pd.Series, length: int) -> pd.Series:
    """ta.stdev: POPULATION standard deviation (Pine default, biased=true)."""
    # Shift by a CONSTANT for numerical stability at ~25000 price levels. It must come from the
    # first bar, not from the whole series' mean: that mean contains the future, so a value
    # would change (by an ulp) when later bars arrive - a look-ahead the backtest tests catch.
    valid = src.dropna()
    shift = float(valid.iloc[0]) if len(valid) else 0.0
    return (src - shift).rolling(length, min_periods=length).std(ddof=0)


def bollinger(src: pd.Series, length: int, mult: float) -> pd.DataFrame:
    """ta.bb: basis = sma, upper/lower = basis +/- mult * population stdev."""
    basis = sma(src, length)
    dev = mult * stdev(src, length)
    return pd.DataFrame({"basis": basis, "upper": basis + dev, "lower": basis - dev})
