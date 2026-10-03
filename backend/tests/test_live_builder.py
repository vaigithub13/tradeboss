"""Candle builder spec (approved cases A-J). Pure: events in, bars out, no clock, no network."""

from __future__ import annotations

import itertools
import time
from datetime import date, datetime

import pytest

from app.live.builder import BuilderConfig, CandleBuilder
from app.live.model import IST, MS_MIN, Bar, I1Bar, Tick, minute_of

DAY = date(2026, 10, 5)  # a Monday
KEY = "NSE_EQ|TEST"


def T(h: int, m: int, s: int = 0, ms: int = 0, day: date = DAY) -> int:
    """Exchange time (epoch ms) of an IST wall-clock time."""
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000 + ms


def M(h: int, m: int) -> int:
    return minute_of(T(h, m))


def tick(h: int, m: int, s: int, price: float, ltq: int = 1, vtt: int | None = None, ms: int = 0, **kw) -> Tick:
    return Tick(T(h, m, s, ms), price, ltq, vtt, **kw)


def i1(h: int, m: int, o: float, hi: float, lo: float, c: float, vol: int = 0) -> I1Bar:
    return I1Bar(T(h, m), o, hi, lo, c, vol)


def builder(**cfg) -> CandleBuilder:
    return CandleBuilder(KEY, DAY, BuilderConfig(**cfg))


def feed(b: CandleBuilder, ticks) -> CandleBuilder:
    for t in ticks:
        b.on_tick(t)
    return b


def ohlc(bar: Bar) -> tuple:
    return (bar.open, bar.high, bar.low, bar.close)


# ------------------------------------------------------------------ A. session gate
def test_a1_a2_a3_a4_the_session_window_is_half_open_in_exchange_time() -> None:
    b = builder()
    assert not b.on_tick(Tick(T(9, 14, 59, 999), 100))
    assert b.counters["pre_open"] == 1
    assert b.on_tick(Tick(T(9, 15, 0, 0), 100))
    assert b.on_tick(Tick(T(15, 29, 59, 999), 101))
    assert not b.on_tick(Tick(T(15, 30, 0, 0), 102))
    assert b.counters["post_close"] == 1
    assert [x.minute for x in b.bars() if x.source == "tick"] == [M(9, 15), M(15, 29)]


def test_a5_post_close_ticks_make_no_bar_using_the_real_probe_times() -> None:
    b = builder()
    b.on_tick(tick(15, 29, 30, 1167.7))
    for h, m, s in ((15, 39, 59), (15, 59, 53), (16, 0, 0)):  # future / RELIANCE / Nifty last ticks
        assert not b.on_tick(tick(h, m, s, 1167.7))
    assert b.counters["post_close"] == 3 and b.saw_post_close_tick
    assert max(x.minute for x in b.bars()) == M(15, 29)


def test_a6_pre_open_ticks_are_dropped() -> None:
    b = builder()
    assert not b.on_tick(tick(9, 8, 0, 99.0))
    assert not b.on_tick(tick(9, 0, 0, 98.0))
    assert b.bars() == [] and b.counters["pre_open"] == 2


def test_a7_a_tick_from_another_day_is_dropped_as_a_stale_snapshot() -> None:
    b = builder()
    friday = date(2026, 10, 2)
    assert not b.on_tick(Tick(T(15, 29, 0, 0, friday), 22421.95, snapshot=True))
    assert not b.on_tick(Tick(T(10, 0, 0, 0, friday), 22400.0))
    assert b.bars() == []
    assert b.counters["stale_snapshot"] == 1 and b.counters["not_trading_day"] == 1


