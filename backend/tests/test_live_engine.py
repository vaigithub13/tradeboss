"""Frame-level rules: trading day source (case 7), F6 hold, session-end evidence, cold start (case 6),
reconnect gaps and withheld publication."""

from __future__ import annotations

import time
from datetime import date, datetime

import pytest

from app.live.engine import EngineConfig, LiveEngine
from app.live.frames import FeedItem, encode_feed, encode_market_info
from app.live.model import IST, Bar, I1Bar, minute_of

DAY = date(2026, 10, 5)  # Monday
SAT = date(2026, 10, 3)
EQ = "NSE_EQ|INE002A01018"
NIFTY = "NSE_INDEX|Nifty 50"
OPT = "NSE_FO|99999"


def T(h: int, m: int, s: int = 0, ms: int = 0, day: date = DAY) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000 + ms


def M(h: int, m: int, day: date = DAY) -> int:
    return minute_of(T(h, m, day=day))


def eq(ltt: int, ltp: float, vtt: int = 1000, ltq: int = 1, i1: I1Bar | None = None, key: str = EQ) -> FeedItem:
    return FeedItem(key, ltp, ltt, ltq, vtt, None, i1, True)


def idx(ltt: int, ltp: float, i1: I1Bar | None = None) -> FeedItem:
    return FeedItem(NIFTY, ltp, ltt, 0, None, None, i1, False)


OPEN_SEGS = {"NSE_EQ": "NORMAL_OPEN", "NSE_INDEX": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN"}
CLOSED_SEGS = {"NSE_EQ": "NORMAL_CLOSE", "NSE_INDEX": "NORMAL_CLOSE", "NSE_FO": "NORMAL_CLOSE"}


class Feeder:
    def __init__(self, eng: LiveEngine | None = None) -> None:
        self.eng = eng or LiveEngine()
        self.n = 0

    def send(self, raw: bytes, wall: int | None = None) -> None:
        self.n += 1
        self.eng.on_frame(raw, wall, self.n)

    def info(self, current_ts: int, segs: dict[str, str] | None = None) -> None:
        self.send(encode_market_info(segs or OPEN_SEGS, current_ts))

    def snap(self, current_ts: int, *items: FeedItem) -> None:
        self.send(encode_feed(list(items), current_ts, snapshot=True))

    def live(self, current_ts: int, *items: FeedItem) -> None:
        self.send(encode_feed(list(items), current_ts))

    def warm(self, *keys: str) -> None:
        """A normal morning: connect at 09:05 and get the (stale) snapshot, so the builders exist
        before the open."""
        fri = date(2026, 10, 2)
        self.info(T(9, 5), {"NSE_EQ": "PRE_OPEN_START", "NSE_INDEX": "PRE_OPEN_START", "NSE_FO": "PRE_OPEN_START"})
        self.snap(T(9, 5), *[eq(T(15, 29, day=fri), 100.0, key=k) for k in keys])


def bar_of(eng: LiveEngine, key: str, minute: int) -> Bar | None:
    return next((b for b in eng.bars(key, include_withheld=True) if b.minute == minute), None)


# ---------------------------------------------------------------- case 7: where the trading day comes from
def test_the_trading_day_comes_from_current_ts_not_from_the_local_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("the engine read the local clock")

    f = Feeder()
    with monkeypatch.context() as mp:
        for name in ("time", "monotonic", "time_ns"):
            mp.setattr(time, name, boom)
        f.info(T(9, 5), CLOSED_SEGS)
        f.snap(T(9, 5), eq(T(15, 29, day=date(2026, 10, 2)), 100.0))
    assert f.eng.day == DAY


def test_a_stale_snapshot_never_changes_the_trading_day_and_makes_no_bars() -> None:
    f = Feeder()
    f.info(T(10, 0, day=SAT), CLOSED_SEGS)
    f.snap(T(10, 0, day=SAT), eq(T(15, 29, 53, day=date(2026, 10, 2)), 1167.7), idx(T(15, 30, day=date(2026, 10, 2)), 22421.95))
    assert f.eng.day == SAT
    assert f.eng.bars(EQ) == [] and f.eng.take_backfill_requests() == []
    assert f.eng.builders[EQ].counters["not_trading_day"] + f.eng.builders[EQ].counters["stale_snapshot"] == 1


def test_a_tick_dated_in_the_future_day_does_not_move_the_day() -> None:
    f = Feeder()
    f.info(T(10, 0))
    f.live(T(10, 0, 5), eq(T(10, 0, 4, day=date(2026, 10, 6)), 100.0))
    assert f.eng.day == DAY and f.eng.counters["held"] == 1


def test_the_day_only_moves_forward_with_current_ts() -> None:
    f = Feeder()
    f.info(T(10, 0))
    f.live(T(10, 0, 1), eq(T(10, 0, 0), 100.0))
    f.info(T(9, 0, day=date(2026, 10, 6)), CLOSED_SEGS)
    assert f.eng.day == date(2026, 10, 6)
    f.live(T(10, 0, 2), eq(T(10, 0, 1), 101.0))  # an old frame cannot roll it back
    assert f.eng.day == date(2026, 10, 6)


def test_rolling_the_day_ends_the_old_sessions_and_starts_fresh_builders() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(9, 15, 2), eq(T(9, 15, 1), 100.0))
    assert f.eng.builders[EQ].day == DAY
    f.info(T(8, 55, day=date(2026, 10, 6)), CLOSED_SEGS)
    assert f.eng.builders == {} and f.eng.day == date(2026, 10, 6)
    assert (DAY, EQ) in f.eng.take_finished()


