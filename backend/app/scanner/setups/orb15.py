"""ORB 15: first 5-minute close through the 09:15–09:30 range, with width and volume filters."""

from __future__ import annotations

from app.scanner.bars import (
    OPEN_MIN,
    RANGE_END_MIN,
    Bar,
    closed_bars,
    day_of,
    entry_allowed,
    minute_of,
    prior_avg_volume,
    split_sessions,
)
from app.scanner.models import Hit


def _range(session: list[Bar]) -> tuple[float, float, float] | None:
    window = [b for b in session if OPEN_MIN <= minute_of(int(b["time"])) < RANGE_END_MIN]
    if len(window) < 2:
        return None
    hi = max(float(b["high"]) for b in window)
    lo = min(float(b["low"]) for b in window)
    mid = (hi + lo) / 2.0
    if mid <= 0 or hi <= lo:
        return None
    return hi, lo, (hi - lo) / mid


WIDTH_MIN = 0.003
WIDTH_MAX = 0.02
VOLUME_MULT = 1.5


def find_orb(
    bars: list[Bar],
    *,
    as_of: int | None = None,
    min_width: float = WIDTH_MIN,
    max_width: float = WIDTH_MAX,
    volume_mult: float = VOLUME_MULT,
) -> list[Hit]:
    bars = closed_bars(bars, as_of)
    hits: list[Hit] = []
    for session in split_sessions(bars):
        rng = _range(session)
        if rng is None:
            continue
        hi, lo, width = rng
        if not (min_width <= width <= max_width):
            continue
        later = [b for b in session if minute_of(int(b["time"])) >= RANGE_END_MIN]
        if not later:
            hits.append(
                Hit(
                    setup="orb15",
                    side="flat",
                    time=int(session[-1]["time"]),
                    close=float(session[-1]["close"]),
                    range_high=hi,
                    range_low=lo,
                    stop=(hi + lo) / 2.0,
                    forming=True,
                )
            )
            continue
        trigger = None
        side = None
        for bar in later:
            if float(bar["close"]) > hi:
                trigger, side = bar, "long"
                break
            if float(bar["close"]) < lo:
                trigger, side = bar, "short"
                break
        if trigger is None or side is None:
            hits.append(
                Hit(
                    setup="orb15",
                    side="flat",
                    time=int(later[-1]["time"]),
                    close=float(later[-1]["close"]),
                    range_high=hi,
                    range_low=lo,
                    stop=(hi + lo) / 2.0,
                    forming=True,
                )
            )
            continue
        if not entry_allowed(int(trigger["time"])):
            continue
        idx = next(i for i, b in enumerate(bars) if int(b["time"]) == int(trigger["time"]) and day_of(int(b["time"])) == day_of(int(trigger["time"])))
        avg = prior_avg_volume(bars, idx, 20)
        if avg is None or avg <= 0 or float(trigger["volume"]) < volume_mult * avg:
            continue
        hits.append(
            Hit(
                setup="orb15",
                side=side,  # type: ignore[arg-type]
                time=int(trigger["time"]),
                close=float(trigger["close"]),
                stop=(hi + lo) / 2.0,
                range_high=hi,
                range_low=lo,
                volume_ratio=float(trigger["volume"]) / avg,
            )
        )
    return hits
