"""Recorder / replayer (cases K1-K7)."""

from __future__ import annotations

import struct
from datetime import date, datetime
from pathlib import Path

import pytest

from app.live.engine import LiveEngine
from app.live.frames import FeedItem, decode_frame, encode_feed, encode_market_info
from app.live.model import IST, minute_of
from app.live.recorder import BINARY, MAGIC, RECV, SENT, TEXT, Recorder, RecordingError, iter_records, recording_path, replay

MON = date(2026, 10, 5)
SAT = date(2026, 10, 3)
EQ = "NSE_EQ|INE002A01018"
OPEN = {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN", "NSE_INDEX": "NORMAL_OPEN"}
PRE = {"NSE_EQ": "PRE_OPEN_START"}
CLOSED = {"NSE_EQ": "NORMAL_CLOSE", "NSE_FO": "NORMAL_CLOSE", "NSE_INDEX": "CLOSING_END"}


def T(h: int, m: int, s: int = 0, day: date = MON) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000


def eq(ltt: int, px: float, vtt: int = 100) -> FeedItem:
    return FeedItem(EQ, px, ltt, 1, vtt, None, None, True)


def info(ts: int, segs: dict[str, str]) -> bytes:
    return encode_market_info(segs, ts)


def live(ts: int, ltt: int, px: float = 100.0) -> bytes:
    return encode_feed([eq(ltt, px)], ts)


def snap(ts: int, ltt: int) -> bytes:
    return encode_feed([eq(ltt, 100.0)], ts, snapshot=True)


def recs(d: Path, day: date = MON) -> list:
    p = recording_path(d, day)
    return list(iter_records(p)) if p.exists() else []


# ---------------------------------------------------------------- file format
def test_k1_records_round_trip(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    sub = b'{"method":"sub"}'
    r.write(SENT, BINARY, sub, T(9, 0, 1))
    a, b = info(T(9, 0, 1), PRE), live(T(9, 15, 2), T(9, 15, 1))
    assert r.write(RECV, BINARY, a, T(9, 0, 1))
    assert r.write(RECV, BINARY, b, T(9, 15, 2))
    r.write(RECV, TEXT, b"hello", T(9, 15, 3))
    r.close()
    p = recording_path(tmp_path, MON)
    assert p.read_bytes().startswith(MAGIC)
    got = recs(tmp_path)
    assert [(x.direction, x.kind, x.payload) for x in got] == [(SENT, BINARY, sub), (RECV, BINARY, a), (RECV, BINARY, b), (RECV, TEXT, b"hello")]
    assert got[2].wall_ms == T(9, 15, 2)


def test_k2_a_truncated_tail_stops_cleanly(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    for i in range(3):
        r.write(RECV, BINARY, live(T(9, 15, i + 1), T(9, 15, i)), T(9, 15, i + 1)) if i else r.write(
            RECV, BINARY, info(T(9, 15), OPEN), T(9, 15))
    r.close()
    p = recording_path(tmp_path, MON)
    data = p.read_bytes()
    p.write_bytes(data[:-5])
    assert len(list(iter_records(p))) == 2


def test_k3_a_foreign_file_and_a_corrupt_length_raise(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"nope")
    with pytest.raises(RecordingError):
        list(iter_records(p))
    p.write_bytes(MAGIC + struct.pack(">BBQI", 0, 0, 1, 0xFFFFFFF0) + b"zz")
    with pytest.raises(RecordingError):
        list(iter_records(p))
    p.write_bytes(MAGIC + struct.pack(">BBQI", 9, 0, 1, 2) + b"zz")
    with pytest.raises(RecordingError):
        list(iter_records(p))


def test_a_second_recorder_appends_to_the_same_day_without_a_second_header(tmp_path: Path) -> None:
    for i in range(2):  # process restart / reconnect
        r = Recorder(tmp_path)
        r.write(RECV, BINARY, info(T(10, i), OPEN), T(10, i))
        r.close()
    assert recording_path(tmp_path, MON).read_bytes().count(MAGIC) == 1
    assert len(recs(tmp_path)) == 2


# ---------------------------------------------------------------- k7: the gate
def test_k7_a_holiday_is_not_recorded(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    wall = T(9, 0, 1)
    for ts in range(0, 7 * 3600, 300):  # a weekday holiday: market_info says closed all day
        r.write(RECV, BINARY, info(wall + ts * 1000, CLOSED), wall + ts * 1000)
        r.write(RECV, BINARY, snap(wall + ts * 1000, T(15, 29, day=date(2026, 10, 2))), wall + ts * 1000)
    r.close()
    assert not recording_path(tmp_path, MON).exists() and r.written == 0


def test_k7_a_special_session_on_a_saturday_is_recorded(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    r.write(RECV, BINARY, info(T(9, 5, day=SAT), PRE), T(9, 5, day=SAT))
    r.write(RECV, BINARY, live(T(9, 16, day=SAT), T(9, 15, 59, day=SAT)), T(9, 16, day=SAT))
    r.close()
    assert len(recs(tmp_path, SAT)) == 2


def test_k7_the_window_is_09_00_to_16_05_ist_whatever_the_status(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    assert not r.write(RECV, BINARY, info(T(8, 59, 59), PRE), T(8, 59, 59))  # status open-ish but before 09:00
    assert r.write(RECV, BINARY, info(T(9, 0, 0), PRE), T(9, 0, 0))
    assert r.write(RECV, BINARY, live(T(16, 4, 59), T(15, 29)), T(16, 4, 59))  # still active at the end of the window
    assert not r.write(RECV, BINARY, live(T(16, 5, 0), T(15, 29)), T(16, 5, 0))
    r.close()
    assert len(recs(tmp_path)) == 2


def test_k7_once_active_it_keeps_recording_after_the_status_closes(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    r.write(RECV, BINARY, info(T(9, 5), PRE), T(9, 5))
    assert r.write(RECV, BINARY, info(T(15, 31), CLOSED), T(15, 31))
    assert r.write(RECV, BINARY, live(T(15, 40), T(15, 39, 59)), T(15, 40))
    r.close()


def test_k7_the_buffered_market_info_and_early_subscriptions_are_written_first(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    sub = b'{"method":"sub"}'
    r.write(SENT, BINARY, sub, T(8, 56))  # before the window
    r.write(RECV, BINARY, info(T(8, 56), CLOSED), T(8, 56))
    r.write(SENT, BINARY, sub, T(9, 0, 1))
    r.write(RECV, BINARY, snap(T(9, 0, 1), T(15, 29, day=date(2026, 10, 2))), T(9, 0, 1))  # inactive: snapshot dropped
    assert r.written == 0
    r.write(RECV, BINARY, info(T(9, 0, 5), PRE), T(9, 0, 5))
    r.close()
    kinds = [(x.direction, decode_frame(x.payload).kind if x.direction == RECV else "sub") for x in recs(tmp_path)]
    assert kinds == [(SENT, "sub"), (RECV, "market_info")]


def test_k7_an_in_session_trade_is_evidence_when_market_info_was_missed(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    assert not r.write(RECV, BINARY, snap(T(10, 0), T(9, 59)), T(10, 0))  # a snapshot proves nothing
    assert r.write(RECV, BINARY, live(T(10, 0, 5), T(10, 0, 4)), T(10, 0, 5))
    r.close()


def test_the_file_name_is_the_ist_date_of_the_receive_time(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    r.write(RECV, BINARY, info(T(9, 5), PRE), T(9, 5))
    r.close()
    assert [p.name for p in tmp_path.iterdir()] == ["2026-10-05.bin"]


# ---------------------------------------------------------------- the replayer
def make_recording(tmp_path: Path) -> Path:
    r = Recorder(tmp_path)
    r.write(RECV, BINARY, info(T(9, 5), PRE), T(9, 5))
    r.write(RECV, BINARY, snap(T(9, 5, 1), T(15, 29, day=date(2026, 10, 2))), T(9, 5, 1))
    for i in range(60):
        t = T(9, 15, 0) + i * 5_000
        r.write(RECV, BINARY, live(t + 400, t + 100, 100 + (i % 7) * 0.1), t + 400)
    r.write(RECV, BINARY, info(T(15, 30, 1), CLOSED), T(15, 30, 1))
    r.close()
    return recording_path(tmp_path, MON)


def test_replay_speed_scales_the_waits_and_zero_means_no_waiting(tmp_path: Path) -> None:
    p = make_recording(tmp_path)
    totals = {}
    for speed in (0, 1, 10, 100):
        slept: list[float] = []
        n = replay(p, lambda raw, wall, i: None, speed=speed, sleeper=slept.append)
        totals[speed] = sum(slept)
        assert n == 63
    assert totals[0] == 0
    span = (T(15, 30, 1) - T(9, 5)) / 1000
    assert totals[1] == pytest.approx(span)
    assert totals[10] == pytest.approx(span / 10) and totals[100] == pytest.approx(span / 100)


def test_replay_through_the_engine_equals_feeding_live(tmp_path: Path) -> None:
    p = make_recording(tmp_path)
    live_eng = LiveEngine()
    for i, rec in enumerate((r for r in iter_records(p) if r.direction == RECV), 1):
        live_eng.on_frame(rec.payload, rec.wall_ms, i)
    results = []
    for speed in (0, 100):
        eng = LiveEngine()
        replay(p, eng.on_frame, speed=speed, sleeper=lambda s: None)
        results.append(eng.bars(EQ, include_withheld=True))
    assert results[0] == results[1] == live_eng.bars(EQ, include_withheld=True)
    assert results[0] and minute_of(T(9, 15)) == results[0][0].minute


def test_a_recording_without_sent_frames_replays_only_received_binary(tmp_path: Path) -> None:
    r = Recorder(tmp_path)
    r.write(RECV, BINARY, info(T(9, 5), PRE), T(9, 5))
    r.write(SENT, BINARY, b"x", T(9, 5, 1))
    r.write(RECV, TEXT, b"txt", T(9, 5, 2))
    r.close()
    seen: list[bytes] = []
    replay(recording_path(tmp_path, MON), lambda raw, w, i: seen.append(raw))
    assert len(seen) == 1


def test_the_replay_script_writes_the_minute_log_and_the_i1_verdict_inputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json as _json

    from scripts.replay_feed import main

    p = make_recording(tmp_path / "rec")
    out = tmp_path / "out"
    assert main([str(p), "--speed", "0", "--out", str(out)]) == 0
    lines = [_json.loads(x) for x in (out / "2026-10-05.minutes.jsonl").read_text().splitlines()]
    assert lines[-1]["phase"] == "summary" and lines[-1]["replay"]["frames"] == 63
    assert any(x["phase"] == "close" and x["key"] == EQ and x["tick"] for x in lines)
    assert main(["/no/such/file.bin"]) == 2
    assert "minute log" in capsys.readouterr().out
