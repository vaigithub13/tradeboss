"""Latest best bid / ask per instrument, from the live depth frames. A quote older than `max_age_ms` is no quote."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.live.spreads.decode import DepthQuote

MAX_AGE_MS = 60_000


@dataclass(frozen=True)
class Quote:
    key: str
    ts_ms: int
    bid: float | None
    ask: float | None


class QuoteBook:
    def __init__(self, max_age_ms: int = MAX_AGE_MS) -> None:
        self.max_age_ms = max_age_ms
        self._last: dict[str, Quote] = {}

    def on_depth(self, quotes: Iterable[DepthQuote]) -> None:
        for q in quotes:
            top = q.levels[0]
            self._last[q.key] = Quote(
                key=q.key,
                ts_ms=q.ts_ms,
                bid=top.bid_p if top.bid_p > 0 else None,
                ask=top.ask_p if top.ask_p > 0 else None,
            )

    def at(self, key: str, now_ms: int) -> Quote | None:
        q = self._last.get(key)
        if q is None or q.ts_ms > now_ms or now_ms - q.ts_ms > self.max_age_ms:
            return None
        return q
