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


def overlaps_holdout(start: date, end: date | None) -> bool:
    """True when start..end touches the holdout. A missing end runs through the last stored bar."""
    holdout_start, holdout_end = load_holdout()
    return start <= holdout_end and (end is None or end >= holdout_start)


def holdout_refusal(start: date, end: date | None) -> str:
    holdout_start, holdout_end = load_holdout()
    return (
        f"{start.isoformat()}..{end.isoformat() if end else 'the last stored bar'} overlaps the fixed holdout "
        f"{holdout_start.isoformat()}..{holdout_end.isoformat()}. A research run ends by "
        f"{(holdout_start - timedelta(days=1)).isoformat()} or starts after {holdout_end.isoformat()}; the holdout "
        "runs only through the holdout action, which counts as a peek"
    )


def refuse_holdout_overlap(start: date, end: date | None) -> None:
    """Research runs and replays never read the holdout (H2, review 2026-10-11)."""
    if overlaps_holdout(start, end):
        raise HoldoutRead(holdout_refusal(start, end))


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
