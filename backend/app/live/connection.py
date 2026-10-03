"""The ONE Upstox market-data-feed connection.

Rules (from the Phase 2b brief):

* never more than one connection from this machine: an in-process guard AND a cross-process
  `flock` on `data/live-state/feed.lock` (a second backend, a replay script or a reload worker
  can never open a second socket; My Trading Desk may use the other one)
* the authorized wss URL is single-use, so we re-authorize on EVERY (re)connect
* reconnect with exponential backoff + jitter; an auth failure backs off to a cap and shows as
  `auth_failed`
* a silent socket is only a problem while the market is open (60 s read timeout then)
* when market_info says every segment is closed (and it is past 09:20) we disconnect and look
  again every 10 minutes
* subscriptions are diffed with `sub` / `unsub` frames (binary frames carrying JSON)

Everything time-related is injectable (`now_ms`, `sleep`, `rng`, `connector`, `authorize`) so the
tests never wait and never touch the network. The local clock here schedules and times out - it
never produces bar content.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Literal

import httpx

from app.live.model import IST
from app.upstox.client import UpstoxAuthError
from app.upstox.redact import redact

log = logging.getLogger("tradeboss.live.connection")

AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
MAX_KEYS = 100  # far below Upstox's 1500 combined full-mode keys; we only need a handful

State = Literal["off", "connecting", "live", "reconnecting", "auth_failed", "closed", "locked_elsewhere", "disabled"]


def authorize_feed(token: str) -> str:
    """Blocking: the single-use wss URL. Never logs the token or the URL."""
    resp = httpx.get(AUTHORIZE_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15.0)
    if resp.status_code in (401, 403):
        raise UpstoxAuthError(f"feed authorize rejected the token (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise RuntimeError(f"feed authorize failed: HTTP {resp.status_code}: {redact(resp.text[:200], [token])}")
    return str(resp.json()["data"]["authorized_redirect_uri"])


def subscribe_frame(method: str, keys: list[str], mode: str = "full") -> bytes:
    req = {"guid": uuid.uuid4().hex[:16], "method": method, "data": {"mode": mode, "instrumentKeys": keys}}
    return json.dumps(req).encode()


def default_connector(uri: str) -> Any:
    from websockets.asyncio.client import connect

    return connect(uri, open_timeout=15, max_size=None, ping_interval=None)


# ---------------------------------------------------------------- one connection per machine
class ConnectionLock:
    _held: set[str] = set()

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        key = str(self.path.resolve())
        if key in ConnectionLock._held:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        ConnectionLock._held.add(key)
        return True

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            ConnectionLock._held.discard(str(self.path.resolve()))
            self._fd = None


# ---------------------------------------------------------------- connect window
def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


@dataclass(frozen=True)
class ConnectWindow:
    start: dtime = dtime(8, 55)
    end: dtime = dtime(16, 10)

    @classmethod
    def parse(cls, start: str, end: str) -> ConnectWindow:
        return cls(_hhmm(start), _hhmm(end))

    def contains(self, now_ms: int) -> bool:
        t = datetime.fromtimestamp(now_ms / 1000, IST).time()
        return self.start <= t < self.end


def wall_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Backoff:
    base: float = 1.0
    cap: float = 60.0
    auth_cap: float = 300.0

    def delay(self, attempt: int, rng: random.Random, *, auth: bool = False) -> float:
        cap = self.auth_cap if auth else self.cap
        raw = min(cap, self.base * (2 ** min(attempt, 12)))
        return raw * (0.5 + rng.random() / 2)  # jitter: 50-100% of the nominal delay


class FeedConnection:
    def __init__(
        self,
        *,
        authorize: Callable[[], str],
        on_frame: Callable[[bytes, int], None],
        on_sent: Callable[[bytes, int], None] | None = None,
        on_state: Callable[[State], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        market_closed: Callable[[], bool] | None = None,
        market_open: Callable[[], bool] | None = None,
        lock: ConnectionLock | None = None,
        window: ConnectWindow | None = None,
        connector: Callable[[str], Any] = default_connector,
        now_ms: Callable[[], int] = wall_ms,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        backoff: Backoff | None = None,
        read_timeout_s: float = 60.0,
        closed_recheck_s: float = 600.0,
        closed_after: dtime = dtime(9, 20),
        outside_window_poll_s: float = 30.0,
        stable_after_s: float = 30.0,
    ) -> None:
        self.authorize = authorize
        self.on_frame = on_frame
        self.on_sent = on_sent
        self.on_state = on_state
        self.on_disconnect = on_disconnect
        self.market_closed = market_closed or (lambda: False)
        self.market_open = market_open or (lambda: True)
        self.lock = lock
        self.window = window or ConnectWindow()
        self.connector = connector
        self.now_ms = now_ms
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.backoff = backoff or Backoff()
        self.read_timeout_s = read_timeout_s
        self.closed_recheck_s = closed_recheck_s
        self.closed_after = closed_after
        self.outside_window_poll_s = outside_window_poll_s
        self.stable_after_s = stable_after_s

        self.state: State = "off"
        self.attempts = 0
        self.connections_opened = 0
        self.last_frame_wall_ms: int | None = None
        self._desired: list[str] = []
        self._subscribed: set[str] = set()
        self._wake = asyncio.Event()
        self._stop = False
        self._ws: Any = None
        self._in_connection = False  # in-process guard: at most one socket at a time
        self._frames_in_session = 0

    # -------------------------------------------------------------- control
    def set_keys(self, keys: list[str]) -> None:
        uniq = list(dict.fromkeys(keys))[:MAX_KEYS]
        self._desired = uniq
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def _set_state(self, s: State) -> None:
        if s != self.state:
            self.state = s
            log.info("feed state: %s", s)
            if self.on_state:
                self.on_state(s)

    # -------------------------------------------------------------- main loop
    async def run(self) -> None:
        attempt = 0
        while not self._stop:
            now = self.now_ms()
            if not self.window.contains(now):
                self._set_state("off")
                await self._nap(self.outside_window_poll_s)
                continue
            if not self._desired:
                await self._nap(1.0)
                continue
            if self.lock is not None and not self.lock.acquire():
                self._set_state("locked_elsewhere")
                log.warning("another process holds the feed connection lock; not connecting")
                await self._nap(self.outside_window_poll_s)
                continue
            started = time.monotonic()
            auth_failed = False
            try:
                await self._session()
            except UpstoxAuthError as exc:
                auth_failed = True
                log.error("feed authorization failed: %s", exc)
                self._set_state("auth_failed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - network drops are expected
                log.warning("feed connection error: %s: %s", type(exc).__name__, redact(str(exc)[:200], []))
            finally:
                if self.lock is not None:
                    self.lock.release()
            if self._frames_in_session > 0 or time.monotonic() - started >= self.stable_after_s:
                attempt = 0  # that session was healthy: start the backoff over
            if self._stop:
                break
            if self.on_disconnect:
                self.on_disconnect()
            if self.state == "closed":
                await self._nap(self.closed_recheck_s)
                continue
            attempt += 1
            if not auth_failed:
                self._set_state("reconnecting")
            await self._nap(self.backoff.delay(attempt, self.rng, auth=auth_failed))

    async def _nap(self, seconds: float) -> None:
        await self.sleep(seconds)

    # -------------------------------------------------------------- one session
    async def _session(self) -> None:
        assert not self._in_connection, "a second feed connection was attempted"
        self._in_connection = True
        self._frames_in_session = 0
        try:
            if self.state not in ("auth_failed", "reconnecting"):  # no badge flapping between retries
                self._set_state("connecting")
            uri = await asyncio.to_thread(self.authorize)
            async with self.connector(uri) as ws:
                self._ws = ws
                self.connections_opened += 1
                self._subscribed = set()
                self._wake.set()  # send the initial subscription
                sender = asyncio.create_task(self._sender(ws))
                try:
                    while not self._stop:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=self.read_timeout_s)
                        except TimeoutError:
                            if self.market_open():
                                log.warning("no data for %.0fs while the market is open: reconnecting", self.read_timeout_s)
                                break
                            continue
                        wall = self.now_ms()
                        self.last_frame_wall_ms = wall
                        if isinstance(msg, str):
                            continue
                        self._frames_in_session += 1
                        if self.state != "live":
                            self._set_state("live")
                        self.on_frame(bytes(msg), wall)
                        if self._should_close_for_market():
                            self._set_state("closed")
                            break
                finally:
                    sender.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await sender
                    self._ws = None
        finally:
            self._in_connection = False

    def _should_close_for_market(self) -> bool:
        if self._frames_in_session < 2:  # market_info AND the snapshot first (the snapshot is evidence)
            return False
        t = datetime.fromtimestamp(self.now_ms() / 1000, IST).time()
        return t >= self.closed_after and self.market_closed()

    async def _sender(self, ws: Any) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            want = set(self._desired)
            add = [k for k in self._desired if k not in self._subscribed]
            drop = sorted(self._subscribed - want)
            if drop:
                await self._send(ws, subscribe_frame("unsub", drop))
                self._subscribed -= set(drop)
            if add:
                await self._send(ws, subscribe_frame("sub", add))
                self._subscribed |= set(add)

    async def _send(self, ws: Any, frame: bytes) -> None:
        await ws.send(frame)  # a bytes payload goes out as a binary frame, as the docs require
        if self.on_sent:
            self.on_sent(frame, self.now_ms())