def test_a8_the_builder_never_reads_a_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("the builder read a clock")

    for name in ("time", "monotonic", "perf_counter", "time_ns"):
        monkeypatch.setattr(time, name, boom)

    class Boom(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            raise AssertionError("the builder read a clock")

        @classmethod
        def utcnow(cls):  # noqa: ANN206
            raise AssertionError("the builder read a clock")

    import app.live.builder as mod
    import app.live.model as model

    monkeypatch.setattr(mod, "datetime", Boom, raising=False)
    monkeypatch.setattr(model, "datetime", Boom)
    b = builder()
    feed(b, [tick(9, 15, 1, 100, vtt=10), tick(9, 16, 1, 101, vtt=20)])
    b.on_i1(i1(9, 15, 100, 100, 100, 100, 10))
    b.on_session_end()
    assert b.bars()


def test_a8_the_builder_source_never_imports_or_calls_a_clock() -> None:
    import inspect

    import app.live.builder as mod

    src = inspect.getsource(mod)
    for needle in ("time.time", "monotonic", "perf_counter", "datetime.now", "utcnow", "import time", "import datetime"):
        assert needle not in src, needle


@pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
def test_a9_a_bad_price_is_dropped(price: float) -> None:
    b = builder()
    assert not b.on_tick(tick(9, 15, 5, price))
    assert b.counters["bad_price"] == 1 and b.bars() == []


def test_a10_the_session_window_is_a_parameter() -> None:
    special = BuilderConfig(open_ms_of_day=11 * 3_600_000 + 15 * MS_MIN, close_ms_of_day=12 * 3_600_000)
    b = CandleBuilder(KEY, DAY, special)
    assert not b.on_tick(tick(9, 15, 5, 100))
    assert b.on_tick(tick(11, 15, 0, 100))
    assert not b.on_tick(tick(12, 0, 0, 100))


# ------------------------------------------------------------------ B. forming bar
def test_b1_single_tick() -> None:
    b = feed(builder(), [tick(9, 15, 3, 100)])
    bar = b.bar(M(9, 15))
    assert bar is not None and ohlc(bar) == (100, 100, 100, 100) and bar.source == "tick"


def test_b2_ordered_ticks() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100), tick(9, 15, 2, 102), tick(9, 15, 3, 99), tick(9, 15, 4, 101)])
    bar = b.bar(M(9, 15))
    assert bar is not None and ohlc(bar) == (100, 102, 99, 101)


def test_b3_a_new_minute_opens_with_its_first_tick_not_the_previous_close() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100), tick(9, 15, 50, 101), tick(9, 16, 0, 105, ms=0)])
    assert ohlc(b.bar(M(9, 15))) == (100, 101, 100, 101)  # type: ignore[arg-type]
    assert ohlc(b.bar(M(9, 16))) == (105, 105, 105, 105)  # type: ignore[arg-type]


def test_b4_arrival_order_inside_a_minute_does_not_matter_for_ohlc() -> None:
    ticks = [tick(9, 20, 1, 100), tick(9, 20, 2, 103), tick(9, 20, 3, 98), tick(9, 20, 4, 101)]
    expected = (100, 103, 98, 101)
    for perm in itertools.permutations(ticks):
        b = feed(builder(), perm)
        bar = b.bar(M(9, 20))
        assert bar is not None and ohlc(bar) == expected, perm


def test_b4_equal_ltt_first_arrival_opens_last_arrival_closes() -> None:
    b = feed(builder(), [Tick(T(9, 20, 5), 100, 1), Tick(T(9, 20, 5), 101, 1)])
    assert ohlc(b.bar(M(9, 20)))[0] == 100 and ohlc(b.bar(M(9, 20)))[3] == 101  # type: ignore[arg-type]


def test_b5_a_duplicate_tick_changes_nothing() -> None:
    b = builder()
    t = tick(9, 15, 1, 100, ltq=5, vtt=1000)
    assert b.on_tick(t)
    before = b.bar(M(9, 15))
    assert not b.on_tick(t)
    assert b.counters["duplicate"] == 1 and b.bar(M(9, 15)) == before