def test_latency_is_logged_from_current_ts_but_never_changes_a_bar() -> None:
    a, b = Feeder(), Feeder()
    for fd, lat in ((a, 50), (b, 4000)):
        fd.info(T(9, 14))
        fd.send(encode_feed([eq(T(9, 15, 1), 100.0)], T(9, 15, 2)), T(9, 15, 2) + lat)
    assert a.eng.bars(EQ) == b.eng.bars(EQ) or a.eng.bars(EQ, include_withheld=True) == b.eng.bars(EQ, include_withheld=True)
    assert a.eng.latency.summary()["mean_ms"] == 50 and b.eng.latency.summary()["max_ms"] == 4000


# ---------------------------------------------------------------- F6: future-dated ticks
def test_f6_a_tick_more_than_5s_ahead_of_current_ts_is_held_not_used() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(9, 15, 1), eq(T(9, 15, 0), 100.0))
    f.live(T(9, 15, 30), eq(T(9, 17, 0), 999.0))  # ltt is 90 s in the future
    assert f.eng.counters["held"] == 1
    assert [b.close for b in f.eng.bars(EQ, include_withheld=True)] == [100.0]
    assert not any(b.minute == M(9, 17) for b in f.eng.bars(EQ, include_withheld=True))


def test_f6_exactly_5s_ahead_is_not_held() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(9, 15, 10), eq(T(9, 15, 15), 100.0))
    assert f.eng.counters["held"] == 0 and bar_of(f.eng, EQ, M(9, 15)) is not None


def test_f6_a_held_tick_is_released_when_a_later_current_ts_catches_up() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(9, 15, 1), eq(T(9, 15, 0), 100.0))
    f.live(T(9, 15, 30), eq(T(9, 16, 10), 105.0, vtt=1010))  # 40 s ahead
    f.live(T(9, 16, 6), eq(T(9, 15, 59), 101.0, vtt=1005))  # currentTs - ahead is now 4 s: released first
    assert f.eng.counters["released"] == 1
    b16 = bar_of(f.eng, EQ, M(9, 16))
    assert b16 is not None and b16.close == 105.0


