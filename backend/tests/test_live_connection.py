"""FeedConnection: one socket at most, backoff, re-authorize, subscription diffs, window, lock.
No network, no real waiting: connector / sleep / clock are injected."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.live.connection import Backoff, ConnectionLock, ConnectWindow, FeedConnection
from app.live.model import IST
from app.upstox.client import UpstoxAuthError

IN_WINDOW = int(datetime(2026, 10, 5, 10, 0, tzinfo=IST).timestamp() * 1000)
BEFORE_OPEN = int(datetime(2026, 10, 5, 8, 0, tzinfo=IST).timestamp() * 1000)
AFTER_920 = int(datetime(2026, 10, 5, 9, 30, tzinfo=IST).timestamp() * 1000)
KEYS = ["NSE_INDEX|Nifty 50", "NSE_INDEX|India VIX", "NSE_FO|48704"]


class FakeWS:
    def __init__(self, script: list[Any], sent: list[bytes]) -> None:
        self.script = list(script)
        self.sent = sent

    async def recv(self) -> Any:
        if not self.script:
            await asyncio.Event().wait()  # silence
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        await asyncio.sleep(0)
        return item

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class FakeConnector:
    """connector(uri) -> async context manager yielding a scripted socket."""

    def __init__(self, scripts: list[list[Any]]) -> None:
        self.scripts = scripts
        self.uris: list[str] = []
        self.sent: list[list[bytes]] = []
        self.open_now = 0
        self.max_open = 0

    def __call__(self, uri: str) -> Any:
        self.uris.append(uri)
        script = self.scripts.pop(0) if self.scripts else [ConnectionError("end")]
        sent: list[bytes] = []
        self.sent.append(sent)
        outer = self

        class Ctx:
            async def __aenter__(self_inner) -> FakeWS:  # noqa: N805
                outer.open_now += 1
                outer.max_open = max(outer.max_open, outer.open_now)
                return FakeWS(script, sent)

            async def __aexit__(self_inner, *a: object) -> None:  # noqa: N805
                outer.open_now -= 1

        return Ctx()


def make(scripts: list[list[Any]], *, stop_after: int = 1, **kw: Any) -> tuple[FeedConnection, FakeConnector, dict]:
    con = FakeConnector(scripts)
    rec: dict[str, Any] = {"frames": [], "sleeps": [], "auth": 0, "disc": 0, "states": []}

    def authorize() -> str:
        rec["auth"] += 1
        return f"wss://example.invalid/{rec['auth']}"

    async def sleep(s: float) -> None:
        rec["sleeps"].append(s)
        await asyncio.sleep(0)

    holder: dict[str, FeedConnection] = {}

    def on_disc() -> None:
        rec["disc"] += 1
        if rec["disc"] >= stop_after:
            holder["c"].stop()

    fc = FeedConnection(
        authorize=kw.pop("authorize", authorize),
        on_frame=lambda raw, wall: rec["frames"].append((raw, wall)),
        on_state=rec["states"].append,
        on_disconnect=on_disc,
        connector=con,
        now_ms=kw.pop("now_ms", lambda: IN_WINDOW),
        sleep=sleep,
        rng=random.Random(7),
        **kw,
    )
    holder["c"] = fc
    fc.set_keys(KEYS)
    return fc, con, rec


def run(fc: FeedConnection) -> None:
    asyncio.run(asyncio.wait_for(fc.run(), timeout=10))


def sub_frames(sent: list[bytes]) -> list[dict]:
    return [json.loads(x) for x in sent]


def test_connects_authorizes_subscribes_in_full_mode_and_forwards_frames() -> None:
    fc, con, rec = make([[b"a", b"b", ConnectionError("drop")]])
    run(fc)
    assert rec["auth"] == 1 and con.uris == ["wss://example.invalid/1"]
    assert [f[0] for f in rec["frames"]] == [b"a", b"b"] and rec["frames"][0][1] == IN_WINDOW
    subs = sub_frames(con.sent[0])
    assert len(subs) == 1 and subs[0]["method"] == "sub" and subs[0]["data"] == {"mode": "full", "instrumentKeys": KEYS}
    assert "live" in rec["states"]


def test_every_reconnect_authorizes_again_because_the_url_is_single_use() -> None:
    fc, con, rec = make([[b"x", ConnectionError("1")], [b"y", ConnectionError("2")], [b"z", ConnectionError("3")]], stop_after=3)
    run(fc)
    assert rec["auth"] == 3 == len(set(con.uris))
    assert rec["disc"] == 3
    assert all(len(sub_frames(s)) == 1 for s in con.sent)  # the full set is re-sent on every connect


def test_text_messages_are_ignored() -> None:
    fc, _, rec = make([["hello", b"real", ConnectionError("x")]])
    run(fc)
    assert [f[0] for f in rec["frames"]] == [b"real"]


def test_backoff_grows_is_jittered_capped_and_resets_after_a_good_session() -> None:
    scripts: list[list[Any]] = [[ConnectionError("a")] for _ in range(9)]
    scripts.append([b"ok", ConnectionError("good then drop")])
    scripts.append([ConnectionError("after good")])
    fc, _, rec = make(scripts, stop_after=11, backoff=Backoff(base=1.0, cap=60.0))
    run(fc)
    naps = [s for s in rec["sleeps"]]
    nominal = [min(60.0, 2.0**n) for n in range(1, 10)]
    for got, nom in zip(naps[:9], nominal, strict=True):
        assert 0.5 * nom <= got <= nom
    assert max(naps[:9]) <= 60.0 and naps[8] >= 30.0  # reached the cap region
    assert naps[9] <= 2.0  # reset to the first step after a session that delivered data
    assert len(set(naps[:4])) == 4  # jitter: no two identical waits


def test_a_rejected_token_shows_auth_failed_never_connects_and_backs_off_to_the_cap() -> None:
    def bad() -> str:
        raise UpstoxAuthError("expired")

    fc, con, rec = make([], stop_after=12, authorize=bad, backoff=Backoff(base=1.0, cap=60.0, auth_cap=300.0))
    run(fc)
    assert con.uris == [] and rec["states"].count("auth_failed") == 1 and fc.state == "auth_failed"
    assert max(rec["sleeps"]) > 60 and max(rec["sleeps"]) <= 300


def test_nothing_connects_outside_the_connect_window() -> None:
    calls = {"n": 0}

    def now() -> int:
        calls["n"] += 1
        if calls["n"] > 3:
            fc.stop()
        return BEFORE_OPEN

    fc, con, rec = make([[b"x"]], now_ms=now)
    run(fc)
    assert rec["auth"] == 0 and con.uris == [] and fc.state == "off"


def test_the_window_is_configurable_and_half_open() -> None:
    w = ConnectWindow.parse("08:55", "16:10")
    t = lambda h, m: int(datetime(2026, 10, 5, h, m, tzinfo=IST).timestamp() * 1000)  # noqa: E731
    assert not w.contains(t(8, 54)) and w.contains(t(8, 55)) and w.contains(t(16, 9)) and not w.contains(t(16, 10))
    assert w.contains(int(datetime(2026, 10, 3, 10, 0, tzinfo=IST).timestamp() * 1000))  # any weekday / Saturday


def test_subscription_changes_are_sent_as_sub_and_unsub_diffs() -> None:
    async def scenario() -> list[dict]:
        fc, con, rec = make([[]], stop_after=99)
        task = asyncio.create_task(fc.run())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if con.sent and con.sent[0]:
                break
        fc.set_keys([KEYS[0], KEYS[2], "NSE_EQ|INE002A01018"])
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(con.sent[0]) >= 3:
                break
        fc.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return sub_frames(con.sent[0])

    frames = asyncio.run(scenario())
    assert frames[0]["method"] == "sub" and frames[0]["data"]["instrumentKeys"] == KEYS
    rest = {f["method"]: f["data"]["instrumentKeys"] for f in frames[1:]}
    assert rest == {"unsub": [KEYS[1]], "sub": ["NSE_EQ|INE002A01018"]}
    assert all(f["data"]["mode"] == "full" for f in frames)


def test_a_silent_socket_while_the_market_is_open_forces_a_reconnect() -> None:
    fc, con, rec = make([[b"x"], []], stop_after=1, read_timeout_s=0.05, market_open=lambda: True)
    run(fc)
    assert rec["disc"] == 1 and len(con.uris) == 1


def test_a_silent_socket_while_the_market_is_closed_is_left_alone() -> None:
    async def scenario() -> int:
        fc, con, rec = make([[b"x"]], stop_after=99, read_timeout_s=0.03, market_open=lambda: False)
        task = asyncio.create_task(fc.run())
        await asyncio.sleep(0.3)
        fc.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return rec["disc"]

    assert asyncio.run(scenario()) == 0


def test_when_every_segment_is_closed_after_09_20_it_disconnects_and_rechecks_in_10_minutes() -> None:
    fc, con, rec = make([[b"info", b"snapshot", b"more"]], stop_after=1, now_ms=lambda: AFTER_920, market_closed=lambda: True)
    run(fc)
    assert fc.state == "closed" and [f[0] for f in rec["frames"]] == [b"info", b"snapshot"]  # kept the snapshot
    assert rec["sleeps"][-1] == 600.0 or rec["sleeps"] == []  # stop() ended the loop before the nap


def test_closed_status_recheck_nap_is_ten_minutes() -> None:
    fc, con, rec = make([[b"info", b"snap"], [b"info", b"snap"]], stop_after=2, now_ms=lambda: AFTER_920, market_closed=lambda: True)
    # stop only on the second disconnect: the first one must be followed by the 600 s nap
    run(fc)
    assert 600.0 in rec["sleeps"]


def test_a_closed_market_before_09_20_is_not_a_reason_to_leave() -> None:
    fc, con, rec = make([[b"info", b"snap", ConnectionError("x")]], now_ms=lambda: IN_WINDOW - 40 * 60_000, market_closed=lambda: True)
    # 09:20 is the threshold; at 09:20 minus 40 min of IN_WINDOW (10:00) = 09:20 exactly -> use 09:00 instead
    fc.closed_after = datetime(2026, 10, 5, 9, 21).time()
    run(fc)
    assert fc.state != "closed"


def test_never_two_sockets_in_one_process() -> None:
    fc, con, rec = make([[b"x", ConnectionError("1")], [b"y", ConnectionError("2")]], stop_after=2)
    run(fc)
    assert con.max_open == 1


def test_the_lock_file_stops_a_second_connection_object(tmp_path: Path) -> None:
    lock_a = ConnectionLock(tmp_path / "feed.lock")
    assert lock_a.acquire()

    fc, con, rec = make([[b"x"]], lock=ConnectionLock(tmp_path / "feed.lock"))
    calls = {"n": 0}
    orig_sleep = fc.sleep

    async def counting_sleep(s: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 3:
            fc.stop()
        await orig_sleep(s)

    fc.sleep = counting_sleep
    run(fc)
    assert con.uris == [] and rec["auth"] == 0 and fc.state == "locked_elsewhere"
    lock_a.release()
    assert ConnectionLock(tmp_path / "feed.lock").acquire()


def test_the_lock_is_released_after_a_session_so_a_reconnect_can_take_it_again(tmp_path: Path) -> None:
    fc, con, rec = make([[b"x", ConnectionError("1")], [b"y", ConnectionError("2")]], stop_after=2, lock=ConnectionLock(tmp_path / "feed.lock"))
    run(fc)
    assert len(con.uris) == 2
    other = ConnectionLock(tmp_path / "feed.lock")
    assert other.acquire()
    other.release()


def test_a_cross_process_lock_holder_blocks_us(tmp_path: Path) -> None:
    import subprocess
    import sys

    code = (
        "import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR);"
        "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('held',flush=True);time.sleep(30)"
    )
    p = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "feed.lock")], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout is not None and p.stdout.readline().strip() == "held"
        assert not ConnectionLock(tmp_path / "feed.lock").acquire()
    finally:
        p.kill()
        p.wait()
    assert ConnectionLock(tmp_path / "feed.lock").acquire()


def test_date_marker() -> None:
    assert date(2026, 10, 5).weekday() == 0
