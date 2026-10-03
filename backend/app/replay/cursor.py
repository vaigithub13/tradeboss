"""Chart bars visible at a replay cursor. Source minutes after the cursor are dropped first."""

from __future__ import annotations

from collections.abc import Iterable, Set
from datetime import date

from app.data.candle import Candle
from app.data.resampler import resample


def replay_bars(
    minutes: Iterable[Candle],
    timeframe: str,
    cursor: int,
    source_minutes: int = 1,
    *,
    anchor_to_first_bar_dates: Set[date] = frozenset(),
) -> list[Candle]:
    """Resample only source bars whose start is <= `cursor`.

    A higher-timeframe bucket may be partial. No bar is invented, and no returned
    bar starts after the cursor.
    """
    clipped = [c for c in minutes if int(c["time"]) <= cursor]
    bars = resample(
        clipped,
        timeframe,
        source_minutes,
        anchor_to_first_bar_dates=anchor_to_first_bar_dates,
    )
    return [c for c in bars if int(c["time"]) <= cursor]
