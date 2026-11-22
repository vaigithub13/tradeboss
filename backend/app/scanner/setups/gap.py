"""Gap-and-go after the first 15 minutes, and gap-fill back through the open."""

from __future__ import annotations

from app.scanner.bars import (
    OPEN_MIN,
    RANGE_END_MIN,
    Bar,
    closed_bars,
    day_of,
    entry_allowed,
    last_swing,
    minute_of,
    nearest_level,
    split_sessions,
    wilder_atr,
)
from app.scanner.models import Hit


def _opening_range(session: list[Bar]) -> tuple[float, float] | None:
    window = [b for b in session if OPEN_MIN <= minute_of(int(b["time"])) < RANGE_END_MIN]
    if len(window) < 2:
        return None
    return max(float(b["high"]) for b in window), min(float(b["low"]) for b in window)


def find_gaps(bars: list[Bar], *, prev_close: float | None, as_of: int | None = None) -> list[Hit]:
    """`prev_close` applies to the last session in `bars`. Earlier sessions are context for ATR and swings."""
    if prev_close is None or prev_close <= 0:
        return []
    bars = closed_bars(bars, as_of)
    sessions = split_sessions(bars)
    if not sessions:
        return []
    session = sessions[-1]
    if not session:
        return []
    today = day_of(int(session[0]["time"]))
    # ATR/swings need the lookback, but the gap itself is only today's open vs the given previous close.
    if day_of(int(bars[-1]["time"])) != today:
        return []
    open_px = float(session[0]["open"])
    gap = (open_px - prev_close) / prev_close
    if abs(gap) <= 0.01:
        return []
    atrs = wilder_atr(bars, 14)
    rng = _opening_range(session)
    hits: list[Hit] = []
    if gap > 0.01 and rng is not None:
        hi, _lo = rng
        for bar in session:
            if minute_of(int(bar["time"])) < RANGE_END_MIN:
                continue
            if not entry_allowed(int(bar["time"])):
                break
            if float(bar["close"]) > hi:
                hits.append(_hit(bars, bar, "gap_go", "long", atrs))
                break
    if gap < -0.01 and rng is not None:
        _hi, lo = rng
        for bar in session:
            if minute_of(int(bar["time"])) < RANGE_END_MIN:
                continue
            if not entry_allowed(int(bar["time"])):
                break
            if float(bar["close"]) < lo:
                hits.append(_hit(bars, bar, "gap_go", "short", atrs))
                break
    # Fill: a close back through today's open, toward the previous close.
    for bar in session:
        if not entry_allowed(int(bar["time"])):
            break
        close = float(bar["close"])
        if gap > 0.01 and close < open_px:
            hits.append(_hit(bars, bar, "gap_fill", "short", atrs))
            break
        if gap < -0.01 and close > open_px:
            hits.append(_hit(bars, bar, "gap_fill", "long", atrs))
            break
    return hits


def _hit(bars: list[Bar], bar: Bar, setup: str, side: str, atrs: list[float | None]) -> Hit:
    idx = next(i for i, b in enumerate(bars) if int(b["time"]) == int(bar["time"]))
    close = float(bar["close"])
    atr = atrs[idx] if idx < len(atrs) else None
    swing = last_swing(bars, idx, kind="low" if side == "long" else "high")
    stop = None
    if swing is not None and atr is not None:
        stop = swing - 0.25 * atr if side == "long" else swing + 0.25 * atr
    return Hit(
        setup=setup,
        side=side,  # type: ignore[arg-type]
        time=int(bar["time"]),
        close=close,
        stop=stop,
        atr=atr,
        swing=swing,
        resistance=nearest_level(bars, idx, close, side="long"),
        support=nearest_level(bars, idx, close, side="short"),
    )
