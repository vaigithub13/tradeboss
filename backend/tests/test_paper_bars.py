"""Closed 5-minute bars from live 1-minute bars: a minute is closed only once a later minute has arrived."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.paper.bars import ClosedBars

IST = timezone(timedelta(hours=5, minutes=30))


def ist(h: int, m: int) -> int:
    return int(datetime(2026, 10, 5, h, m, tzinfo=IST).timestamp())


def minute(h: int, m: int, o: float, hi: float, lo: float, c: float, v: float = 10) -> dict:
    return {"time": ist(h, m), "open": o, "high": hi, "low": lo, "close": c, "volume": v}


def five_minutes_0915() -> list[dict]:
    return [
        minute(9, 15, 100, 103, 99, 101, 5),
        minute(9, 16, 101, 106, 100, 105, 6),
        minute(9, 17, 105, 107, 104, 104, 7),
        minute(9, 18, 104, 104, 98, 99, 8),
        minute(9, 19, 99, 102, 97, 100, 9),
    ]


def test_bar_is_emitted_only_after_the_next_minute_arrives() -> None:
    builder = ClosedBars(bar_minutes=5)
    out: list[dict] = []
    for bar in five_minutes_0915():
        out += builder.on_minute(bar)
    assert out == []  # 09:19 is still forming until 09:20 arrives
    closed = builder.on_minute(minute(9, 20, 100, 101, 99, 100))
    assert closed == [{
        "time": ist(9, 15), "open": 100, "high": 107, "low": 97, "close": 100, "volume": 35,
    }]


def test_forming_updates_of_the_same_minute_replace_it() -> None:
    builder = ClosedBars(bar_minutes=5)
    builder.on_minute(minute(9, 15, 100, 100, 100, 100, 1))
    builder.on_minute(minute(9, 15, 100, 103, 99, 101, 5))  # a later update of 09:15
    for bar in five_minutes_0915()[1:]:
        builder.on_minute(bar)
    closed = builder.on_minute(minute(9, 20, 100, 101, 99, 100))
    assert closed[0]["volume"] == 5 + 6 + 7 + 8 + 9
    assert closed[0]["high"] == 107


def test_a_gap_makes_the_bar_incomplete_and_it_is_not_emitted() -> None:
    builder = ClosedBars(bar_minutes=5)
    bars = five_minutes_0915()
    bars.pop(2)  # 09:17 never arrived
    out: list[dict] = []
    for bar in bars:
        out += builder.on_minute(bar)
    out += builder.on_minute(minute(9, 20, 100, 101, 99, 100))
    assert out == []
    assert builder.incomplete == {ist(9, 15)}


def test_pre_open_minutes_are_ignored() -> None:
    builder = ClosedBars(bar_minutes=5)
    builder.on_minute(minute(9, 10, 90, 90, 90, 90, 1000))  # 09:10 pre-open print
    out = builder.on_minute(minute(9, 15, 100, 100, 100, 100))
    assert out == []
    assert builder.pre_open_ignored == 1


def test_buckets_align_to_0915_ist() -> None:
    builder = ClosedBars(bar_minutes=5)
    closed: list[dict] = []
    for h, m in [(9, 15 + i) for i in range(5)] + [(9, 20 + i) for i in range(5)]:
        closed += builder.on_minute(minute(h, m, 100, 100, 100, 100, 1))
    assert [b["time"] for b in closed] == [ist(9, 15)]
    closed += builder.on_minute(minute(9, 25, 100, 100, 100, 100, 1))
    assert [b["time"] for b in closed] == [ist(9, 15), ist(9, 20)]


def test_end_of_day_closes_the_last_minute() -> None:
    builder = ClosedBars(bar_minutes=5)
    for bar in five_minutes_0915():
        builder.on_minute(bar)
    closed = builder.end_of_day()
    assert [b["time"] for b in closed] == [ist(9, 15)]