def test_b6_two_different_trades_in_the_same_millisecond_both_count() -> None:
    b = feed(builder(), [Tick(T(9, 15, 1), 100, 1), Tick(T(9, 15, 1), 104, 1), Tick(T(9, 15, 1), 96, 1)])
    bar = b.bar(M(9, 15))
    assert bar is not None and ohlc(bar) == (100, 104, 96, 96)


def test_b7_a_mid_session_snapshot_tick_is_a_normal_tick() -> None:
    b = builder()
    assert b.on_tick(Tick(T(10, 40, 1), 100, 1, 500, snapshot=True))
    assert b.bar(M(10, 40)) is not None


# ------------------------------------------------------------------ C. quiet minutes
def test_c1_quiet_minutes_get_flat_filled_bars_at_the_previous_close() -> None:
    b = feed(builder(), [tick(9, 16, 5, 100), tick(9, 16, 40, 101), tick(9, 19, 5, 103)])
    for m in (M(9, 17), M(9, 18)):
        f = b.bar(m)
        assert f is not None and f.source == "filled" and ohlc(f) == (101, 101, 101, 101) and f.volume == 0
    assert b.bar(M(9, 19)).source == "tick"  # type: ignore[union-attr]


def test_c2_a_filled_bar_is_replaced_by_i1_backfill_or_official() -> None:
    b = feed(builder(), [tick(9, 16, 5, 100), tick(9, 19, 5, 103)])
    assert b.bar(M(9, 17)).source == "filled"  # type: ignore[union-attr]
    b.on_i1(i1(9, 17, 100, 101, 99.5, 100.5, 7))
    b.on_i1(i1(9, 18, 100.5, 100.5, 100.5, 100.5, 0))  # advances: 09:17 is final
    f = b.bar(M(9, 17))
    assert f is not None and f.source == "i1" and ohlc(f) == (100, 101, 99.5, 100.5) and f.volume == 7


def test_c3_nothing_is_filled_before_the_days_first_tick() -> None:
    b = feed(builder(), [tick(9, 17, 5, 100)])
    assert [x.minute for x in b.bars()] == [M(9, 17)]


def test_c4_fills_follow_evidence_only_and_the_close_fills_to_15_29() -> None:
    b = feed(builder(), [tick(15, 20, 5, 100), tick(15, 20, 30, 101)])
    assert [x.minute for x in b.bars()] == [M(15, 20)]  # no timer-driven fills
    b.on_session_end()
    assert [x.minute for x in b.bars()] == list(range(M(15, 20), M(15, 30)))
    assert all(x.source == "filled" and x.close == 101 for x in b.bars()[1:])


def test_c5_reconcile_deletes_a_filled_bar_official_does_not_have() -> None:
    b = feed(builder(), [tick(9, 16, 5, 100), tick(9, 19, 5, 103)])
    official = [Bar(M(9, 16), 100, 100, 100, 100, 5.0, None, "official"), Bar(M(9, 18), 100, 100, 100, 100, 0.0, None, "official"),
                Bar(M(9, 19), 103, 103, 103, 103, 3.0, None, "official")]
    diffs = b.apply_official(official)
    assert b.bar(M(9, 17)) is None
    assert [d.kind for d in diffs if d.minute == M(9, 17)] == ["removed"]
    assert b.bar(M(9, 18)).source == "official"  # type: ignore[union-attr]


# ------------------------------------------------------------------ D. volume
def vol_ticks() -> list[Tick]:
    return [
        tick(9, 15, 5, 100, ltq=50, vtt=1000),
        tick(9, 15, 40, 101, ltq=10, vtt=1200),
        tick(9, 16, 10, 102, ltq=10, vtt=1500),
        tick(9, 16, 50, 103, ltq=10, vtt=1700),
    ]


def test_d1_default_first_tick_baseline_excludes_pre_open_volume() -> None:
    b = feed(builder(), vol_ticks())
    assert b.bar(M(9, 15)).volume == 250  # 1200 - (1000 - 50)   # type: ignore[union-attr]
    assert b.bar(M(9, 16)).volume == 500  # 1700 - 1200   # type: ignore[union-attr]