def test_f6_a_held_tick_that_never_catches_up_is_dropped_and_logged() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(9, 15, 1), eq(T(9, 15, 0), 100.0))
    f.live(T(9, 15, 30), eq(T(13, 0, 0), 999.0))  # hours ahead
    for s in range(0, 200, 20):
        f.live(T(9, 16, 0) + s * 1000, eq(T(9, 16, 0) + s * 1000 - 1000, 100.0 + s / 100, vtt=1100 + s))
    assert f.eng.counters["held_dropped"] == 1 and f.eng.held == []
    rec = f.eng.held_dropped[0]
    assert rec["key"] == EQ and rec["ltt"] == T(13, 0, 0) and rec["reason"] == "never_caught_up"
    assert all(b.high < 999 for b in f.eng.bars(EQ, include_withheld=True))


def test_f6_an_illiquid_option_with_a_real_25_minute_gap_is_not_held() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(9, 15, 1), eq(T(9, 15, 0), 50.0, key=OPT, vtt=100))
    f.live(T(9, 40, 6), eq(T(9, 40, 5), 55.0, key=OPT, vtt=130, ltq=30))  # first trade in 25 minutes
    assert f.eng.counters["held"] == 0
    bars = {b.minute: b for b in f.eng.bars(OPT, include_withheld=True)}
    assert bars[M(9, 40)].close == 55.0 and bars[M(9, 40)].source == "tick"
    assert all(bars[m].source == "filled" and bars[m].close == 50.0 for m in range(M(9, 16), M(9, 40)))  # 24 flat bars


# ---------------------------------------------------------------- session end evidence
def test_session_end_by_a_post_close_tick() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(15, 20, 1), eq(T(15, 20, 0), 100.0))
    f.live(T(15, 31, 1), eq(T(15, 30, 5), 100.0))
    assert f.eng.builders[EQ].ended and (DAY, EQ) in f.eng.take_finished()
    assert bar_of(f.eng, EQ, M(15, 29)).source == "filled"  # type: ignore[union-attr]


def test_session_end_by_a_closed_market_status_after_it_was_seen_open() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(15, 20, 1), eq(T(15, 20, 0), 100.0))
    f.info(T(15, 30, 2), CLOSED_SEGS)
    assert f.eng.builders[EQ].ended


def test_a_closed_status_without_having_seen_the_segment_open_is_not_evidence() -> None:
    f = Feeder()
    f.info(T(8, 56), CLOSED_SEGS)
    f.live(T(8, 57), eq(T(15, 29, day=date(2026, 10, 2)), 100.0))
    f.info(T(9, 0, 1), CLOSED_SEGS)
    assert not f.eng.builders[EQ].ended


def test_session_end_by_current_ts_30s_after_the_close() -> None:
    f = Feeder()
    f.info(T(9, 14))
    f.live(T(15, 20, 1), eq(T(15, 20, 0), 100.0))
    f.live(T(15, 30, 20), eq(T(15, 20, 0), 100.0))  # same tick again, only the server clock moved
    assert not f.eng.builders[EQ].ended
    f.live(T(15, 30, 31), eq(T(15, 20, 0), 100.0))
    assert f.eng.builders[EQ].ended


def test_no_session_end_and_no_backfill_on_a_non_trading_day() -> None:
    f = Feeder()
    f.info(T(9, 0, day=SAT), CLOSED_SEGS)
    f.snap(T(9, 0, day=SAT), eq(T(15, 29, day=date(2026, 10, 2)), 100.0))
    f.live(T(15, 40, day=SAT), eq(T(15, 29, day=date(2026, 10, 2)), 100.0))
    assert not f.eng.builders[EQ].ended and f.eng.take_backfill_requests() == [] and f.eng.take_finished() == []


# ---------------------------------------------------------------- case 6: cold start mid-session
def test_cold_start_at_11_asks_for_the_backfill_from_the_open_and_withholds_live_bars() -> None:
    f = Feeder()
    f.info(T(11, 0, 1))
    f.snap(T(11, 0, 1), eq(T(10, 59, 58), 100.0, vtt=50_000, i1=I1Bar(T(10, 59), 99, 101, 98, 100, 700)))
    f.live(T(11, 0, 4), eq(T(11, 0, 3), 101.0, vtt=50_040))
    reqs = f.eng.take_backfill_requests()
    assert len(reqs) == 1 and reqs[0].key == EQ and reqs[0].first_minute == M(9, 15) and reqs[0].last_minute == M(10, 58)  # 10:59 is the first tick's minute
    assert not f.eng.publishable(EQ) and f.eng.bars(EQ) == [] and f.eng.take_events() == []


