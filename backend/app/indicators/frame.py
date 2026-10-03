"""Candle dicts -> the DataFrame the indicator functions work on."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from app.data.candle import Candle

FRAME_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def candles_to_frame(candles: Sequence[Candle]) -> pd.DataFrame:
    """Columns time (int unix s), open, high, low, close, volume (floats); `oi` is dropped."""
    df = pd.DataFrame(
        {
            "time": [c["time"] for c in candles],
            "open": [c["open"] for c in candles],
            "high": [c["high"] for c in candles],
            "low": [c["low"] for c in candles],
            "close": [c["close"] for c in candles],
            "volume": [c["volume"] for c in candles],
        },
        columns=FRAME_COLUMNS,
    )
    df["time"] = df["time"].astype("int64")
    for col in FRAME_COLUMNS[1:]:
        df[col] = df[col].astype(float)
    return df
