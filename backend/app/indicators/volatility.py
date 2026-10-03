"""True range, ATR and Supertrend (TradingView definitions)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.indicators.basic import rma


def true_range(df: pd.DataFrame) -> pd.Series:
    """TR with ta.atr's convention: bar 0 = high - low (Pine's bare `ta.tr` is NaN there).

    Later bars: max(high - low, |high - prev close|, |low - prev close|).
    """
    high, low = df["high"], df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1, skipna=True)
    return tr


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    """ta.atr: Wilder RMA of the true range; first value at bar length-1."""
    return rma(true_range(df), length)


def supertrend(df: pd.DataFrame, atr_length: int, multiplier: float) -> pd.DataFrame:
    """ta.supertrend. direction: -1 = UPTREND (line = lower band), +1 = DOWNTREND (upper band).

    Follows Pine's reference implementation step by step, including its nz()/na() quirks.
    NaN until the first ATR (bar atr_length-1), where direction starts at +1.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(close)
    a = atr(df, atr_length).to_numpy()
    hl2 = (high + low) / 2.0

    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    prev_lower = 0.0  # nz(lowerBand[1])
    prev_upper = 0.0  # nz(upperBand[1])
    prev_upper_raw = math.nan  # upperBand[1] as stored (for the == comparison)
    prev_st = math.nan
    prev_close = math.nan
    prev_atr = math.nan
    for i in range(n):
        lower = hl2[i] - multiplier * a[i]
        upper = hl2[i] + multiplier * a[i]
        # NaN comparisons are False, exactly like Pine's na handling.
        lower = lower if (lower > prev_lower or prev_close < prev_lower) else prev_lower
        upper = upper if (upper < prev_upper or prev_close > prev_upper) else prev_upper
        if math.isnan(prev_atr):
            d = 1.0
        elif prev_st == prev_upper_raw:
            d = -1.0 if close[i] > upper else 1.0
        else:
            d = 1.0 if close[i] < lower else -1.0
        line = lower if d == -1.0 else upper
        if not math.isnan(a[i]):
            st[i] = line
            direction[i] = d
        prev_st = line if not math.isnan(a[i]) else math.nan
        prev_upper_raw = upper
        prev_lower = 0.0 if math.isnan(lower) else lower
        prev_upper = 0.0 if math.isnan(upper) else upper
        prev_close = close[i]
        prev_atr = a[i]
    return pd.DataFrame({"supertrend": st, "direction": direction}, index=df.index)