def test_d1b_pre_open_inclusive_baseline() -> None:
    b = feed(builder(open_volume_baseline="pre_open_inclusive"), vol_ticks())
    assert b.bar(M(9, 15)).volume == 1200  # type: ignore[union-attr]
    assert b.bar(M(9, 16)).volume == 500  # type: ignore[union-attr]


def test_d1c_both_open_volume_candidates_are_available_whatever_the_setting() -> None:
    for mode in ("first_tick", "pre_open_inclusive"):
        b = feed(builder(open_volume_baseline=mode), vol_ticks())
        assert b.open_volume_candidates() == {"first_tick": 250.0, "pre_open_inclusive": 1200.0}


def test_d2_after_a_reconnect_the_first_minutes_volume_is_unknown_the_next_is_known() -> None:
    b = builder()
    b.begin_resume()  # connected mid-session
    feed(b, [tick(10, 30, 20, 100, vtt=5000, ltq=3), tick(10, 30, 50, 101, vtt=5100), tick(10, 31, 10, 102, vtt=5300)])
    assert b.bar(M(10, 30)).volume is None  # type: ignore[union-attr]
    assert b.bar(M(10, 31)).volume == 200  # 5300 - 5100   # type: ignore[union-attr]


def test_d3_a_decreasing_vtt_is_ignored_for_volume_but_the_price_counts() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100, ltq=1, vtt=1000), tick(9, 15, 5, 101, vtt=1200), tick(9, 15, 9, 105, vtt=1100)])
    bar = b.bar(M(9, 15))
    assert bar is not None and bar.high == 105 and bar.volume == 201 and b.counters["vtt_regress"] == 1


def test_d4_a_zero_vtt_after_a_positive_one_is_treated_as_missing() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100, ltq=1, vtt=1000), tick(9, 15, 5, 101, vtt=0)])
    assert b.counters["vtt_missing"] == 1 and b.bar(M(9, 15)).volume == 1  # type: ignore[union-attr]


def test_d5_indices_have_known_zero_volume() -> None:
    b = feed(builder(has_volume=False), [tick(9, 15, 1, 22000.0), tick(9, 16, 1, 22001.0)])
    b.begin_resume()
    assert [x.volume for x in b.bars()] == [0.0, 0.0]


def test_d6_volume_does_not_depend_on_arrival_order() -> None:
    ticks = vol_ticks()
    expected = (250.0, 500.0)
    for perm in itertools.permutations(ticks):
        b = feed(builder(), perm)
        assert (b.bar(M(9, 15)).volume, b.bar(M(9, 16)).volume) == expected, perm  # type: ignore[union-attr]


def test_d7_the_bar_after_a_filled_run_uses_the_last_known_vtt() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100, ltq=10, vtt=1000), tick(9, 18, 1, 101, vtt=1040)])
    assert b.bar(M(9, 16)).source == "filled" and b.bar(M(9, 16)).volume == 0  # type: ignore[union-attr]
    assert b.bar(M(9, 18)).volume == 40  # type: ignore[union-attr]


def test_d8_i1_volume_wins_over_tick_volume_and_the_difference_is_logged() -> None:
    b = feed(builder(), vol_ticks())
    b.take_diffs()
    b.on_i1(i1(9, 15, 100, 101, 100, 101, 260))
    b.on_i1(i1(9, 16, 102, 103, 102, 103, 500))  # advances -> 09:15 final
    assert b.bar(M(9, 15)).volume == 260 and b.bar(M(9, 15)).source == "i1"  # type: ignore[union-attr]
    d = [x for x in b.take_diffs() if x.minute == M(9, 15)]
    assert len(d) == 1 and d[0].fields["volume"] == (250, 260) and d[0].ours_source == "tick"


