"""A listed special session uses its own window (Muhurat 8 Nov 2026: 14:20-15:30) for gaps, backfill, the session
end and the reconcile, so no 09:15-14:19 gap is created. Other days keep 09:15-15:30."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.data import sessions
from app.data.sessions import session_window
from app.live.engine import EngineConfig, LiveEngine
from app.live.frames import FeedItem, encode_feed, encode_market_info
from app.live.model import IST, Bar, Diff, minute_of
from app.live.reconcile import session_bars, split_expected
from app.live.session_days import listed_session

MUHURAT = date(2026, 11, 8)
MON = date(2026, 11, 2)
SAT = date(2026, 10, 31)
EQ = "NSE_EQ|INE002A01018"
FUT = "NSE_FO|61471"
PRE = {"NSE_EQ": "PRE_OPEN_START", "NSE_INDEX": "PRE_OPEN_START", "NSE_FO": "PRE_OPEN_START"}
OPEN = {"NSE_EQ": "NORMAL_OPEN", "NSE_INDEX": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN"}


def T(h: int, m: int, s: int = 0, day: date = MUHURAT) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000


def M(h: int, m: int, day: date = MUHURAT) -> int:
    return minute_of(T(h, m, day=day))


def eq(ltt: int, px: float = 100.0, vtt: int = 1000) -> FeedItem:
    return FeedItem(EQ, px, ltt, 1, vtt, None, None, True)


def engine() -> LiveEngine:
    return LiveEngine(EngineConfig(listed=listed_session, window=session_window))


def send_info(eng: LiveEngine, ts: int, segs: dict[str, str]) -> None:
    eng.on_frame(encode_market_info(segs, ts))


def send_live(eng: LiveEngine, ts: int, *items: FeedItem, snapshot: bool = False) -> None:
    eng.on_frame(encode_feed(list(items), ts, snapshot=snapshot))


# ---------------------------------------------------------------- the window table
def test_muhurat_2026_has_its_own_window_and_other_days_the_normal_one() -> None:
    assert session_window(MUHURAT) == (14 * 60 + 20, 15 * 60 + 30)
    assert session_window(MON) == (9 * 60 + 15, 15 * 60 + 30)
    assert session_window(SAT) == (9 * 60 + 15, 15 * 60 + 30)


# ---------------------------------------------------------------- the engine
def test_a_cold_start_inside_the_muhurat_session_backfills_from_14_20_not_09_15() -> None:
    eng = engine()
    send_info(eng, T(14, 30, 1), OPEN)
    send_live(eng, T(14, 30, 1), eq(T(14, 29, 58), vtt=50_000), snapshot=True)
    send_live(eng, T(14, 30, 4), eq(T(14, 30, 3), 101.0, vtt=50_040))
    reqs = eng.take_backfill_requests()
    assert len(reqs) == 1 and reqs[0].first_minute == M(14, 20) and reqs[0].last_minute == M(14, 28)


def test_a_connection_before_the_muhurat_open_creates_no_gap_and_no_bar_before_14_20() -> None:
    eng = engine()
    send_info(eng, T(13, 50), PRE)
    send_live(eng, T(13, 50), eq(T(15, 29, day=date(2026, 11, 6))), snapshot=True)  # Friday's stale snapshot
    send_live(eng, T(14, 10, 5), eq(T(14, 10, 1)))  # pre-open trade: not a session bar
    send_info(eng, T(14, 20), OPEN)
    send_live(eng, T(14, 20, 2), eq(T(14, 20, 1)))
    send_live(eng, T(14, 25, 2), eq(T(14, 25, 1), vtt=1100))
    assert eng.take_backfill_requests() == []
    assert eng.builders[EQ].counters["pre_open"] == 1
    bars = eng.bars(EQ, include_withheld=True)
    assert bars[0].minute == M(14, 20) and all(b.minute >= M(14, 20) for b in bars)
    assert eng.builders[EQ].gap_minutes() == []


def test_the_muhurat_session_ends_30s_after_its_own_close_and_fills_to_15_29() -> None:
    eng = engine()
    send_info(eng, T(14, 15), PRE)
    send_live(eng, T(14, 20, 2), eq(T(14, 20, 1)))
    send_live(eng, T(15, 30, 20), eq(T(14, 20, 1)))
    assert not eng.builders[EQ].ended
    send_live(eng, T(15, 30, 31), eq(T(14, 20, 1)))
    assert eng.builders[EQ].ended and (MUHURAT, EQ) in eng.take_finished()
    bars = eng.bars(EQ, include_withheld=True)
    assert bars[0].minute == M(14, 20) and bars[-1].minute == M(15, 29) and len(bars) == 70
    assert eng.take_backfill_requests() == []


def test_a_normal_day_keeps_09_15_with_the_window_table() -> None:
    eng = engine()
    send_info(eng, T(11, 0, 1, day=MON), OPEN)
    send_live(eng, T(11, 0, 1, day=MON), eq(T(10, 59, 58, day=MON), vtt=50_000), snapshot=True)
    send_live(eng, T(11, 0, 4, day=MON), eq(T(11, 0, 3, day=MON), 101.0, vtt=50_040))
    reqs = eng.take_backfill_requests()
    assert len(reqs) == 1 and reqs[0].first_minute == M(9, 15, day=MON)


def test_the_window_moves_with_the_day_roll() -> None:
    eng = engine()
    send_live(eng, T(15, 31, day=date(2026, 11, 6)), eq(T(15, 29, day=date(2026, 11, 6))))
    send_info(eng, T(14, 30, 1), OPEN)  # Sunday 8 Nov: the day rolls to Muhurat
    send_live(eng, T(14, 30, 4), eq(T(14, 30, 3), 101.0, vtt=50_040))
    reqs = [r for r in eng.take_backfill_requests() if r.day == MUHURAT]
    assert len(reqs) == 1 and reqs[0].first_minute == M(14, 20)


# ---------------------------------------------------------------- the reconcile
def bar(minute: int) -> Bar:
    return Bar(minute, 1.0, 1.0, 1.0, 1.0, 1.0, None, "official")


def added(key: str, minute: int) -> Diff:
    return Diff(key, minute, "added", None, "official")


def test_the_reconcile_uses_the_day_window_for_the_futures_post_close_and_the_pre_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evening = date(2026, 11, 9)  # a made-up evening session, 18:00-19:00
    monkeypatch.setitem(sessions.SESSION_WINDOWS, evening, (18 * 60, 19 * 60))
    diffs = [added(FUT, M(19, 0, day=evening)), added(FUT, M(19, 9, day=evening)),
             added(EQ, M(17, 55, day=evening)), added(FUT, M(18, 30, day=evening))]
    real, expected = split_expected(FUT, diffs[:2] + diffs[3:])
    assert [d.minute for d in expected] == [M(19, 0, day=evening), M(19, 9, day=evening)]
    assert [d.minute for d in real] == [M(18, 30, day=evening)]
    real, expected = split_expected(EQ, diffs[2:3])
    assert real == [] and len(expected) == 1  # an official pre-open minute is not a session bar
    kept = session_bars(FUT, [bar(M(17, 55, day=evening)), bar(M(18, 0, day=evening)), bar(M(19, 0, day=evening))])
    assert [b.minute for b in kept] == [M(18, 0, day=evening)]


def test_the_reconcile_on_a_normal_day_is_unchanged() -> None:
    real, expected = split_expected(FUT, [added(FUT, M(15, 30, day=MON)), added(FUT, M(15, 39, day=MON)),
                                          added(FUT, M(15, 40, day=MON)), added(FUT, M(9, 15, day=MON))])
    assert [d.minute for d in expected] == [M(15, 30, day=MON), M(15, 39, day=MON)]
    assert [d.minute for d in real] == [M(15, 40, day=MON), M(9, 15, day=MON)]
    kept = session_bars(FUT, [bar(M(9, 15, day=MON)), bar(M(15, 29, day=MON)), bar(M(15, 30, day=MON))])
    assert [b.minute for b in kept] == [M(9, 15, day=MON), M(15, 29, day=MON)]


def test_the_service_and_the_paper_catch_up_build_their_engines_with_the_window(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.data.store import CandleStore
    from app.live.service import LiveConfig, LiveService
    from app.paper.catchup import CatchUp

    cfg = LiveConfig(candles_dir=tmp_path / "c", recordings_dir=tmp_path / "r", state_dir=tmp_path / "s",
                     instruments_dir=tmp_path / "i")
    svc = LiveService(cfg, CandleStore(tmp_path / "c"), client_factory=lambda: None,
                      authorize=lambda: "wss://x.invalid/1", now_ms=lambda: T(12, 0, day=MON))
    assert svc.engine.cfg.window is session_window
    rec = tmp_path / "rec.bin"
    rec.write_bytes(b"")
    catch = CatchUp(rec, on_vix=None, open_volume_baseline="pre_open_inclusive", until_ms=None)
    assert catch.engine.cfg.window is session_window and catch.engine.cfg.listed is None