def test_cold_start_backfill_is_applied_with_the_same_rules_as_a_reconnect_gap() -> None:
    f = Feeder()
    f.info(T(11, 0, 1))
    f.snap(T(11, 0, 1), eq(T(10, 59, 58), 100.0, vtt=50_000))
    f.live(T(11, 0, 4), eq(T(11, 0, 3), 101.0, vtt=50_040))
    f.eng.take_backfill_requests()
    hist = [Bar(m, 100, 101, 99, 100, 10.0, None, "backfill") for m in range(M(9, 15), M(10, 59))]
    f.eng.apply_backfill(EQ, hist)
    assert f.eng.publishable(EQ) and f.eng.outstanding_backfills() == []
    bars = f.eng.bars(EQ)
    assert len(bars) == 106 and bars[0].minute == M(9, 15) and bars[0].source == "backfill"
    assert bars[-2].minute == M(10, 59) and bars[-2].partial and bars[-2].volume is None  # joined mid-minute
    assert bars[-1].source == "tick" and bars[-1].volume == 40  # 11:00 is complete: baseline is 10:59's last vtt
    assert f.eng.take_events()  # now live bars flow


def test_cold_start_backfill_never_overwrites_an_i1_final_bar() -> None:
    f = Feeder()
    f.info(T(11, 0, 1))
    f.snap(T(11, 0, 1), eq(T(10, 59, 58), 100.0, vtt=50_000))
    f.live(T(11, 0, 4), eq(T(11, 0, 3), 101.0, vtt=50_040, i1=I1Bar(T(11, 0), 101, 101, 101, 101, 5)))
    f.live(T(11, 1, 4), eq(T(11, 1, 3), 102.0, vtt=50_080, i1=I1Bar(T(11, 1), 102, 102, 102, 102, 3)))  # 11:00 final
    assert bar_of(f.eng, EQ, M(11, 0)).source == "i1"  # type: ignore[union-attr]
    f.eng.apply_backfill(EQ, [Bar(M(11, 0), 1, 1, 1, 1, 1.0, None, "backfill")])
    assert bar_of(f.eng, EQ, M(11, 0)).open == 101  # type: ignore[union-attr]


def test_an_unfinished_backfill_can_be_released_after_a_timeout_and_is_retried() -> None:
    f = Feeder()
    f.info(T(11, 0, 1))
    f.live(T(11, 0, 4), eq(T(11, 0, 3), 101.0, vtt=50_040))
    f.eng.take_backfill_requests()
    f.eng.release(EQ)
    assert f.eng.publishable(EQ) and bar_of(f.eng, EQ, M(11, 0)) is not None
    assert [(r.key, r.first_minute) for r in f.eng.outstanding_backfills()] == [(EQ, M(9, 15))]


def test_a_connection_before_the_open_has_first_of_day_semantics_and_no_backfill() -> None:
    f = Feeder()
    f.info(T(9, 5), {"NSE_EQ": "PRE_OPEN_START"})
    f.snap(T(9, 5), eq(T(15, 29, day=date(2026, 10, 2)), 100.0, vtt=9999))
    f.live(T(9, 15, 4), eq(T(9, 15, 3), 100.0, vtt=1000, ltq=50))
    f.live(T(9, 15, 40), eq(T(9, 15, 39), 101.0, vtt=1200, ltq=10))
    assert f.eng.take_backfill_requests() == [] and f.eng.publishable(EQ)
    assert bar_of(f.eng, EQ, M(9, 15)).volume == 1200  # type: ignore[union-attr]  # pre_open_inclusive baseline


def test_the_open_volume_baseline_is_a_setting() -> None:
    f = Feeder(LiveEngine(EngineConfig(open_volume_baseline="first_tick")))
    f.warm(EQ)
    f.live(T(9, 15, 4), eq(T(9, 15, 3), 100.0, vtt=1000, ltq=50))
    f.live(T(9, 15, 40), eq(T(9, 15, 39), 101.0, vtt=1200, ltq=10))
    assert bar_of(f.eng, EQ, M(9, 15)).volume == 250  # type: ignore[union-attr]