# ------------------------------------------------------------------ E. I1
def test_e1_i1_replaces_the_tick_bar_only_once_a_later_i1_appears_and_logs_the_diff() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100, vtt=10, ltq=1), tick(9, 15, 30, 102, vtt=20)])
    b.take_diffs()
    b.on_i1(i1(9, 15, 100, 102.5, 100, 102, 10))
    assert b.bar(M(9, 15)).source == "tick"  # type: ignore[union-attr]
    b.on_i1(i1(9, 16, 102, 102, 102, 102, 0))
    bar = b.bar(M(9, 15))
    assert bar is not None and bar.source == "i1" and bar.high == 102.5
    diffs = b.take_diffs()
    assert diffs[0].fields["high"] == (102, 102.5) and diffs[0].ours_source == "tick" and diffs[0].theirs_source == "i1"


def test_e2_i1_for_the_newest_minute_updates_in_place_the_last_value_is_final() -> None:
    b = builder()
    b.on_i1(i1(9, 15, 100, 100, 100, 100, 1))
    b.on_i1(i1(9, 15, 100, 101, 99.5, 100.5, 5))
    b.on_i1(i1(9, 15, 100, 102, 99.5, 101.5, 9))
    assert b.bar(M(9, 15)) is None  # not final yet
    b.on_i1(i1(9, 16, 101.5, 101.5, 101.5, 101.5, 0))
    bar = b.bar(M(9, 15))
    assert bar is not None and (bar.high, bar.close, bar.volume) == (102, 101.5, 9)


def test_e3_an_i1_equal_to_the_tick_bar_logs_no_difference() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100, ltq=5, vtt=1000), tick(9, 15, 30, 101, vtt=1020)])
    b.take_diffs()
    b.on_i1(i1(9, 15, 100, 101, 100, 101, 25))  # 1020 - (1000-5) = 25
    b.on_i1(i1(9, 16, 101, 101, 101, 101, 0))
    assert b.take_diffs() == [] and b.bar(M(9, 15)).source == "i1"  # type: ignore[union-attr]


def test_e4_an_i1_at_or_after_the_close_is_never_a_bar() -> None:
    b = builder()
    b.on_tick(tick(15, 29, 30, 22421.95))
    b.on_i1(i1(15, 30, 22421.95, 22421.95, 22421.95, 22421.95))  # real Nifty 15:30 flat bar
    b.on_i1(i1(15, 39, 22520.3, 22520.3, 22517.2, 22520.0, 25155))  # real future 15:39 bar
    assert b.counters["i1_post_close"] == 2
    assert [x.minute for x in b.bars()] == [M(15, 29)]


def test_e5_a_malformed_i1_is_ignored() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100)])
    bad = [
        I1Bar(T(9, 16, 5), 100, 101, 99, 100),  # not on a minute
        I1Bar(T(9, 16), 100, 99, 101, 100),  # high < low
        I1Bar(T(9, 16), 103, 101, 99, 100),  # open above high
        I1Bar(T(9, 16), 0, 101, 99, 100),  # price 0
    ]
    for x in bad:
        b.on_i1(x)
    assert b.counters["i1_malformed"] == 4 and b.bar(M(9, 15)).source == "tick"  # type: ignore[union-attr]


def test_e6_a_backwards_i1_is_ignored_and_a_changed_final_one_is_a_logged_revision() -> None:
    b = builder()
    b.on_i1(i1(9, 15, 100, 101, 100, 101, 5))
    b.on_i1(i1(9, 16, 101, 102, 101, 102, 5))
    b.on_i1(i1(9, 17, 102, 102, 102, 102, 0))  # 09:15 and 09:16 final
    b.on_i1(i1(9, 15, 100, 105, 100, 101, 5))  # stale replay with different values
    assert b.counters["i1_stale"] == 1 and b.counters["i1_revision"] == 1
    assert b.bar(M(9, 15)).high == 101  # type: ignore[union-attr]  # first value kept
    assert len(b.revisions) == 1


