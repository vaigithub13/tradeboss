"""Plain data types shared by the live pipeline (no I/O, no clocks).

All times are exchange / server epoch MILLISECONDS unless a name ends in `_s`. A "minute" is the
epoch minute (ms // 60_000); IST is UTC+5:30, a whole number of minutes, so epoch minutes line up
with IST minutes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

IST = timezone(timedelta(hours=5, minutes=30))
IST_OFFSET_MS = 19_800_000
MS_MIN = 60_000
MS_DAY = 86_400_000

Source = Literal["filled", "tick", "i1", "backfill", "official"]
#: a bar from a higher source replaces one from a lower source; `final` = rank >= i1
SOURCE_RANK: dict[str, int] = {"filled": 0, "tick": 1, "i1": 2, "backfill": 3, "official": 4}
FINAL_RANK = SOURCE_RANK["i1"]


def minute_of(ms: int) -> int:
    return ms // MS_MIN


def ist_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, IST).date()


def ist_ms_of_day(ms: int) -> int:
    return (ms + IST_OFFSET_MS) % MS_DAY


def ist_minute_of_day(minute: int) -> int:
    return ((minute * MS_MIN + IST_OFFSET_MS) % MS_DAY) // MS_MIN


def day_open_minute(day: date, open_ms_of_day: int) -> int:
    """Epoch minute of `day` at the given IST time of day."""
    start_ms = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp()) * 1000
    return (start_ms + open_ms_of_day) // MS_MIN


def fmt_minute(minute: int) -> str:
    return datetime.fromtimestamp(minute * 60, IST).strftime("%H:%M")


@dataclass(frozen=True)
class Tick:
    ltt: int  # exchange last-trade time (ms)
    ltp: float
    ltq: int = 0
    vtt: int | None = None  # volume traded today (cumulative); None for indices
    oi: float | None = None
    snapshot: bool = False  # came in the initial_feed frame


@dataclass(frozen=True)
class I1Bar:
    """The feed's exchange 1-minute OHLC entry (marketOHLC interval "I1")."""

    ts: int  # bar start (ms)
    open: float
    high: float
    low: float
    close: float
    vol: int = 0


@dataclass(frozen=True)
class Bar:
    minute: int
    open: float
    high: float
    low: float
    close: float
    #: None = unknown (e.g. the forming bar right after a reconnect, until I1 / backfill arrives)
    volume: float | None
    oi: float | None
    source: Source
    #: some trades of this minute were missed (connection gap); a higher source fixes it
    partial: bool = False

    @property
    def time_s(self) -> int:
        return self.minute * 60

    @property
    def final(self) -> bool:
        return SOURCE_RANK[self.source] >= FINAL_RANK

    def values(self) -> tuple:
        return (self.open, self.high, self.low, self.close, self.volume, self.oi)


@dataclass(frozen=True)
class BarEvent:
    key: str
    bar: Bar


@dataclass(frozen=True)
class Diff:
    """One minute where a higher source changed what we had (logged, never silent)."""

    key: str
    minute: int
    kind: Literal["changed", "added", "removed"]
    ours_source: str | None
    theirs_source: str
    fields: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "minute": fmt_minute(self.minute),
            "minute_epoch_s": self.minute * 60,
            "kind": self.kind,
            "ours_source": self.ours_source,
            "theirs_source": self.theirs_source,
            "fields": {k: {"ours": a, "theirs": b} for k, (a, b) in self.fields.items()},
        }


PRICE_TOL = 0.005
VOLUME_TOL = 0.5


def diff_fields(old: Bar | None, new: Bar) -> dict[str, tuple[float | None, float | None]]:
    """Fields that differ between two bars (price tolerance half a paisa, volume exact)."""
    if old is None:
        return {}
    out: dict[str, tuple[float | None, float | None]] = {}
    for name in ("open", "high", "low", "close"):
        a, b = getattr(old, name), getattr(new, name)
        if abs(a - b) > PRICE_TOL:
            out[name] = (a, b)
    if old.volume is None or new.volume is None:
        if old.volume != new.volume:
            out["volume"] = (old.volume, new.volume)
    elif abs(old.volume - new.volume) > VOLUME_TOL:
        out["volume"] = (old.volume, new.volume)
    if old.oi is not None and new.oi is not None and abs(old.oi - new.oi) > VOLUME_TOL:
        out["oi"] = (old.oi, new.oi)
    return out
