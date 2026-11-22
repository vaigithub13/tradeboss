"""RSI(14) extreme plus a reversal bar and a VWAP distance above 1%."""

from __future__ import annotations

from app.scanner.bars import (
    Bar,
    closed_bars,
    entry_allowed,
    last_swing,
    nearest_level,
    session_vwap,
    wilder_atr,
    wilder_rsi,
)
from app.scanner.models import Hit


def _long_ok(bars: list[Bar], i: int, rsi: float, vwap: float) -> bool:
    close = float(bars[i]["close"])
    prev = float(bars[i - 1]["close"])
    if rsi >= 25 or close <= float(bars[i]["open"]) or close <= prev:
        return False
    if vwap <= 0 or close >= vwap:
        return False
    return abs(close - vwap) / vwap > 0.01


def _short_ok(bars: list[Bar], i: int, rsi: float, vwap: float) -> bool:
    close = float(bars[i]["close"])
    prev = float(bars[i - 1]["close"])
    if rsi <= 75 or close >= float(bars[i]["open"]) or close >= prev:
        return False
    if vwap <= 0 or close <= vwap:
        return False
    return abs(close - vwap) / vwap > 0.01


def find_rsi_extremes(bars: list[Bar], *, as_of: int | None = None) -> list[Hit]:
    bars = closed_bars(bars, as_of)
    rsis = wilder_rsi(bars, 14)
    vwaps = session_vwap(bars)
    atrs = wilder_atr(bars, 14)
    hits: list[Hit] = []
    for i in range(15, len(bars)):
        rsi, vwap = rsis[i], vwaps[i]
        if rsi is None or vwap is None or not entry_allowed(int(bars[i]["time"])):
            continue
        prev_rsi, prev_vwap = rsis[i - 1], vwaps[i - 1]
        long = _long_ok(bars, i, rsi, vwap)
        short = _short_ok(bars, i, rsi, vwap)
        if long and prev_rsi is not None and prev_vwap is not None and _long_ok(bars, i - 1, prev_rsi, prev_vwap):
            long = False
        if short and prev_rsi is not None and prev_vwap is not None and _short_ok(bars, i - 1, prev_rsi, prev_vwap):
            short = False
        if not long and not short:
            continue
        side = "long" if long else "short"
        close = float(bars[i]["close"])
        swing = last_swing(bars, i, kind="low" if side == "long" else "high")
        atr = atrs[i]
        stop = None
        if swing is not None and atr is not None:
            stop = swing - 0.25 * atr if side == "long" else swing + 0.25 * atr
        hits.append(
            Hit(
                setup="rsi_extreme",
                side=side,  # type: ignore[arg-type]
                time=int(bars[i]["time"]),
                close=close,
                stop=stop,
                atr=atr,
                swing=swing,
                resistance=nearest_level(bars, i, close, side="long"),
                support=nearest_level(bars, i, close, side="short"),
            )
        )
    return hits