def test_e7_the_last_bar_of_the_day_is_finalised_by_session_end_real_reliance_case() -> None:
    b = feed(builder(), [tick(15, 28, 59, 1170.4, ltq=1, vtt=15_670_000), tick(15, 29, 20, 1167.7, ltq=10, vtt=16_771_221)])
    b.on_i1(i1(15, 29, 1167.7, 1167.7, 1167.7, 1167.7, 1_101_100))  # I1 stays at 15:29 forever
    assert b.bar(M(15, 29)).source == "tick"  # type: ignore[union-attr]
    for h, m, s in ((15, 31, 0), (15, 59, 53)):
        b.on_tick(tick(h, m, s, 1167.7))  # post-close ticks are dropped
    b.on_session_end()
    bar = b.bar(M(15, 29))
    assert bar is not None and bar.source == "i1" and ohlc(bar) == (1167.7,) * 4 and bar.volume == 1_101_100


def test_e8_index_i1_has_volume_zero() -> None:
    b = builder(has_volume=False)
    b.on_i1(i1(9, 15, 100, 100, 100, 100, 0))
    b.on_i1(i1(9, 16, 100, 100, 100, 100, 0))
    assert b.bar(M(9, 15)).volume == 0  # type: ignore[union-attr]


def test_e9_the_snapshots_i1_does_not_finalise_earlier_minutes() -> None:
    b = builder()
    b.begin_resume()
    b.on_i1(i1(10, 40, 100, 100, 100, 100, 3))  # snapshot after a reconnect
    assert b.bars() == []


def test_i1_timing_evidence_is_recorded_for_the_minute_log() -> None:
    b = builder()
    b.on_i1(i1(9, 15, 100, 100, 100, 100, 1), ref_ltt=T(9, 15, 30), frame=1)  # seen while ltt is in 09:15
    b.on_i1(i1(9, 16, 100, 100, 100, 100, 1), ref_ltt=T(9, 17, 2), frame=7)  # seen one minute late
    assert b.minute_record(M(9, 15))["i1"]["seen_while_ltt_minute_offset"] == 0
    assert b.minute_record(M(9, 16))["i1"]["seen_while_ltt_minute_offset"] == 1


# ------------------------------------------------------------------ F. late ticks
def test_f1_a_late_tick_revises_a_non_final_bar_by_exchange_time() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100, ltq=1, vtt=1000), tick(9, 15, 30, 101, vtt=1100), tick(9, 16, 2, 102, vtt=1300)])
    assert b.bar(M(9, 15)).close == 101  # type: ignore[union-attr]
    b.take_events()
    b.on_tick(tick(9, 15, 59, 103, ms=900, vtt=1250))  # late: ltt 09:15:59.9 arrives after 09:16:02
    bar = b.bar(M(9, 15))
    assert bar is not None and (bar.high, bar.close, bar.volume) == (103, 103, 251)  # 1250 - (1000 - 1)
    assert b.bar(M(9, 16)).volume == 50  # type: ignore[union-attr]  # baseline moved from 1100 to 1250
    assert any(e.bar.minute == M(9, 15) for e in b.take_events())


def test_f2_a_late_tick_for_an_i1_final_bar_is_dropped_and_counted() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100)])
    b.on_i1(i1(9, 15, 100, 100, 100, 100, 1))
    b.on_i1(i1(9, 16, 100, 100, 100, 100, 1))
    assert not b.on_tick(tick(9, 15, 59, 105))
    assert b.counters["late_after_final"] == 1 and b.bar(M(9, 15)).high == 100  # type: ignore[union-attr]
    assert b.minute_record(M(9, 15))["tick"]["late_after_final"] == 1


def test_f3_late_ticks_are_accepted_for_any_non_final_bar_however_old() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100), tick(9, 40, 1, 101)])
    assert b.on_tick(tick(9, 15, 30, 99))
    assert b.bar(M(9, 15)).low == 99  # type: ignore[union-attr]


