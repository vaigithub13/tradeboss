"""Dated event list used only to FLAG trades (never to change a number).

If an event date is not a trading session, the next trading session is a `reaction_day`
(e.g. Saturday 2023-05-13 → Monday 2023-05-15). Weekend dates that *were* trading
sessions (2025-02-01, 2026-02-01) flag on the day itself and do not create a Monday reaction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.backtest.expiry import load_default_calendar

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_EVENTS_FILE = DATA_DIR / "event_days.json"

# Full-length weekend sessions in our Nifty history (weekend_full). A date in this set
# is a trading session even though it is Saturday or Sunday.
WEEKEND_TRADING_SESSIONS = frozenset(
    {
        date(2024, 1, 20),
        date(2025, 2, 1),
        date(2026, 2, 1),
    }
)


@dataclass(frozen=True)
class EventDay:
    date: date
    kind: str
    name: str
    source: str = ""


def default_is_trading_session(d: date, extra: frozenset[date] = WEEKEND_TRADING_SESSIONS) -> bool:
    """Mon–Fri that is not an NSE holiday, plus known weekend trading sessions."""
    if d in extra:
        return True
    return load_default_calendar().is_trading_day(d)


def next_trading_session(
    d: date, is_trading_session: Callable[[date], bool], *, limit: int = 14
) -> date:
    nxt = d + timedelta(days=1)
    for _ in range(limit):
        if is_trading_session(nxt):
            return nxt
        nxt += timedelta(days=1)
    raise ValueError(f"no trading session within {limit} days after {d}")


class EventCalendar:
    def __init__(
        self,
        days: list[EventDay],
        *,
        is_trading_session: Callable[[date], bool] | None = None,
        extra_sessions: frozenset[date] | None = None,
    ) -> None:
        seen: set[date] = set()
        ordered: list[EventDay] = []
        for d in sorted(days, key=lambda x: x.date):
            if d.date in seen:
                raise ValueError(f"duplicate event day {d.date}")
            seen.add(d.date)
            ordered.append(d)
        self.days = ordered
        self._dates = frozenset(seen)
        extras = WEEKEND_TRADING_SESSIONS if extra_sessions is None else extra_sessions
        if is_trading_session is None:
            cal = load_default_calendar()
            pred: Callable[[date], bool] = lambda d, _e=extras, _c=cal: d in _e or _c.is_trading_day(d)
        else:
            pred = is_trading_session
        self._is_session = pred
        self._reaction: dict[date, EventDay] = {}
        for ev in self.days:
            if not pred(ev.date):
                self._reaction[next_trading_session(ev.date, pred)] = ev

    @classmethod
    def from_dict(cls, data: dict[str, Any], **kw: Any) -> EventCalendar:
        rows = []
        for raw in data.get("rows", []):
            rows.append(
                EventDay(
                    date.fromisoformat(str(raw["date"])),
                    str(raw["kind"]),
                    str(raw["name"]),
                    str(raw.get("source", "")),
                )
            )
        extras = data.get("extra_trading_sessions")
        if extras is not None and "extra_sessions" not in kw:
            kw["extra_sessions"] = frozenset(date.fromisoformat(str(x)) for x in extras)
        return cls(rows, **kw)

    @classmethod
    def load(cls, path: Path) -> EventCalendar:
        return cls.from_dict(json.loads(path.read_text()))

    def is_event(self, d: date) -> bool:
        return d in self._dates

    def is_reaction(self, d: date) -> bool:
        return d in self._reaction

    def reaction_of(self, event_date: date) -> date | None:
        """Next trading session if `event_date` was not one; else None."""
        for react, ev in self._reaction.items():
            if ev.date == event_date:
                return react
        return None

    def _spans(self, start: date, end: date, dates: frozenset[date]) -> bool:
        if end < start:
            start, end = end, start
        d = start
        while d <= end:
            if d in dates:
                return True
            d += timedelta(days=1)
        return False

    def spans(self, start: date, end: date) -> bool:
        """True if any calendar day from `start` through `end` is an event day."""
        return self._spans(start, end, self._dates)

    def spans_reaction(self, start: date, end: date) -> bool:
        return self._spans(start, end, frozenset(self._reaction))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [
                {"date": d.date.isoformat(), "kind": d.kind, "name": d.name, "source": d.source} for d in self.days
            ]
        }


def load_default_events() -> EventCalendar:
    if not DEFAULT_EVENTS_FILE.exists():
        return EventCalendar([])
    return EventCalendar.load(DEFAULT_EVENTS_FILE)
