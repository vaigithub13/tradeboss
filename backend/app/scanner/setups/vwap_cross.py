"""VWAP reclaim/reject: a cross, a hold on the next bar, and volume on the cross bar."""

from __future__ import annotations

from app.scanner.bars import (
    Bar,
    closed_bars,
    entry_allowed,
    last_swing,
    nearest_level,
    prior_avg_volume,
    session_vwap,
    wilder_atr,
)
from app.scanner.models import Hit


def find_vwap_crosses(bars: list[Bar], *, as_of: int | None = None) -> list[Hit]:
    bars = closed_bars(bars, as_of)
    vwaps = session_vwap(bars)
    atrs = wilder_atr(bars, 14)
    hits: list[Hit] = []
    for i in range(21, len(bars)):
        hold = i
        cross = i - 1
        prior = i - 2
        if vwaps[hold] is None or vwaps[cross] is None or vwaps[prior] is None:
            continue
        if not entry_allowed(int(bars[hold]["time"])):
            continue
        avg = prior_avg_volume(bars, cross, 20)
        if avg is None or avg <= 0 or float(bars[cross]["volume"]) < 1.2 * avg:
            continue
        prev_c, cross_c, hold_c = float(bars[prior]["close"]), float(bars[cross]["close"]), float(bars[hold]["close"])
        prev_v, cross_v, hold_v = float(vwaps[prior]), float(vwaps[cross]), float(vwaps[hold])
        side = None
        if prev_c < prev_v and cross_c > cross_v and hold_c > hold_v:
            side = "long"
        elif prev_c > prev_v and cross_c < cross_v and hold_c < hold_v:
            side = "short"
        if side is None:
            continue
        close = hold_c
        swing = last_swing(bars, hold, kind="low" if side == "long" else "high")
        atr = atrs[hold]
        stop = None
        if swing is not None and atr is not None:
            stop = swing - 0.25 * atr if side == "long" else swing + 0.25 * atr
        hits.append(
            Hit(
                setup="vwap_cross",
                side=side,  # type: ignore[arg-type]
                time=int(bars[hold]["time"]),
                close=close,
                stop=stop,
                atr=atr,
                swing=swing,
                volume_ratio=float(bars[cross]["volume"]) / avg,
                resistance=nearest_level(bars, hold, close, side="long"),
                support=nearest_level(bars, hold, close, side="short"),
            )
        )
    return hits
