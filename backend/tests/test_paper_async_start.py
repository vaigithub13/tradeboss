"""A paper Start in the middle of the day does not pause the feed (M2, review 2026-10-11).

The session is built, warmed and caught up from the day's recording in a worker thread; the event loop keeps
handling frames. Then, on the loop, the frames recorded meanwhile are replayed into it and it goes live. Nothing is
lost or replayed twice: the recording cursor only moves past complete records.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.live.recorder import BINARY, MAGIC, RECV, SENT, TEXT, _REC, RecordingCursor
from app.paper.live import AlreadyRunning, PaperError, PaperRunner
from app.paper.pricing import VixSeries
from app.paper.store import load_day
from tests.test_paper_session import DAY, Scripted, choose, ist_ms, key_for, minute


def _record(direction: int, kind: int, wall_ms: int, payload: bytes) -> bytes:
    return _REC.pack(direction, kind, wall_ms, len(payload)) + payload


# ---------------------------------------------------------------- the recording cursor
def test_the_cursor_moves_only_past_complete_received_frames(tmp_path: Path) -> None:
    path = tmp_path / "day.bin"
    path.write_bytes(MAGIC + _record(RECV, BINARY, 1, b"one") + _record(SENT, TEXT, 2, b"sub")
                     + _record(RECV, BINARY, 3, b"two"))
    cursor = RecordingCursor(path)
    assert [(p, w) for p, w in cursor.frames()] == [(b"one", 1), (b"two", 3)]
    assert list(cursor.frames()) == []  # nothing new

    third = _record(RECV, BINARY, 4, b"three-payload")
    with path.open("ab") as fh:
        fh.write(third[:-5])  # a record still being written: header and part of the payload
    assert list(cursor.frames()) == []
    with path.open("ab") as fh:
        fh.write(third[-5:] + _REC.pack(RECV, BINARY, 5, 3)[:4])  # the rest, and half of the next header
    assert [(p, w) for p, w in cursor.frames()] == [(b"three-payload", 4)]
    with path.open("ab") as fh:
        fh.write(_REC.pack(RECV, BINARY, 5, 3)[4:] + b"fiv")
    assert [(p, w) for p, w in cursor.frames()] == [(b"fiv", 5)]


def test_the_vix_series_can_be_written_and_read_from_two_threads() -> None:
    vix = VixSeries()
    stop = threading.Event()
    errors: list[BaseException] = []

    def write() -> None:
        t = 0
        while not stop.is_set():
            t += 60
            vix.add(t, 12.0)

    def read() -> None:
        try:
            while not stop.is_set():
                vix.at(10**9)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write), threading.Thread(target=read)]
    for th in threads:
        th.start()
    time.sleep(0.3)
    stop.set()
    for th in threads:
        th.join()
    assert errors == []


# ---------------------------------------------------------------- the runner
class _Continuation:
    def __init__(self) -> None:
        self.resumed_on: int | None = None

    def resume(self, session: Any) -> None:
        self.resumed_on = threading.get_ident()


def _runner(tmp_path: Path, *, catch_up: Any, make: Any = None) -> PaperRunner:
    def default_make(day: date, strategy: str, params: dict) -> Any:
        from app.backtest.costs import load_default_cost_table
        from app.paper.quotes import QuoteBook
        from app.paper.session import PaperSession

        return PaperSession(day=day, strategy=Scripted({}), choose=choose, key_for=key_for, quotes=QuoteBook(),
                            cost_table=load_default_cost_table())

    return PaperRunner(tmp_path, make or default_make, catch_up=catch_up)


def test_a_start_catches_up_in_a_thread_while_the_loop_keeps_running(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    continuation = _Continuation()

    def catch_up(session: Any, day: date) -> _Continuation:
        seen["catch_up_thread"] = threading.get_ident()
        time.sleep(0.4)  # a long recording
        return continuation

    runner = _runner(tmp_path, catch_up=catch_up)

    async def scenario() -> None:
        ticks = 0
        done = asyncio.Event()

        async def feed_loop() -> None:
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                runner.on_index_bar(minute(10, 0, 22600.0), now_ms=ist_ms(10, 0))  # ignored while starting
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(feed_loop())
        start = asyncio.create_task(runner.start_async(DAY, "log_xz", {}, now_ms=ist_ms(11, 0),
                                                       clock=lambda: ist_ms(11, 0)))
        await asyncio.sleep(0.1)
        assert runner.state == "starting"
        with pytest.raises(AlreadyRunning):
            await runner.start_async(DAY, "log_xz", {}, now_ms=ist_ms(11, 0), clock=lambda: ist_ms(11, 0))
        await start
        done.set()
        await ticker
        seen["ticks"] = ticks
        seen["loop_thread"] = threading.get_ident()

    asyncio.run(scenario())
    assert seen["ticks"] >= 20  # the loop was never blocked for the 0.4 s catch-up
    assert seen["catch_up_thread"] != seen["loop_thread"]
    assert continuation.resumed_on == seen["loop_thread"]  # the frames recorded meanwhile, on the loop
    assert runner.state == "running" and runner.session is not None
    assert runner.session.shown == []  # the bars fed while starting were not decided by a half-built session
    assert load_day(tmp_path, DAY)["state"] == "running"


def test_a_failed_start_leaves_the_runner_stopped(tmp_path: Path) -> None:
    def catch_up(session: Any, day: date) -> None:
        raise PaperError("no instrument snapshot yet")

    runner = _runner(tmp_path, catch_up=catch_up)
    with pytest.raises(PaperError):
        asyncio.run(runner.start_async(DAY, "log_xz", {}, now_ms=ist_ms(11, 0), clock=lambda: ist_ms(11, 0)))
    assert runner.state == "stopped" and runner.session is None
    assert load_day(tmp_path, DAY) is None


def test_a_start_that_finishes_after_the_session_ended_ends_the_day(tmp_path: Path) -> None:
    runner = _runner(tmp_path, catch_up=lambda session, day: None)
    asyncio.run(runner.start_async(DAY, "log_xz", {}, now_ms=ist_ms(15, 29), clock=lambda: ist_ms(15, 31)))
    assert runner.state == "stopped" and runner.ended_by == "session ended"
    assert load_day(tmp_path, DAY)["state"] == "stopped"


def test_the_sync_start_still_works_for_a_restart(tmp_path: Path) -> None:
    continuation = _Continuation()
    runner = _runner(tmp_path, catch_up=lambda session, day: continuation)
    runner.start(DAY, "log_xz", {})
    assert runner.state == "running"
    assert continuation.resumed_on == threading.get_ident()


# ---------------------------------------------------------------- the real pipeline, split in two passes
def test_a_catch_up_split_while_the_recording_grows_equals_one_pass(tmp_path: Path) -> None:
    """The first pass reads a recording that is still being written (cut in the middle of a record), the second
    pass reads the rest: the same signals and trades as replaying the whole file at once."""
    from app.config import settings
    from app.live.recorder import recording_path
    from app.paper.catchup import replay_into
    from tests.test_paper_replay import _missing, run_session_for

    day = date(2026, 10, 9)
    missing = _missing(day, need_candles=True)
    if missing:
        pytest.skip(missing)
    source = recording_path(settings.feed_recordings_dir, day)
    whole_session, whole_vix = run_session_for(day)
    replay_into(whole_session, source, on_vix=whole_vix.add)

    data = source.read_bytes()
    cut = len(data) * 2 // 3 + 7  # almost surely inside a record
    growing = tmp_path / "growing.bin"
    growing.write_bytes(data[:cut])
    split_session, split_vix = run_session_for(day)
    continuation = replay_into(split_session, growing, on_vix=split_vix.add)
    with growing.open("ab") as fh:
        fh.write(data[cut:])
    continuation.resume(split_session)

    assert split_session.signals == whole_session.signals and split_session.signals
    assert split_session.book.trades == whole_session.book.trades
    assert split_session.shown == whole_session.shown
    shutil.rmtree(tmp_path, ignore_errors=True)
