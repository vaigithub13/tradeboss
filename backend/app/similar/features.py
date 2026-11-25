"""Candle features and which windows are allowed.

Intraday windows that cross the overnight gap are dropped. The gap is not a
feature. Muhurat, special, and weekend sessions are not loaded (callers pass
session type ``normal`` only).

A bar is usable at ``as_of`` only when its start time is at or before ``as_of``,
the same rule the chart uses in replay.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np

IST_OFFSET_S = 19_800
DAY_S = 86_400
ATR_N = 14
FEATURE_NAMES = ("log_return", "range_atr", "body_range", "close_location")
HORIZONS = (5, 10, 20)
DEFAULT_WINDOW = 30
ALLOWED_TIMEFRAMES = ("5m", "15m", "1h", "1D")
INDEX_SYMBOLS = frozenset({"NIFTY50", "SENSEX", "BANKNIFTY"})
VIX_SYMBOL = "NSE_INDEX_India_VIX"
# Four completed-bar clocks. The bar included is the one that closes at this minute.
SAMPLE_CLOCKS = (10 * 60 + 15, 11 * 60 + 30, 13 * 60 + 15, 14 * 60 + 15)
HOLDOUT_START = date(2026, 7, 1)
HOLDOUT_END = date(2026, 10, 1)
TIME_CAP_S = 120.0
TOP_K = 10
DAY_GAP_S = DAY_S
SIMILAR_DAYS = 20
CONTEXT_NOTE = "Read-only context. Not a signal. No order is sent."

_EPOCH = date(1970, 1, 1).toordinal()


def bar_seconds(timeframe: str) -> int:
    if timeframe == "1D":
        return DAY_S
    if timeframe == "1h":
        return 3600
    if timeframe.endswith("m"):
        return int(timeframe[:-1]) * 60
    raise ValueError(f"unsupported timeframe {timeframe}")


def ist_day(times: np.ndarray) -> np.ndarray:
    return (times.astype(np.int64) + IST_OFFSET_S) // DAY_S


def day_number(d: date) -> int:
    return d.toordinal() - _EPOCH


def date_of(day: int) -> date:
    return date.fromordinal(int(day) + _EPOCH)


def in_holdout(day: int) -> bool:
    d = date_of(int(day))
    return HOLDOUT_START <= d <= HOLDOUT_END


def holdout_mask(days: np.ndarray) -> np.ndarray:
    start = day_number(HOLDOUT_START)
    end = day_number(HOLDOUT_END)
    return (days >= start) & (days <= end)


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = ATR_N) -> np.ndarray:
    """ATR at each bar from that bar and earlier bars only."""
    prev = np.empty_like(close)
    prev[0] = close[0]
    prev[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    atr = np.empty_like(tr)
    if len(tr) == 0:
        return atr
    atr[0] = tr[0]
    alpha = 1.0 / n
    for i in range(1, len(tr)):
        atr[i] = atr[i - 1] + alpha * (tr[i] - atr[i - 1])
    return atr


def candle_features(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Four features per candle. Log return is close/open, so it stays inside the bar."""
    eps = 1e-12
    log_ret = np.log(np.maximum(close, eps) / np.maximum(open_, eps))
    atr = np.maximum(wilder_atr(high, low, close), eps)
    rng = high - low
    range_atr = rng / atr
    body = np.divide(np.abs(close - open_), np.maximum(rng, eps), out=np.zeros_like(rng), where=rng > eps)
    loc = np.divide(close - low, np.maximum(rng, eps), out=np.full_like(rng, 0.5), where=rng > eps)
    return np.column_stack([log_ret, range_atr, body, loc]).astype(np.float64)


def window_ends(times: np.ndarray, window: int, timeframe: str) -> np.ndarray:
    """End index of every window of ``window`` bars that does not cross an overnight gap.

    Daily windows are sequences of daily bars. Intraday windows must sit on one
    IST date and cover ``window`` adjacent bars of that timeframe.
    """
    n = len(times)
    if window < 2 or n < window:
        return np.empty(0, dtype=np.int64)
    intraday = timeframe != "1D"
    span = (window - 1) * bar_seconds(timeframe)
    ends = np.arange(window - 1, n, dtype=np.int64)
    starts = ends - (window - 1)
    if not intraday:
        return ends
    days = ist_day(times)
    same = days[starts] == days[ends]
    tight = (times[ends] - times[starts]) <= span
    return ends[same & tight]


def zscore_window(block: np.ndarray) -> np.ndarray:
    """Z-score each feature inside one window. A flat feature becomes zeros."""
    mu = block.mean(axis=0)
    sd = block.std(axis=0)
    out = np.zeros_like(block)
    ok = sd >= 1e-8
    out[:, ok] = (block[:, ok] - mu[ok]) / sd[ok]
    return out


def stack_windows(features: np.ndarray, ends: np.ndarray, window: int) -> np.ndarray:
    """Z-scored windows as rows of length ``window * n_features``."""
    if len(ends) == 0:
        return np.zeros((0, window * features.shape[1]), dtype=np.float32)
    offset = np.arange(window, dtype=np.int64)
    index = ends.astype(np.int64)[:, None] - (window - 1) + offset[None, :]
    blocks = features[index]
    mu = blocks.mean(axis=1, keepdims=True)
    sd = blocks.std(axis=1, keepdims=True)
    out = np.where(sd < 1e-8, 0.0, (blocks - mu) / np.where(sd < 1e-8, 1.0, sd))
    return out.reshape(len(ends), -1).astype(np.float32)


def clip_as_of(times: np.ndarray, as_of: int | None) -> np.ndarray:
    """Boolean mask of bars whose start is at or before ``as_of``."""
    if as_of is None:
        return np.ones(len(times), dtype=bool)
    return times <= int(as_of)


def similarity(distance: float, dim: int) -> float:
    scale = math.sqrt(max(dim, 1))
    value = 100.0 / (1.0 + distance / scale)
    return float(max(0.0, min(100.0, value)))
