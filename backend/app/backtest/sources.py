"""Where the engine gets its source candles (always the finest stored bars, resampled by the engine)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.data.candle import Candle
from app.data.resampler import DAY_S, IST_OFFSET_S
from app.data.sessions import SESSION_TYPES
from app.data.store import CandleStore

_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


def ist_date(t: int) -> date:
    return date.fromordinal(_EPOCH_ORDINAL + (t + IST_OFFSET_S) // DAY_S)


@dataclass(frozen=True)
class Loaded:
    candles: list[Candle]
    base_minutes: int
    anchored: frozenset[date]  # sessions whose bars anchor to their own first bar (Muhurat)


class CandleSource(Protocol):
    symbol: str

    def load(self, from_time: int | None, to_time: int | None, session_types: Iterable[str]) -> Loaded: ...


class StoreSource:
    """The candle store (Parquet), exactly as the chart reads it."""

    def __init__(self, store: CandleStore, symbol: str) -> None:
        self.store, self.symbol = store, symbol

    def load(self, from_time: int | None, to_time: int | None, session_types: Iterable[str]) -> Loaded:
        types = list(session_types)
        candles, base = self.store.load(self.symbol, from_time=from_time, to_time=to_time, session_types=types)
        anchored = frozenset(self.store.dates_of_type(self.symbol, "muhurat")) if "muhurat" in types else frozenset()
        return Loaded(candles, base, anchored)


class ListSource:
    """In-memory candles (tests). `labels` maps an IST date to its session type; unlabeled dates are 'normal'."""

    def __init__(self, candles: list[Candle], base_minutes: int = 1, labels: dict[date, str] | None = None,
                 symbol: str = "TEST") -> None:
        self.candles = sorted(candles, key=lambda c: c["time"])
        self.base_minutes = base_minutes
        self.labels = dict(labels or {})
        self.symbol = symbol
        bad = sorted(set(self.labels.values()) - set(SESSION_TYPES))
        if bad:
            raise ValueError(f"unknown session type(s) {bad}")

    def load(self, from_time: int | None, to_time: int | None, session_types: Iterable[str]) -> Loaded:
        types = set(session_types)
        out: list[Candle] = []
        for c in self.candles:
            if from_time is not None and c["time"] < from_time:
                continue
            if to_time is not None and c["time"] > to_time:
                continue
            if self.labels and self.labels.get(ist_date(c["time"]), "normal") not in types:
                continue
            if not self.labels and "normal" not in types and "weekend_full" not in types:
                continue
            out.append(c)
        anchored = frozenset(d for d, label in self.labels.items() if label == "muhurat" and "muhurat" in types)
        return Loaded(out, self.base_minutes, anchored)
