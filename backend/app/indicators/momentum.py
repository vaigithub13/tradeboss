"""RSI, MACD, and Stochastic (TradingView definitions)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.basic import ema, rma, sma


def rsi(src: pd.Series, length: int) -> pd.Series:
    """ta.rsi: Wilder RMA of gains / losses.

    down == 0 -> 100, up == 0 -> 0, else 100 - 100/(1 + up/down) (checked in that order, so a flat
    series gives 100). The first value is at bar `length` because change() is NaN on bar 0.
    """
    change = src.diff()
    up = rma(change.clip(lower=0), length).to_numpy()
    down = rma((-change).clip(lower=0), length).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        value = 100.0 - 100.0 / (1.0 + up / down)
    value = np.where(down == 0, 100.0, np.where(up == 0, 0.0, value))
    value = np.where(np.isnan(up) | np.isnan(down), np.nan, value)
    return pd.Series(value, index=src.index)


def stochastic(df: pd.DataFrame, k_length: int, k_smoothing: int, d_smoothing: int) -> pd.DataFrame:
    """TradingView Stochastic.

    raw %K = 100 * (close - lowest(low, k_length)) / (highest(high, k_length) - lowest(low, k_length)).
    %K = SMA(raw, k_smoothing). %D = SMA(%K, d_smoothing).
    A bar whose highest high equals its lowest low is na, and an SMA that includes that na is na.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    highest = high.rolling(k_length, min_periods=k_length).max().to_numpy()
    lowest = low.rolling(k_length, min_periods=k_length).min().to_numpy()
    span = highest - lowest
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = 100.0 * (close.to_numpy(dtype=float) - lowest) / span
    raw = np.where((span == 0) | np.isnan(span), np.nan, raw)
    k = sma(pd.Series(raw, index=df.index), k_smoothing)
    d = sma(k, d_smoothing)
    return pd.DataFrame({"k": k, "d": d})


def macd(src: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """ta.macd: ema(fast) - ema(slow); signal = ema(macd, signal); hist = macd - signal."""
    line = ema(src, fast) - ema(src, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})
