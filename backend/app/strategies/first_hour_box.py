"""First Hour Box — Python port of frontend/src/indicators/firstHourBox.ts.

Outputs must match the shared JSON fixtures under tests/fixtures/first_hour_box/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Sequence

IST = timezone(timedelta(hours=5, minutes=30))
IST_S = 19_800
DAY = 86_400
OPEN_MIN = 9 * 60 + 15
READY_MIN = 10 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
LAST_STATUS_MIN = 14 * 60 + 45
STUB_MIN = 15 * 60 + 15
DEFAULT_MAX_WIDTH = 150

FhbSide = Literal["above", "below"]
FhbKind = Literal["PROBE", "BREAK", "ACCEPTED", "FAILED", "PROBE_BOTH", "INSIDE"]


@dataclass(frozen=True)
class FhbCandle:
    time: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class FhbMarker:
    time: int
    kind: FhbKind
    side: FhbSide | None
    label: str
    color: str


@dataclass
class FhbDayBox:
    day: str
    high: float
    low: float
    mid: float
    width: float
    tooWide: bool
    startTime: int
    readyTime: int
    sessionEndTime: int
    shadeEndTime: int
    forming: bool
    markers: list[FhbMarker] = field(default_factory=list)
    lastStatus: str = "INSIDE"
    lastStatusTime: int | None = None
    lastStatusLabel: str = "INSIDE"


@dataclass
class FirstHourBoxResult:
    boxes: list[FhbDayBox]
    markers: list[FhbMarker]
    nextCloseTime: int | None


FHB_COLORS = {
    "probe": "#f59e0b",
    "break": "#3b82f6",
    "acceptedAbove": "#16a34a",
    "acceptedBelow": "#dc2626",
    "failed": "#a855f7",
    "inside": "#94a3b8",
}


def ist_day_key(unix: int) -> str:
    return datetime.fromtimestamp(unix, IST).strftime("%Y-%m-%d")


def ist_minute_of_day(unix: int) -> int:
    return int(((unix + IST_S) % DAY) // 60)


def thirty_open_unix(unix: int) -> int | None:
    day = (unix + IST_S) // DAY
    minute = ist_minute_of_day(unix)
    if minute < OPEN_MIN or minute >= CLOSE_MIN:
        return None
    if minute >= STUB_MIN:
        return None
    k = (minute - OPEN_MIN) // 30
    start_min = OPEN_MIN + k * 30
    if start_min > LAST_STATUS_MIN:
        return None
    return day * DAY - IST_S + start_min * 60


def is_status_bar_minute(minute: int) -> bool:
    return READY_MIN <= minute <= LAST_STATUS_MIN and (minute - OPEN_MIN) % 30 == 0


def session_end_from_open(start_915: int) -> int:
    return start_915 + (CLOSE_MIN - OPEN_MIN) * 60


def to_thirty_minute_bars(candles: Sequence[FhbCandle]) -> list[FhbCandle]:
    buckets: dict[int, FhbCandle] = {}
    for c in candles:
        if c is None:
            continue
        open_t = thirty_open_unix(int(c.time))
        if open_t is None:
            continue
        cur = buckets.get(open_t)
        if cur is None:
            buckets[open_t] = FhbCandle(open_t, c.open, c.high, c.low, c.close)
        else:
            buckets[open_t] = FhbCandle(
                open_t,
                cur.open,
                max(cur.high, c.high),
                min(cur.low, c.low),
                c.close,
            )
    return [buckets[k] for k in sorted(buckets)]


def _arrow(side: FhbSide) -> str:
    return "↑" if side == "above" else "↓"


def _opposite(side: FhbSide) -> FhbSide:
    return "below" if side == "above" else "above"


def _marker_color(kind: FhbKind, side: FhbSide | None) -> str:
    if kind in ("PROBE", "PROBE_BOTH"):
        return FHB_COLORS["probe"]
    if kind == "FAILED":
        return FHB_COLORS["failed"]
    if kind == "BREAK":
        return FHB_COLORS["break"]
    if kind == "INSIDE":
        return FHB_COLORS["inside"]
    if side == "above":
        return FHB_COLORS["acceptedAbove"]
    if side == "below":
        return FHB_COLORS["acceptedBelow"]
    return FHB_COLORS["probe"]


def _make_marker(time: int, kind: FhbKind, side: FhbSide | None) -> FhbMarker:
    if kind == "PROBE_BOTH":
        label = "PROBE ↕"
    elif kind == "PROBE":
        label = f"PROBE {_arrow(side)}"  # type: ignore[arg-type]
    elif kind == "BREAK":
        label = f"BREAK {_arrow(side)}"  # type: ignore[arg-type]
    elif kind == "ACCEPTED":
        label = f"ACCEPTED {_arrow(side)}"  # type: ignore[arg-type]
    elif kind == "FAILED":
        label = f"FAILED {_arrow(side)}"  # type: ignore[arg-type]
    else:
        label = "INSIDE"
    return FhbMarker(time=time, kind=kind, side=side, label=label, color=_marker_color(kind, side))


def unique_ascending_markers(markers: Sequence[FhbMarker]) -> list[FhbMarker]:
    m: dict[int, FhbMarker] = {}
    for x in markers:
        if x is None or not isinstance(x.time, (int, float)):
            continue
        m[int(x.time)] = x
    return [m[t] for t in sorted(m)]


def next_thirty_close_after(as_of: int | None) -> int | None:
    if as_of is None:
        return None
    day = (as_of + IST_S) // DAY
    for pass_ in range(5):
        d = day + pass_
        minute = READY_MIN + 30
        while minute <= LAST_STATUS_MIN + 30:
            close = d * DAY - IST_S + minute * 60
            if close > as_of:
                return close
            minute += 30
    return None


def _close_side(close: float, high: float, low: float) -> str:
    if close > high:
        return "above"
    if close < low:
        return "below"
    return "inside"


def compute_first_hour_box(
    candles: Sequence[FhbCandle | dict[str, Any]],
    *,
    as_of: int | None = None,
    skip_odd_sessions: bool = True,
    max_width: float = DEFAULT_MAX_WIDTH,
    show_inside: bool = False,
) -> FirstHourBoxResult:
    """Port of computeFirstHourBox — fixture outputs must match TS exactly."""
    norm: list[FhbCandle] = []
    for c in candles:
        if isinstance(c, FhbCandle):
            norm.append(c)
        else:
            norm.append(
                FhbCandle(
                    int(c["time"]),
                    float(c["open"]),
                    float(c["high"]),
                    float(c["low"]),
                    float(c["close"]),
                )
            )

    clipped = norm if as_of is None else [c for c in norm if c.time <= as_of]
    thirties = to_thirty_minute_bars(clipped)

    by_day: dict[str, list[FhbCandle]] = {}
    for bar in thirties:
        key = ist_day_key(bar.time)
        by_day.setdefault(key, []).append(bar)

    boxes: list[FhbDayBox] = []
    for day in sorted(by_day):
        bars = by_day[day]
        candle_a = next((b for b in bars if ist_minute_of_day(b.time) == OPEN_MIN), None)
        if candle_a is None:
            continue
        if skip_odd_sessions:
            first = bars[0]
            if first is None or ist_minute_of_day(first.time) != OPEN_MIN:
                continue

        start_time = candle_a.time
        ready_time = start_time + 60 * 60
        session_end = session_end_from_open(start_time)
        now = as_of if as_of is not None else (clipped[-1].time if clipped else start_time)
        forming = now < ready_time
        candle_b = next((b for b in bars if ist_minute_of_day(b.time) == OPEN_MIN + 30), None)

        if not forming and candle_b is None:
            continue

        parts = [candle_a, candle_b] if candle_b is not None else [candle_a]
        high = max(p.high for p in parts)
        low = min(p.low for p in parts)
        if not (high > low):
            continue
        mid = (high + low) / 2.0
        width = round((high - low) * 100) / 100
        too_wide = width > max_width

        markers: list[FhbMarker] = []
        phase: dict[str, Any] = {"kind": "inside"}
        last_status: str = "FORMING" if forming else "INSIDE"
        last_status_time: int | None = None

        if not forming:
            for bar in bars:
                minute = ist_minute_of_day(bar.time)
                if not is_status_bar_minute(minute):
                    continue
                bar_close = bar.time + 30 * 60
                if as_of is not None and as_of < bar_close - 1:
                    continue

                side_close = _close_side(bar.close, high, low)
                wick_above = bar.high > high
                wick_below = bar.low < low
                emitted: FhbMarker | None = None

                if phase["kind"] == "inside":
                    if side_close == "above":
                        emitted = _make_marker(bar.time, "BREAK", "above")
                        phase = {"kind": "break", "side": "above"}
                    elif side_close == "below":
                        emitted = _make_marker(bar.time, "BREAK", "below")
                        phase = {"kind": "break", "side": "below"}
                    elif wick_above and wick_below:
                        emitted = _make_marker(bar.time, "PROBE_BOTH", None)
                    elif wick_above:
                        emitted = _make_marker(bar.time, "PROBE", "above")
                        phase = {"kind": "probe", "side": "above"}
                    elif wick_below:
                        emitted = _make_marker(bar.time, "PROBE", "below")
                        phase = {"kind": "probe", "side": "below"}
                    elif show_inside:
                        emitted = _make_marker(bar.time, "INSIDE", None)
                elif side_close == "inside":
                    emitted = _make_marker(bar.time, "FAILED", _opposite(phase["side"]))
                    phase = {"kind": "inside"}
                elif side_close == phase["side"]:
                    if phase["kind"] == "break":
                        emitted = _make_marker(bar.time, "ACCEPTED", phase["side"])
                        phase = {"kind": "accepted", "side": phase["side"]}
                    elif phase["kind"] == "probe":
                        emitted = _make_marker(bar.time, "BREAK", phase["side"])
                        phase = {"kind": "break", "side": phase["side"]}
                else:
                    emitted = _make_marker(bar.time, "BREAK", side_close)  # type: ignore[arg-type]
                    phase = {"kind": "break", "side": side_close}

                if emitted is not None:
                    markers.append(emitted)
                    last_status = emitted.kind
                    last_status_time = bar_close
                else:
                    last_status = "INSIDE"
                    last_status_time = bar_close

        uniq = unique_ascending_markers(markers)
        if last_status == "FORMING":
            label = "Box forming"
        elif uniq:
            label = uniq[-1].label
        else:
            label = "INSIDE"
        boxes.append(
            FhbDayBox(
                day=day,
                high=high,
                low=low,
                mid=mid,
                width=width,
                tooWide=too_wide,
                startTime=start_time,
                readyTime=ready_time,
                sessionEndTime=session_end,
                shadeEndTime=ready_time,
                forming=forming,
                markers=uniq,
                lastStatus=last_status,
                lastStatusTime=last_status_time,
                lastStatusLabel=label,
            )
        )

    all_markers = unique_ascending_markers([m for b in boxes for m in b.markers])
    last_t = clipped[-1].time if clipped else None
    return FirstHourBoxResult(
        boxes=boxes,
        markers=all_markers,
        nextCloseTime=next_thirty_close_after(as_of if as_of is not None else last_t),
    )
