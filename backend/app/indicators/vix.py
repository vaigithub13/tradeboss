"""India VIX on any chart: the VIX close of the same-timeframe VIX bar for every chart candle.

Both series come from the same store and resampler (anchored to 09:15 IST), so bar times line up.
A chart candle with no VIX bar at its time is empty. A replay cursor clips the VIX bars the same way
it clips the chart, so a candle never shows a VIX value from after the cursor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.data.service import TimeframeUnavailable, UnknownTimeframe, get_candles
from app.data.store import CandleStore, SymbolNotFound

VIX_SYMBOL = "NSE_INDEX_India_VIX"


def align_vix(chart_times: Sequence[int], vix_candles: Sequence[Mapping[str, Any]]) -> np.ndarray:
    """VIX close per chart time (NaN where the VIX has no bar at that time)."""
    by_time = {int(c["time"]): float(c["close"]) for c in vix_candles}
    return np.array([by_time.get(int(t), np.nan) for t in chart_times], dtype=float)


def vix_for_chart(
    store: CandleStore,
    chart_candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    session_types: Sequence[str],
    *,
    cursor: int | None,
) -> np.ndarray:
    if not chart_candles:
        return np.zeros(0, dtype=float)
    times = [int(c["time"]) for c in chart_candles]
    try:
        vix = get_candles(
            store, VIX_SYMBOL, timeframe,
            from_time=times[0], to_time=times[-1], session_types=list(session_types), cursor=cursor,
        ).candles
    except (SymbolNotFound, TimeframeUnavailable, UnknownTimeframe):
        return np.full(len(times), np.nan)
    return align_vix(times, vix)


def vix_outputs(vix_close: np.ndarray) -> dict[str, np.ndarray]:
    """{"vix": close, "change": close minus the previous candle's close} (first candle: empty)."""
    close = np.asarray(vix_close, dtype=float)
    change = np.full(len(close), np.nan)
    if len(close) > 1:
        change[1:] = close[1:] - close[:-1]
    return {"vix": close, "change": change}
