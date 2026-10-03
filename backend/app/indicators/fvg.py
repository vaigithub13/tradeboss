"""Fair value gaps. The chart and a backtest both call `fvg_boxes`.

Bullish: low of candle 3 is strictly above high of candle 1.
Bearish: high of candle 3 is strictly below low of candle 1.
The box starts at candle 1 and is formed when candle 3 closes.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.data.resampler import TIMEFRAMES

IST = ZoneInfo("Asia/Kolkata")

_MITIGATION = ("touch", "half", "full")
_WHEN = ("stop", "fade")
_GAP_MODE = ("points", "percent")
_SESSION_GAPS = ("include", "exclude")
_TIMEFRAMES = ("", *TIMEFRAMES)

_DEFAULTS: dict[str, Any] = {
    "min_gap": 0,
    "min_gap_mode": "points",
    "mitigation": "touch",
    "when_mitigated": "stop",
    "show_last": 10,
    "timeframe": "",
    "session_gaps": "include",
}


def bar_close_seconds(timeframe: str) -> int:
    """Seconds after a bar's start when that bar has closed (session close for 1D / 1W)."""
    minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(timeframe)
    if minutes is not None:
        return minutes * 60
    session = (15 * 60 + 30 - (9 * 60 + 15)) * 60
    if timeframe == "1D":
        return session
    if timeframe == "1W":
        return 4 * 86_400 + session
    raise ValueError(f"Unknown timeframe {timeframe!r}")


def validate_fvg_params(params: Any) -> dict[str, Any]:
    given = dict(params or {})
    unknown = sorted(set(given) - set(_DEFAULTS))
    if unknown:
        raise ValueError(f"fvg: unknown parameter(s) {unknown}; expected {list(_DEFAULTS)}")
    min_gap = given.get("min_gap", _DEFAULTS["min_gap"])
    if isinstance(min_gap, bool) or not isinstance(min_gap, (int, float)) or not math.isfinite(min_gap) or min_gap < 0:
        raise ValueError("fvg: min_gap must be a number >= 0")
    mode = given.get("min_gap_mode", _DEFAULTS["min_gap_mode"])
    if mode not in _GAP_MODE:
        raise ValueError(f"fvg: min_gap_mode must be one of {list(_GAP_MODE)}")
    mitigation = given.get("mitigation", _DEFAULTS["mitigation"])
    if mitigation not in _MITIGATION:
        raise ValueError(f"fvg: mitigation must be one of {list(_MITIGATION)}")
    when = given.get("when_mitigated", _DEFAULTS["when_mitigated"])
    if when not in _WHEN:
        raise ValueError(f"fvg: when_mitigated must be one of {list(_WHEN)}")
    show = given.get("show_last", _DEFAULTS["show_last"])
    if isinstance(show, bool) or not isinstance(show, int) or not 1 <= show <= 500:
        raise ValueError("fvg: show_last must be a whole number from 1 to 500")
    timeframe = given.get("timeframe", _DEFAULTS["timeframe"])
    if timeframe not in _TIMEFRAMES:
        raise ValueError(f"fvg: timeframe must be empty or one of {list(TIMEFRAMES)}")
    gaps = given.get("session_gaps", _DEFAULTS["session_gaps"])
    if gaps not in _SESSION_GAPS:
        raise ValueError(f"fvg: session_gaps must be one of {list(_SESSION_GAPS)}")
    gap = float(min_gap)
    return {
        "min_gap": int(gap) if gap.is_integer() else gap,
        "min_gap_mode": mode,
        "mitigation": mitigation,
        "when_mitigated": when,
        "show_last": show,
        "timeframe": timeframe,
        "session_gaps": gaps,
    }


def _session(time: int) -> str:
    return datetime.fromtimestamp(int(time), IST).date().isoformat()


def _reached(direction: str, bottom: float, top: float, high: float, low: float, mitigation: str) -> bool:
    mid = (bottom + top) / 2
    if direction == "bull":
        if mitigation == "touch":
            return low <= top
        if mitigation == "half":
            return low <= mid
        return low <= bottom
    if mitigation == "touch":
        return high >= bottom
    if mitigation == "half":
        return high >= mid
    return high >= top


def fvg_boxes(frame: pd.DataFrame, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Boxes on `frame`. Missing settings use the defaults. `bar_seconds` and `as_of` are call-time.

    A box whose candle 3 has not closed by `as_of` is left out (`time + bar_seconds > as_of`).
    `bar_seconds` of 0 or a missing `as_of` keeps every formed box.
    `session_gaps` `exclude` drops a gap whose candle 1 and candle 3 fall on different IST dates.
    """
    raw = dict(_DEFAULTS)
    raw.update(params or {})
    if len(frame) < 3:
        return []
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    times = frame["time"].to_numpy()
    min_gap = float(raw["min_gap"])
    gap_mode = str(raw["min_gap_mode"])
    mitigation = str(raw["mitigation"])
    when = str(raw["when_mitigated"])
    show_last = int(raw["show_last"])
    session_gaps = str(raw["session_gaps"])
    bar_seconds = int(raw.get("bar_seconds") or 0)
    as_of = raw.get("as_of")
    boxes: list[dict[str, Any]] = []
    n = len(frame)
    for i in range(2, n):
        if bar_seconds > 0 and as_of is not None and int(times[i]) + bar_seconds > int(as_of):
            continue
        c1 = i - 2
        bull = low[i] > high[c1]
        bear = high[i] < low[c1]
        if not bull and not bear:
            continue
        if session_gaps == "exclude" and _session(int(times[c1])) != _session(int(times[i])):
            continue
        if bull:
            bottom, top, direction = float(high[c1]), float(low[i]), "bull"
        else:
            bottom, top, direction = float(high[i]), float(low[c1]), "bear"
        size = top - bottom
        if gap_mode == "percent":
            base = float(close[i])
            if base == 0 or size / base * 100 < min_gap:
                continue
        elif size < min_gap:
            continue
        end: int | None = None
        for j in range(i + 1, n):
            if _reached(direction, bottom, top, float(high[j]), float(low[j]), mitigation):
                end = j
                break
        mitigated = end is not None
        fade = mitigated and when == "fade"
        boxes.append(
            {
                "direction": direction,
                "start_index": c1,
                "formed_index": i,
                "bottom": bottom,
                "top": top,
                "mitigated": mitigated,
                "end_index": end,
                "extends": (not mitigated) or fade,
                "faded": fade,
            }
        )
    if show_last >= 0:
        boxes = boxes[-show_last:]
    return boxes


def compute_fvg(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, np.ndarray]:
    """Formation prices on candle 3 only. Every other bar, including the mitigation bar, is NaN."""
    n = len(df)
    bull_bottom = np.full(n, np.nan)
    bull_top = np.full(n, np.nan)
    bear_bottom = np.full(n, np.nan)
    bear_top = np.full(n, np.nan)
    for box in fvg_boxes(df, params):
        i = int(box["formed_index"])
        if box["direction"] == "bull":
            bull_bottom[i] = box["bottom"]
            bull_top[i] = box["top"]
        else:
            bear_bottom[i] = box["bottom"]
            bear_top[i] = box["top"]
    return {
        "bull_bottom": bull_bottom,
        "bull_top": bull_top,
        "bear_bottom": bear_bottom,
        "bear_top": bear_top,
    }
