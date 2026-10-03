"""India VIX as-of a fill minute. Never reads a bar that starts after `t`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

STALE_S = 5 * 60


@dataclass(frozen=True)
class VixQuote:
    value: float
    time: int
    age_s: int
    stale: bool


def vix_asof(bars: Sequence[dict[str, Any]], t: int, *, stale_s: int = STALE_S) -> VixQuote | None:
    """The VIX 1m bar at `t` (its open), else the last bar at or before `t`.

    A quote older than `stale_s` is still returned, with `stale=True`. Nothing at or before `t`
    returns None (the trade is then unpriced — never made up).
    """
    last: dict[str, Any] | None = None
    for b in bars:
        bt = int(b["time"])
        if bt > t:
            break
        last = b
        if bt == t:
            break
    if last is None:
        return None
    age = int(t) - int(last["time"])
    return VixQuote(float(last["open"]), int(last["time"]), age, age > stale_s)
