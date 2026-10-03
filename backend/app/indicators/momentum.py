"""RSI and MACD (TradingView definitions)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.basic import ema, rma


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


def macd(src: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """ta.macd: ema(fast) - ema(slow); signal = ema(macd, signal); hist = macd - signal."""
    line = ema(src, fast) - ema(src, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})
