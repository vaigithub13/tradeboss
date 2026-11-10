"""The session gate: a session opens only on a day our calendar lists (a trading day or a listed special session).
market_info alone never opens one; real in-session trades dated that day do, with an "unlisted session detected"
warning. Also the rate limit on the "backfill still missing" warning."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pytest

from app.data.store import CandleStore
from app.live.engine import EngineConfig, LiveEngine
from app.live.frames import FeedItem, encode_feed, encode_market_info
from app.live.model import IST
from app.live.recorder import BINARY, RECV, Recorder, recording_path
from app.live.service import LiveConfig, LiveService
from app.live.session_days import listed_session, normal_trading_day

MON = date(2026, 11, 2)
FRI = date(2026, 10, 30)
SAT = date(2026, 10, 31)  # market_info said open all day; nothing traded
SUN_MUHURAT = date(2026, 11, 8)
EQ = "NSE_EQ|INE002A01018"
OPEN = {"NSE_EQ": "NORMAL_OPEN", "NSE_INDEX": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN"}


def T(h: int, m: int, s: int = 0, day: date = SAT) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000


def eq(ltt: int, px: float = 100.0) -> FeedItem:
    return FeedItem(EQ, px, ltt, 1, 1000, None, None, True)


def info(ts: int) -> bytes:
    return encode_market_info(OPEN, ts)


def live(ts: int, ltt: int) -> bytes:
    return encode_feed([eq(ltt)], ts)


def snap(ts: int, ltt: int) -> bytes:
    return encode_feed([eq(ltt)], ts, snapshot=True)


def gated() -> LiveEngine:
    return LiveEngine(EngineConfig(listed=listed_session))


# ---------------------------------------------------------------- the calendar
def test_listed_sessions_are_trading_days_and_listed_special_sessions() -> None:
    assert listed_session(MON) and listed_session(FRI)
    assert listed_session(SUN_MUHURAT)  # Muhurat 2026 is on a Sunday
    assert not listed_session(SAT) and not listed_session(date(2026, 11, 1))
    assert not listed_session(date(2026, 11, 10))  # a weekday NSE holiday


def test_normal_trading_day_skips_muhurat_holidays_and_weekends() -> None:
    """Paper autostart uses this: backend may still record on Muhurat via listed_session."""
    assert normal_trading_day(MON) and normal_trading_day(FRI)
    assert not normal_trading_day(SUN_MUHURAT)
    assert not normal_trading_day(SAT)
    assert not normal_trading_day(date(2026, 11, 10))
    assert listed_session(SUN_MUHURAT) and not normal_trading_day(SUN_MUHURAT)


# ---------------------------------------------------------------- the engine
def test_market_info_open_on_an_unlisted_day_opens_no_session() -> None:
    """31 Oct 2026: market_info open, only Friday's stale snapshot. No session end, no backfill, nothing persisted."""
    eng = gated()
    eng.on_frame(info(T(9, 5)))
    eng.on_frame(snap(T(9, 5), T(15, 29, day=FRI)))
    eng.on_frame(info(T(12, 39)))
    eng.on_frame(live(T(15, 31), T(15, 29, day=FRI)))
    eng.on_frame(info(T(15, 40)))
    assert not eng.trading_today()
    assert not any(b.ended for b in eng.builders.values())
    assert eng.take_backfill_requests() == [] and eng.take_finished() == []
    assert eng.outstanding_backfills() == []


def test_the_same_frames_on_a_listed_day_still_open_the_session() -> None:
    eng = gated()
    day = MON
    eng.on_frame(info(T(9, 5, day=day)))
    eng.on_frame(live(T(9, 16, day=day), T(9, 15, 30, day=day)))
    eng.on_frame(live(T(15, 31, day=day), T(15, 30, 5, day=day)))
    assert eng.trading_today() and eng.builders[EQ].ended and (day, EQ) in eng.take_finished()


def test_a_listed_special_session_opens_on_market_info() -> None:
    eng = gated()
    eng.on_frame(info(T(14, 10, day=SUN_MUHURAT)))
    assert eng.trading_today()


def test_real_in_session_trades_on_an_unlisted_day_open_it_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    eng = gated()
    with caplog.at_level(logging.WARNING, logger="tradeboss.live.engine"):
        eng.on_frame(info(T(9, 5)))
        eng.on_frame(snap(T(9, 5), T(15, 29, day=FRI)))
        assert not eng.trading_today()
        eng.on_frame(live(T(9, 16), T(9, 15, 40)))
        eng.on_frame(live(T(9, 17), T(9, 16, 10)))
        assert eng.trading_today()
        eng.on_frame(live(T(15, 31), T(15, 30, 5)))
    assert eng.builders[EQ].ended and (SAT, EQ) in eng.take_finished()
    warned = [r.getMessage() for r in caplog.records if "unlisted session detected" in r.getMessage()]
    assert len(warned) == 1 and "2026-10-31" in warned[0]


