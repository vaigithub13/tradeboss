"""Closed 5-minute bars from exchange-final 1-minute bars (I1). A bar needs every one of its minutes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.paper.bars import ClosedBars

IST = timezone(timedelta(hours=5, minutes=30))


def ist(h: int, m: int) -> int:
    return int(datetime(2026, 10, 5, h, m, tzinfo=IST).timestamp())


def minute(h: int, m: int, o: float, hi: float, lo: float, c: float, v: float = 10, source: str = "i1") -> dict:
    return {"time": ist(h, m), "open": o, "high": hi, "low": lo, "close": c, "volume": v, "source": source}


def five_minutes_0915() -> list[dict]:
    return [
        minute(9, 15, 100, 103, 99, 101, 5),
        minute(9, 16, 101, 106, 100, 105, 6),
        minute(9, 17, 105, 107, 104, 104, 7),
        minute(9, 18, 104, 104, 98, 99, 8),
        minute(9, 19, 99, 102, 97, 100, 9),
    ]


def test_bar_is_emitted_when_its_fifth_final_minute_arrives() -> None:
    builder = ClosedBars(bar_minutes=5)
    out: list[dict] = []
    for bar in five_minutes_0915()[:4]:
        out += builder.on_minute(bar)
    assert out == []
    out = builder.on_minute(five_minutes_0915()[4])
    assert out == [{"time": ist(9, 15), "open": 100, "high": 107, "low": 97, "close": 100, "volume": 35}]


def test_tick_built_minutes_are_not_final_and_never_build_a_bar() -> None:
    builder = ClosedBars(bar_minutes=5)
    out: list[dict] = []
    for bar in five_minutes_0915():
        out += builder.on_minute({**bar, "source": "tick"})
    assert out == []
    assert builder.not_final_ignored == 5
    assert builder.incomplete == set()


def test_a_missing_minute_makes_the_bar_incomplete_and_it_is_never_emitted() -> None:
    builder = ClosedBars(bar_minutes=5)
    bars = five_minutes_0915()
    bars.pop(2)  # 09:17 never reached I1 final
    out: list[dict] = []
    for bar in bars:
        out += builder.on_minute(bar)
    assert out == []
    assert builder.incomplete == {ist(9, 15)}


def test_a_minute_repeated_by_a_later_final_copy_is_ignored() -> None:
    builder = ClosedBars(bar_minutes=5)
    for bar in five_minutes_0915()[:4]:
        builder.on_minute(bar)
    builder.on_minute(five_minutes_0915()[3])  # a repeat of 09:18
    out = builder.on_minute(five_minutes_0915()[4])
    assert len(out) == 1 and out[0]["volume"] == 35


def test_pre_open_minutes_are_ignored() -> None:
    builder = ClosedBars(bar_minutes=5)
    assert builder.on_minute(minute(9, 10, 90, 90, 90, 90, 1000)) == []
    assert builder.pre_open_ignored == 1


def test_buckets_align_to_0915_ist() -> None:
    builder = ClosedBars(bar_minutes=5)
    closed: list[dict] = []
    for h, m in [(9, 15 + i) for i in range(5)] + [(9, 20 + i) for i in range(5)]:
        closed += builder.on_minute(minute(h, m, 100, 100, 100, 100, 1))
    assert [b["time"] for b in closed] == [ist(9, 15), ist(9, 20)]


def test_end_of_day_marks_an_unfinished_bucket_incomplete() -> None:
    builder = ClosedBars(bar_minutes=5)
    for bar in five_minutes_0915()[:3]:
        builder.on_minute(bar)
    builder.end_of_day()
    assert builder.incomplete == {ist(9, 15)}
