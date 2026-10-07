"""Stopping the backend (Ctrl+C, SIGTERM, or a forced second Ctrl+C) still writes the day's files.

7 Oct: the backend went down at 15:32 with no spread report. Uvicorn runs the app's shutdown only on a graceful
exit; a second Ctrl+C (or `uv run` forwarding the first) forces the exit and skips it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from app.data.store import CandleStore
from app.live.model import IST
from app.live.service import LiveConfig, LiveService

DAY = date(2026, 10, 7)


def _ms(h: int, m: int) -> int:
    return int(datetime(2026, 10, 7, h, m, tzinfo=IST).timestamp() * 1000)


class FakeSpreads:
    def __init__(self) -> None:
        self.closes = 0

    def close(self, bars: list) -> None:
        self.closes += 1


class FakePaper:
    def __init__(self) -> None:
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def service(tmp_path: Path, now: int) -> LiveService:
    cfg = LiveConfig(candles_dir=tmp_path / "candles", recordings_dir=tmp_path / "rec", state_dir=tmp_path / "state",
                     instruments_dir=tmp_path / "inst", spread_recorder_enabled=True, spreads_dir=tmp_path / "spreads")
    svc = LiveService(cfg, CandleStore(tmp_path / "candles"), client_factory=lambda: None,
                      authorize=lambda: "wss://example.invalid", now_ms=lambda: now)
    svc._spreads = FakeSpreads()  # type: ignore[assignment]
    svc.paper = FakePaper()  # type: ignore[assignment]
    return svc


def test_closing_writes_the_spread_report_and_flushes_the_paper_day_once(tmp_path: Path) -> None:
    svc = service(tmp_path, _ms(16, 0))
    svc.close_day_files()
    svc.close_day_files()  # the lifespan stop and the exit hook both call it
    assert svc._spreads.closes == 1 and svc.paper.flushes == 1  # type: ignore[union-attr]


def test_stopping_before_1545_on_a_feed_day_warns_that_the_1545_jobs_will_not_run(tmp_path: Path, caplog) -> None:
    svc = service(tmp_path, _ms(15, 32))
    svc.engine.day = DAY
    with caplog.at_level(logging.WARNING, logger="tradeboss.live"):
        svc.close_day_files()
    assert any("15:45" in r.getMessage() and "will not run" in r.getMessage() for r in caplog.records)


def test_no_warning_after_the_reconcile_or_on_a_day_without_a_feed(tmp_path: Path, caplog) -> None:
    late = service(tmp_path / "a", _ms(16, 5))
    late.engine.day = DAY
    idle = service(tmp_path / "b", _ms(11, 0))  # engine.day is None: the feed never ran today
    with caplog.at_level(logging.WARNING, logger="tradeboss.live"):
        late.close_day_files()
        idle.close_day_files()
    assert not any("will not run" in r.getMessage() for r in caplog.records)