def test_trades_outside_the_session_hours_on_an_unlisted_day_are_not_evidence() -> None:
    eng = gated()
    eng.on_frame(info(T(9, 0)))
    eng.on_frame(live(T(9, 10), T(9, 8)))  # pre-open
    eng.on_frame(live(T(15, 45), T(15, 40)))  # after the close
    assert not eng.trading_today() and eng.take_finished() == []


def test_without_a_calendar_the_engine_keeps_its_market_info_rule() -> None:
    """Replays and catch-up build the engine without a calendar: market_info open still counts there."""
    eng = LiveEngine()
    eng.on_frame(info(T(9, 5)))
    assert eng.trading_today()


# ---------------------------------------------------------------- the recorder
def test_the_recorder_ignores_market_info_open_on_an_unlisted_day(tmp_path: Path) -> None:
    r = Recorder(tmp_path, listed=listed_session)
    r.write(RECV, BINARY, info(T(12, 39)), T(12, 39))
    r.write(RECV, BINARY, snap(T(12, 39), T(15, 29, day=FRI)), T(12, 39))
    r.close()
    assert not recording_path(tmp_path, SAT).exists() and r.written == 0


def test_the_recorder_starts_on_real_trades_on_an_unlisted_day(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    r = Recorder(tmp_path, listed=listed_session)
    with caplog.at_level(logging.WARNING, logger="tradeboss.live.recorder"):
        r.write(RECV, BINARY, info(T(9, 10)), T(9, 10))
        assert r.write(RECV, BINARY, live(T(9, 16), T(9, 15, 40)), T(9, 16))
    r.close()
    assert recording_path(tmp_path, SAT).exists()
    assert any("unlisted session detected" in rec.getMessage() for rec in caplog.records)


def test_the_recorder_starts_on_market_info_on_a_listed_day(tmp_path: Path) -> None:
    r = Recorder(tmp_path, listed=listed_session)
    assert r.write(RECV, BINARY, info(T(9, 5, day=MON)), T(9, 5, day=MON))
    r.close()


# ---------------------------------------------------------------- the service
def make_service(tmp_path: Path) -> LiveService:
    cfg = LiveConfig(candles_dir=tmp_path / "candles", recordings_dir=tmp_path / "rec", state_dir=tmp_path / "state",
                     instruments_dir=tmp_path / "instr")
    return LiveService(cfg, CandleStore(tmp_path / "candles"), client_factory=lambda: None,
                       authorize=lambda: "wss://x.invalid/1", now_ms=lambda: T(12, 0, day=MON))


def test_the_service_engine_and_recorder_use_the_calendar(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    assert svc.engine.cfg.listed is listed_session
    svc._on_frame(info(T(12, 39)), T(12, 39))  # noqa: SLF001
    assert not svc.engine.trading_today() and not svc.recorder.active


def test_backfill_still_missing_is_warned_at_most_once_a_minute_per_instrument(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    svc = make_service(tmp_path)
    other = "NSE_FO|61471"
    with caplog.at_level(logging.WARNING, logger="tradeboss.live.service"):
        for now in (100.0, 105.0, 130.0, 159.9):
            svc._warn_backfill_missing(EQ, now - 40.0, now)  # noqa: SLF001
        svc._warn_backfill_missing(other, 70.0, 110.0)  # noqa: SLF001
        svc._warn_backfill_missing(EQ, 120.0, 160.0)  # noqa: SLF001
    msgs = [r.getMessage() for r in caplog.records if "still missing" in r.getMessage()]
    assert sum(EQ in m for m in msgs) == 2 and sum(other in m for m in msgs) == 1


def test_the_15_45_reconcile_runs_on_a_listed_day_and_not_on_an_unlisted_one(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    svc._on_frame(info(T(9, 5)), T(9, 5))  # noqa: SLF001
    assert not svc._session_today()  # noqa: SLF001  (31 Oct: market_info open, nothing traded)
    svc._on_frame(info(T(9, 5, day=MON)), T(9, 5, day=MON))  # noqa: SLF001
    assert svc._session_today()  # noqa: SLF001