def test_f5_the_newest_marker_never_moves_backwards() -> None:
    b = feed(builder(), [tick(10, 0, 1, 100)])
    b.on_tick(tick(9, 50, 1, 99))
    assert b.last_ltt == T(10, 0, 1)


# ------------------------------------------------------------------ G. gaps (builder side)
def test_g1_a_gap_is_marked_not_flat_filled_and_a_backfill_is_wanted() -> None:
    b = feed(builder(), [tick(10, 30, 5, 100, vtt=5000, ltq=1), tick(10, 30, 20, 101, vtt=5100)])
    b.begin_resume()
    b.on_tick(tick(10, 33, 5, 105, vtt=5400))
    assert b.bar(M(10, 31)) is None and b.bar(M(10, 32)) is None
    assert b.gap_minutes() == [M(10, 30), M(10, 31), M(10, 32)]
    assert b.wanted_backfill() == (M(10, 30), M(10, 32))
    assert b.bar(M(10, 30)).partial and b.bar(M(10, 30)).volume is None  # type: ignore[union-attr]
    assert b.bar(M(10, 33)).volume is None  # type: ignore[union-attr]  # baseline lost


def bf(h: int, m: int, o: float, v: float = 10.0) -> Bar:
    return Bar(M(h, m), o, o + 1, o - 1, o + 0.5, v, None, "backfill")


def test_g2_backfill_replaces_the_gap_bars_including_the_partial_minute() -> None:
    b = feed(builder(), [tick(10, 30, 5, 100, vtt=5000, ltq=1)])
    b.begin_resume()
    b.on_tick(tick(10, 33, 5, 105, vtt=5400))
    diffs = b.apply_backfill([bf(10, 30, 100), bf(10, 31, 101), bf(10, 32, 102)])
    assert b.gap_minutes() == [] and b.wanted_backfill() is None
    assert [b.bar(M(10, m)).source for m in (30, 31, 32)] == ["backfill"] * 3  # type: ignore[union-attr]
    assert any(d.minute == M(10, 30) for d in diffs)
    assert b.bar(M(10, 33)).source == "tick"  # type: ignore[union-attr]


def test_g3_a_short_backfill_leaves_the_missing_minutes_wanted() -> None:
    b = feed(builder(), [tick(10, 30, 5, 100)])
    b.begin_resume()
    b.on_tick(tick(10, 34, 5, 105))
    b.apply_backfill([bf(10, 30, 100), bf(10, 31, 101)])
    assert b.gap_minutes() == [M(10, 32), M(10, 33)] and b.wanted_backfill() == (M(10, 32), M(10, 33))


def test_g4_backfill_never_overwrites_an_i1_final_bar() -> None:
    b = feed(builder(), [tick(10, 30, 5, 100)])
    b.begin_resume()
    b.on_tick(tick(10, 33, 5, 105))
    b.on_i1(i1(10, 31, 101, 102, 100, 101.5, 40))
    b.on_i1(i1(10, 32, 101.5, 101.5, 101.5, 101.5, 0))  # 10:31 final from I1
    b.apply_backfill([bf(10, 30, 100), bf(10, 31, 200), bf(10, 32, 102)])
    assert b.bar(M(10, 31)).source == "i1" and b.bar(M(10, 31)).open == 101  # type: ignore[union-attr]
    assert b.counters["backfill_not_wanted"] == 1  # already resolved by I1


def test_g6_a_gap_across_the_close_asks_only_for_session_minutes() -> None:
    b = feed(builder(), [tick(15, 20, 5, 100)])
    b.begin_resume()
    b.on_session_end()  # we never saw another tick
    assert b.wanted_backfill() == (M(15, 20), M(15, 29))
    b.apply_backfill([Bar(M(15, 30), 1, 1, 1, 1, 1.0, None, "backfill"), bf(15, 25, 100)])
    assert b.counters["backfill_out_of_session"] == 1 and b.bar(M(15, 25)).source == "backfill"  # type: ignore[union-attr]


