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


def test_a_missing_minute_holds_the_bar_and_it_is_given_up_only_after_the_gap_wait() -> None:
    builder = ClosedBars(bar_minutes=5)
    bars = five_minutes_0915()
    bars.pop(2)  # 09:17 never reached I1 final
    out: list[dict] = []
    for bar in bars:
        out += builder.on_minute(bar)
    assert out == [] and builder.incomplete == set() and builder.waiting == ist(9, 15)
    for m in (20, 21, 22):  # GAP_WAIT_S = 180: the bar ended 09:20, the 09:22 minute ends 09:23
        out += builder.on_minute(minute(9, m, 100, 100, 100, 100))
    assert out == [] and builder.incomplete == {ist(9, 15)} and builder.waiting is None


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


# ---------------------------------------------------------------- feed gaps: backfilled minutes rebuild the bar
def flat_minutes(h: int, m0: int, n: int, price: float = 100.0, source: str = "i1") -> list[dict]:
    return [minute(h, m0 + i, price, price, price, price, 1, source) for i in range(n)]


def test_a_backfilled_minute_in_order_completes_the_bar_on_time_flagged_after_gap() -> None:
    builder = ClosedBars(bar_minutes=5)
    out: list[dict] = []
    for bar in five_minutes_0915()[:2] + [{**five_minutes_0915()[2], "source": "backfill"}] + five_minutes_0915()[3:]:
        out += builder.on_minute(bar)
    assert [b["time"] for b in out] == [ist(9, 15)] and out[0]["late_after_gap"] is True
    assert builder.late == {ist(9, 15)} and builder.incomplete == set()


def test_a_backfill_after_later_minutes_rebuilds_the_bar_late() -> None:
    builder = ClosedBars(bar_minutes=5)
    bars = five_minutes_0915()
    gap = bars.pop(2)
    out: list[dict] = []
    for bar in bars + flat_minutes(9, 20, 2):  # the next bar has started; 09:17 is still missing
        out += builder.on_minute(bar)
    assert out == [] and builder.waiting == ist(9, 15)
    out = builder.on_minute({**gap, "source": "backfill"})
    assert out == [{"time": ist(9, 15), "open": 100, "high": 107, "low": 97, "close": 100, "volume": 35,
                    "late_after_gap": True}]
    assert builder.incomplete == set() and builder.late == {ist(9, 15)}
    rest: list[dict] = []
    for bar in flat_minutes(9, 22, 3):
        rest += builder.on_minute(bar)
    assert [b["time"] for b in rest] == [ist(9, 20)] and "late_after_gap" not in rest[0]


def test_bars_due_behind_a_gap_are_held_and_released_in_order_after_it() -> None:
    builder = ClosedBars(bar_minutes=1)
    out = builder.on_minute(minute(9, 15, 1, 1, 1, 1, 1))
    for bar in flat_minutes(9, 17, 2):  # 09:16 missing: 09:17 and 09:18 are complete but wait behind it
        out += builder.on_minute(bar)
    assert [b["time"] for b in out] == [ist(9, 15)]
    out = builder.on_minute(minute(9, 16, 2, 2, 2, 2, 1, "backfill"))
    assert [b["time"] for b in out] == [ist(9, 16), ist(9, 17), ist(9, 18)]
    assert all(b["late_after_gap"] for b in out)


def test_a_bar_with_no_minutes_at_all_is_waited_for_and_rebuilt() -> None:
    builder = ClosedBars(bar_minutes=5)
    out: list[dict] = []
    for bar in flat_minutes(9, 15, 5) + flat_minutes(9, 25, 2):  # 09:20-09:24 never arrived
        out += builder.on_minute(bar)
    assert [b["time"] for b in out] == [ist(9, 15)] and builder.waiting == ist(9, 20)
    for bar in flat_minutes(9, 20, 5, source="backfill"):
        out += builder.on_minute(bar)
    assert [b["time"] for b in out] == [ist(9, 15), ist(9, 20)] and out[1]["late_after_gap"] is True


def test_a_gap_never_filled_is_given_up_said_and_the_bars_behind_it_go_on() -> None:
    builder = ClosedBars(bar_minutes=5)
    out: list[dict] = []
    for bar in flat_minutes(9, 15, 5) + flat_minutes(9, 25, 5):  # 09:20 bar missing; 09:27 is 180 s past its end
        out += builder.on_minute(bar)
    assert [b["time"] for b in out] == [ist(9, 15), ist(9, 25)]
    assert builder.incomplete == {ist(9, 20)} and builder.given_up == [ist(9, 20)]
    assert "late_after_gap" not in out[1]  # 09:25 completed on time: the give-up happened at 09:27
    late = builder.on_minute(minute(9, 22, 1, 1, 1, 1, 1, "backfill"))
    assert late == [] and builder.after_give_up_ignored == 1


def test_end_of_day_gives_up_a_waiting_gap_and_the_bars_queued_behind_it() -> None:
    builder = ClosedBars(bar_minutes=5)
    for bar in flat_minutes(9, 15, 4) + flat_minutes(9, 20, 2):  # 09:19 missing; still inside the gap wait
        builder.on_minute(bar)
    assert builder.waiting == ist(9, 15)
    builder.end_of_day()
    assert builder.incomplete == {ist(9, 15), ist(9, 20)} and builder.waiting is None