# ---------------------------------------------------------------- reconnects
def test_reconnect_inside_the_session_requests_the_missing_minutes() -> None:
    f = Feeder()
    f.warm(EQ)
    f.live(T(10, 30, 6), eq(T(10, 30, 5), 100.0, vtt=5000))
    assert f.eng.take_backfill_requests() == []
    f.eng.on_disconnect()
    f.info(T(10, 34, 1))
    f.live(T(10, 34, 3), eq(T(10, 34, 2), 105.0, vtt=5500))
    reqs = f.eng.take_backfill_requests()
    assert [(r.first_minute, r.last_minute) for r in reqs] == [(M(10, 30), M(10, 33))]
    assert not f.eng.publishable(EQ)
    f.eng.apply_backfill(EQ, [Bar(m, 100, 101, 99, 100, 5.0, None, "backfill") for m in range(M(10, 30), M(10, 34))])
    assert f.eng.publishable(EQ)
    assert [b.source for b in f.eng.bars(EQ)[-5:]] == ["backfill"] * 4 + ["tick"]


def test_reconnect_before_the_open_is_not_a_gap() -> None:
    f = Feeder()
    f.info(T(8, 56), CLOSED_SEGS)
    f.eng.on_disconnect()
    f.info(T(9, 6), {"NSE_EQ": "PRE_OPEN_START"})
    f.live(T(9, 15, 4), eq(T(9, 15, 3), 100.0, vtt=1000))
    assert f.eng.take_backfill_requests() == []


def test_reconnect_inside_the_same_minute_needs_no_backfill() -> None:
    f = Feeder()
    f.warm(EQ)
    f.live(T(10, 30, 6), eq(T(10, 30, 5), 100.0, vtt=5000, ltq=1))
    f.eng.on_disconnect()
    f.live(T(10, 30, 20), eq(T(10, 30, 19), 101.0, vtt=5030))
    assert f.eng.take_backfill_requests() == []
    assert f.eng.publishable(EQ)


# ---------------------------------------------------------------- I1 evidence + determinism
def test_i1_timing_evidence_reaches_the_daily_summary_for_monday() -> None:
    f = Feeder()
    f.info(T(9, 14))
    # I1 is the FORMING bar: the 09:15 entry is first seen while the trade time is inside 09:15
    f.live(T(9, 15, 30), eq(T(9, 15, 29), 100.0, vtt=100, i1=I1Bar(T(9, 15), 100, 100, 100, 100, 10)))
    f.live(T(9, 16, 30), eq(T(9, 16, 29), 101.0, vtt=120, i1=I1Bar(T(9, 16), 101, 101, 101, 101, 20)))
    s = f.eng.daily_summary()
    assert s["i1_timing"] == {"i1_is_forming_bar": 2}
    rec = next(r for r in f.eng.minute_records() if r["minute"] == M(9, 15))
    assert rec["tick"] and rec["i1"] and rec["tick_vs_i1"] is not None


def test_replaying_the_same_frames_batched_or_unbatched_gives_identical_bars() -> None:
    frames: list[bytes] = [encode_market_info(OPEN_SEGS, T(9, 14))]
    px = 100.0
    for s in range(0, 600, 7):
        t = T(9, 15) + s * 1000
        px += (-1) ** s * 0.05
        frames.append(encode_feed([eq(t - 500, round(px, 2), vtt=1000 + s * 3, ltq=3), idx(t - 400, 22000 + s)], t))
    out = []
    for lat in (0, 123, 9999):
        e = LiveEngine()
        for i, fr in enumerate(frames):
            e.on_frame(fr, T(9, 15) + i * lat, i)
        out.append((e.bars(EQ, include_withheld=True), e.bars(NIFTY, include_withheld=True)))
    assert out[0] == out[1] == out[2] and out[0][0]