def test_g8_applying_the_same_backfill_twice_changes_nothing() -> None:
    b = feed(builder(), [tick(10, 30, 5, 100)])
    b.begin_resume()
    b.on_tick(tick(10, 32, 5, 105))
    batch = [bf(10, 30, 100), bf(10, 31, 101)]
    b.apply_backfill(batch)
    before = b.bars()
    b.take_events()
    b.apply_backfill(batch)
    assert b.bars() == before and b.take_events() == []


def test_f4_ticks_for_minutes_the_backfill_owns_are_dropped() -> None:
    b = feed(builder(), [tick(10, 30, 5, 100)])
    b.begin_resume()
    b.on_tick(tick(10, 33, 5, 105))
    assert not b.on_tick(tick(10, 31, 30, 101))  # a late arrival for a gap minute
    assert b.counters["before_resume"] == 1


def test_cold_start_requests_everything_from_the_open() -> None:
    b = builder()
    b.begin_resume()  # first connection of the day at 11:00
    b.on_tick(tick(11, 0, 3, 100, vtt=90_000))
    assert b.wanted_backfill() == (M(9, 15), M(10, 59))
    assert b.gap_minutes()[0] == M(9, 15)
    assert b.gap_minutes()[-1] == M(10, 59)


# ------------------------------------------------------------------ H. reconcile (builder side)
def official_bar(h: int, m: int, o: float, v: float = 5.0) -> Bar:
    return Bar(M(h, m), o, o + 1, o - 1, o + 0.5, v, None, "official")


def test_h2_h3_official_replaces_adds_and_logs_every_difference() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100, vtt=10, ltq=1), tick(9, 15, 30, 100.5, vtt=15), tick(9, 17, 1, 102, vtt=30)])
    off = [official_bar(9, 15, 100), official_bar(9, 16, 100), official_bar(9, 17, 102)]
    diffs = b.apply_official(off)
    by_minute = {d.minute: d for d in diffs}
    assert by_minute[M(9, 15)].kind == "changed" and "high" in by_minute[M(9, 15)].fields
    assert by_minute[M(9, 15)].ours_source == "tick" and by_minute[M(9, 15)].theirs_source == "official"
    assert all(x.source == "official" for x in b.bars())
    assert M(9, 16) in by_minute  # the filled bar differs from official


def test_h4_an_empty_official_list_changes_nothing() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100)])
    before = b.bars()
    assert b.apply_official([]) == [] and b.bars() == before and b.counters["official_empty"] == 1


def test_h5_reconcile_is_idempotent_and_partial_official_data_keeps_later_live_bars() -> None:
    b = feed(builder(), [tick(9, 15, 1, 100), tick(9, 16, 1, 101), tick(9, 17, 1, 102)])
    part = [official_bar(9, 15, 100), official_bar(9, 16, 101)]  # API lags: no 09:17 yet
    b.apply_official(part)
    assert b.bar(M(9, 17)).source == "tick"  # type: ignore[union-attr]  # not removed (beyond the official span)
    again = b.apply_official(part)
    assert again == []


# ------------------------------------------------------------------ J. open interest
def test_j1_futures_carry_the_oi_of_the_last_tick_by_exchange_time() -> None:
    b = builder(has_oi=True)
    feed(b, [tick(9, 15, 1, 100, oi=1000.0), tick(9, 15, 30, 101, oi=1010.0), tick(9, 15, 20, 100.5, oi=1005.0)])
    assert b.bar(M(9, 15)).oi == 1010.0  # type: ignore[union-attr]
    stock = feed(builder(has_oi=False), [tick(9, 15, 1, 100, oi=5.0)])
    assert stock.bar(M(9, 15)).oi is None  # type: ignore[union-attr]
