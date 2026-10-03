"""Fixed final holdout. Dates come from data/holdout.json and do not follow the last bar."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.options.model import HISTORY_START

HOLDOUT_PATH = Path(__file__).resolve().parent / "data" / "holdout.json"


class HoldoutRead(RuntimeError):
    """A walk-forward range touched the holdout, or the forward period while it is off."""


def load_holdout() -> tuple[date, date]:
    raw = json.loads(HOLDOUT_PATH.read_text())
    return date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])


def holdout_record(peeks: int) -> dict[str, str | int]:
    start, end = load_holdout()
    return {"start": start.isoformat(), "end": end.isoformat(), "peeks": peeks}


@dataclass(frozen=True)
class Period:
    holdout: tuple[date, date]
    research_start: date
    research_end: date
    forward_end: date | None


def resolve_period(last_session: date, include_forward: bool | None = None) -> Period:
    """Research ends the day before the frozen holdout, whatever `last_session` is.

    `include_forward` defaults to the flag in the holdout file. When it is on and
    the last session is after the holdout, that date is the forward end. The
    holdout interval itself is never part of either span.
    """
    raw = json.loads(HOLDOUT_PATH.read_text())
    holdout = (date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"]))
    flag = bool(raw.get("include_forward_period", False)) if include_forward is None else include_forward
    forward = last_session if flag and last_session > holdout[1] else None
    return Period(
        holdout=holdout,
        research_start=HISTORY_START,
        research_end=holdout[0] - timedelta(days=1),
        forward_end=forward,
    )


def assert_research_range(start: date, end: date, *, include_forward: bool) -> None:
    """Walk-forward may read research dates, and the forward period only when asked.

    The holdout interval is refused either way. Dates after the holdout are refused
    unless `include_forward` is set.
    """
    if end < start:
        raise HoldoutRead("end is before start")
    holdout_start, holdout_end = load_holdout()
    if start <= holdout_end and end >= holdout_start:
        raise HoldoutRead(
            f"{start.isoformat()}..{end.isoformat()} intersects the holdout "
            f"{holdout_start.isoformat()}..{holdout_end.isoformat()}"
        )
    if end > holdout_end and not include_forward:
        raise HoldoutRead(f"{start.isoformat()}..{end.isoformat()} is in the forward period")
