"""LiveService: wires connection -> recorder -> engine -> overlay/persistence -> hub.

Threading model: everything that touches the engine runs on the asyncio loop thread (frames,
backfill results) under `_elock`; blocking work (HTTP, Parquet I/O, indicator maths) runs in worker
threads and hands its result back to the loop. The reconcile applies its result under `_elock` too.

The local clock is only used to schedule (connect window, reconcile time, retries, the badge's
"seconds since last tick"). Bars use exchange time only (see engine.py / builder.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any

from app.data.history import symbol_dir_name
from app.data.store import CandleStore, SymbolNotFound, set_overlay
from app.live.backfill import fetch_backfill
from app.live.connection import (
    ConnectionLock,
    ConnectWindow,
    FeedConnection,
    State,
    authorize_feed,
    default_connector,
    wall_ms,
)
from app.live.engine import BackfillRequest, EngineConfig, LiveEngine
from app.live.frames import OPENISH_STATUSES
from app.live.hub import LiveHub
from app.live.minutelog import append_jsonl, minutes_path, write_minute_log
from app.live.model import IST, Bar
from app.live.overlay import LiveOverlay
from app.live.persist import ReconcileState, is_stored, upsert_bars
from app.live.reconcile import reconcile_missed, reconcile_today
from app.live.recorder import BINARY, RECV, SENT, Recorder
from app.upstox.instruments import NIFTY_INDEX_KEY, VIX_KEY, current_index

log = logging.getLogger("tradeboss.live.service")

STALE_AFTER_S = 15.0
MAX_SUBSCRIPTIONS = 12
BACKFILL_RETRY_S = 15.0
BACKFILL_WITHHOLD_MAX_S = 60.0
RECONCILE_RETRY_S = 300.0
REST_STATUS_EVERY_S = 60.0
MAINTENANCE_EVERY_S = 5.0

#: statuses the feed's market_info uses for "the market is (about to be) trading"
MARKET_SEGMENTS = ("NSE_EQ", "NSE_FO", "NSE_INDEX")


def badge_state(conn_state: str, market_open: bool | None, since_last_tick_s: float | None) -> str:
    """live | stale | reconnecting | closed | auth_failed | locked | disabled"""
    if conn_state in ("disabled", "auth_failed"):
        return conn_state
    if conn_state == "locked_elsewhere":
        return "locked"
    if conn_state in ("connecting", "reconnecting"):
        return "reconnecting"
    if conn_state == "live":
        if market_open is False:
            return "closed"
        if since_last_tick_s is None or since_last_tick_s > STALE_AFTER_S:
            return "stale" if market_open else "live"  # a quiet pre-open is not "stale"
        return "live"
    return "closed"  # off / closed


@dataclass
class LiveConfig:
    candles_dir: Path
    recordings_dir: Path
    state_dir: Path
    instruments_dir: Path
    open_volume_baseline: str = "first_tick"
    connect_start: str = "08:55"
    connect_end: str = "16:10"
    record_start: str = "09:00"
    record_end: str = "16:05"
    reconcile_at: str = "15:45"
    reconcile_until: str = "16:30"
    default_sessions: tuple[str, ...] = ("normal", "weekend_full")
    base_keys: tuple[str, ...] = (NIFTY_INDEX_KEY, VIX_KEY)


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


@dataclass
class _Flags:
    reconciled_day: date | None = None
    next_reconcile_try: float = 0.0
    next_rest_poll: float = 0.0
    rest_status: dict[str, Any] | None = None
    close_log_written: bool = False


class LiveService:
    def __init__(
        self,
        cfg: LiveConfig,
        store: CandleStore,
        *,
        client_factory: Callable[[], Any | None],
        authorize: Callable[[], str] | None = None,
        connector: Callable[[str], Any] = default_connector,
        now_ms: Callable[[], int] = wall_ms,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        enabled: bool = True,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.client_factory = client_factory
        self.now_ms = now_ms
        self.sleep = sleep
        self.enabled = enabled
        self.overlay = LiveOverlay()
        self.engine = LiveEngine(EngineConfig(open_volume_baseline=cfg.open_volume_baseline))  # type: ignore[arg-type]
        self.recorder = Recorder(cfg.recordings_dir, window_start=cfg.record_start, window_end=cfg.record_end)
        self.state = ReconcileState(cfg.state_dir)
        self._elock = threading.RLock()
        self._flags = _Flags()
        self._tasks: list[asyncio.Task[Any]] = []
        self._inflight: set[str] = set()
        self._last_attempt: dict[str, float] = {}
        self._first_requested: dict[str, float] = {}
        self._key_cache: dict[str, str | None] = {}
        self._close_records: list[dict] | None = None
        self.hub = LiveHub(
            store,
            self.status,
            lambda: cfg.default_sessions,
            volume_known=self._volume_known,
            on_views_changed=self.refresh_subscriptions,
        )
        self.connection = FeedConnection(
            authorize=authorize or (lambda: authorize_feed(_token())),
            on_frame=self._on_frame,
            on_sent=lambda frame, wall: self.recorder.write(SENT, BINARY, frame, wall),
            on_disconnect=self._on_disconnect,
            market_closed=self._market_closed,
            market_open=lambda: self.market_open() is not False,
            lock=ConnectionLock(cfg.state_dir / "feed.lock"),
            window=ConnectWindow.parse(cfg.connect_start, cfg.connect_end),
            connector=connector,
            now_ms=now_ms,
        )

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        set_overlay(self.overlay.rows)
        self._tasks = [asyncio.create_task(self.hub.run(), name="live-hub")]  # status works even when the feed is off
        if not self.enabled:
            return
        self.refresh_subscriptions()
        self._tasks += [
            asyncio.create_task(self.connection.run(), name="live-feed"),
            asyncio.create_task(self._maintenance(), name="live-maintenance"),
            asyncio.create_task(self._startup_reconcile(), name="live-missed-reconcile"),
        ]

    async def stop(self) -> None:
        self.connection.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []
        if self.connection.lock is not None:
            self.connection.lock.release()
        self.recorder.close()
        set_overlay(None)

    # ------------------------------------------------------------------ feed callbacks (loop thread)
    def _on_frame(self, raw: bytes, wall: int) -> None:
        try:
            self.recorder.write(RECV, BINARY, raw, wall)
        except OSError as exc:  # a full disk must not stop the live view
            log.error("recorder write failed: %s", exc)
        with self._elock:
            self.engine.on_frame(raw, wall)
        self._drain()

    def _on_disconnect(self) -> None:
        with self._elock:
            self.engine.on_disconnect()

    def _market_closed(self) -> bool:
        return self.market_open() is False

    def market_open(self) -> bool | None:
        seg = {k: v for k, v in self.engine.segments.items() if k in MARKET_SEGMENTS}
        if not seg:
            return None
        return any(v in OPENISH_STATUSES for v in seg.values())

    def _drain(self) -> None:
        """Move engine output to the overlay / hub / background jobs."""
        with self._elock:
            events = self.engine.take_events()
            diffs = self.engine.take_diffs()
            requests = self.engine.take_backfill_requests()
            finished = self.engine.take_finished()
            day = self.engine.day
        if events:
            by_dir: dict[str, list[Bar]] = {}
            for ev in events:
                by_dir.setdefault(symbol_dir_name(ev.key), []).append(ev.bar)
            for d, bars in by_dir.items():
                self.overlay.upsert(d, bars)
            self.hub.mark_dirty(set(by_dir))
        if diffs and day is not None:
            append_jsonl(self.cfg.recordings_dir / f"{day.isoformat()}.overwrites.jsonl", [x.as_dict() for x in diffs])
        for req in requests:
            self._spawn_backfill(req)
        for d, key in finished:
            asyncio.get_running_loop().create_task(self._persist(d, key))

    def _sync_key(self, key: str) -> None:
        """Replace the overlay for `key` with everything the engine has (after backfill / release)."""
        with self._elock:
            bars = self.engine.bars(key)
        d = symbol_dir_name(key)
        self.overlay.replace(d, bars)
        self.hub.mark_dirty({d})

    # ------------------------------------------------------------------ backfill
    def _spawn_backfill(self, req: BackfillRequest) -> None:
        loop = asyncio.get_running_loop()
        self._first_requested.setdefault(req.key, loop.time())
        if req.key in self._inflight:
            return
        self._inflight.add(req.key)
        loop.create_task(self._backfill(req))

    async def _backfill(self, req: BackfillRequest) -> None:
        loop = asyncio.get_running_loop()
        self._last_attempt[req.key] = loop.time()
        try:
            client = self.client_factory()
            if client is None:
                log.warning("backfill %s skipped: no Upstox token", req.key)
                return
            bars = await asyncio.to_thread(fetch_backfill, client, req.key, req.day, req.first_minute, req.last_minute)
            with self._elock:
                self.engine.apply_backfill(req.key, bars)
            self._drain()
            self._sync_key(req.key)
            await self.hub.broadcast_reload({symbol_dir_name(req.key)})
            log.info("backfilled %s: %d bars for %d..%d", req.key, len(bars), req.first_minute, req.last_minute)
        except Exception as exc:  # noqa: BLE001 - retried by the maintenance loop
            log.warning("backfill %s failed: %s: %s", req.key, type(exc).__name__, exc)
        finally:
            self._inflight.discard(req.key)
            with self._elock:
                still = any(r.key == req.key for r in self.engine.outstanding_backfills())
            if not still:
                self._first_requested.pop(req.key, None)

    # ------------------------------------------------------------------ persistence at session end
    async def _persist(self, day: date, key: str) -> None:
        try:
            with self._elock:
                bars = self.engine.bars(key, include_withheld=True)
            if is_stored(self.cfg.candles_dir, key):
                keep_oi = "_FO" in key.split("|", 1)[0]
                n = await asyncio.to_thread(upsert_bars, self.cfg.candles_dir, key, bars, keep_oi=keep_oi)
                self.state.mark(day, key)
                log.info("persisted %d live bars of %s for %s (unreconciled until the official fetch)", n, key, day)
            self._sync_key(key)
        except Exception as exc:  # noqa: BLE001
            log.error("persisting %s failed: %s: %s", key, type(exc).__name__, exc)
        self._maybe_write_close_log()

    def _maybe_write_close_log(self) -> None:
        with self._elock:
            builders = list(self.engine.builders.values())
            if not builders or not all(b.ended for b in builders) or self._flags.close_log_written:
                return
            self._close_records = self.engine.minute_records()
            summary = self.engine.daily_summary()
            day = self.engine.day
        assert day is not None
        self._flags.close_log_written = True
        write_minute_log(minutes_path(self.cfg.recordings_dir, day.isoformat()), close=self._close_records, reconciled=None, summary=summary)

    # ------------------------------------------------------------------ reconcile
    async def reconcile_now(self) -> list:
        """The 15:45 job (also callable by hand). Ends the session first, then fetches official bars."""
        client = self.client_factory()
        if client is None:
            log.warning("reconcile skipped: no Upstox token")
            return []
        with self._elock:
            self.engine.end_day()
            day = self.engine.day
        self._drain()
        await asyncio.sleep(0)
        if day is None:
            return []
        extra = self.subscription_keys()
        reports = await asyncio.to_thread(
            reconcile_today, self.engine, client,
            candles_dir=self.cfg.candles_dir, log_dir=self.cfg.recordings_dir, state=self.state,
            extra_keys=extra, engine_lock=self._elock,
        )  # fmt: skip
        for r in reports:
            if r.ok:
                self._sync_key(r.key)
        self._maybe_write_close_log()
        with self._elock:
            records, summary = self.engine.minute_records(), self.engine.daily_summary()
        write_minute_log(minutes_path(self.cfg.recordings_dir, day.isoformat()),
                         close=self._close_records or [], reconciled=records, summary=summary)
        await self.hub.broadcast_reload({symbol_dir_name(r.key) for r in reports if r.ok})
        if reports and all(r.ok for r in reports):
            self._flags.reconciled_day = day
        return reports

    async def _startup_reconcile(self) -> None:
        client = self.client_factory()
        if client is None or not self.state.pending():
            return
        today = datetime.fromtimestamp(self.now_ms() / 1000, IST).date()
        try:
            reports = await asyncio.to_thread(
                reconcile_missed, client,
                candles_dir=self.cfg.candles_dir, log_dir=self.cfg.recordings_dir, state=self.state, today=today,
            )  # fmt: skip
            log.info("startup reconcile of missed days: %d instrument-days, %d ok", len(reports), sum(r.ok for r in reports))
            await self.hub.broadcast_reload(None)
        except Exception as exc:  # noqa: BLE001
            log.warning("startup reconcile failed: %s: %s", type(exc).__name__, exc)

    # ------------------------------------------------------------------ maintenance loop
    async def _maintenance(self) -> None:
        while True:
            try:
                await self.maintenance_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("live maintenance error: %s: %s", type(exc).__name__, exc)
            await self.sleep(MAINTENANCE_EVERY_S)

    async def maintenance_once(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        # 1. retry unfinished backfills; stop withholding instruments that have waited too long
        with self._elock:
            outstanding = self.engine.outstanding_backfills()
        for req in outstanding:
            if req.key in self._inflight:
                continue
            waited = now - self._first_requested.get(req.key, now)
            if waited >= BACKFILL_WITHHOLD_MAX_S and not self.engine.publishable(req.key):
                with self._elock:
                    self.engine.release(req.key)
                self._drain()
                self._sync_key(req.key)
                log.warning("backfill for %s still missing after %.0fs: showing live bars, gap stays marked", req.key, waited)
            if now - self._last_attempt.get(req.key, -1e9) >= BACKFILL_RETRY_S:
                self._spawn_backfill(req)
        # 2. the 15:45 reconcile (retry until 16:30)
        local = datetime.fromtimestamp(self.now_ms() / 1000, IST)
        t = local.time()
        if (
            _hhmm(self.cfg.reconcile_at) <= t < _hhmm(self.cfg.reconcile_until)
            and self.engine.day == local.date()
            and self._flags.reconciled_day != local.date()
            and now >= self._flags.next_reconcile_try
        ):
            self._flags.next_reconcile_try = now + RECONCILE_RETRY_S
            await self.reconcile_now()
        # 3. REST market status as a second source (informational)
        if now >= self._flags.next_rest_poll and self.connection.window.contains(self.now_ms()):
            self._flags.next_rest_poll = now + REST_STATUS_EVERY_S
            client = self.client_factory()
            if client is not None:
                with contextlib.suppress(Exception):
                    self._flags.rest_status = await asyncio.to_thread(client.market_status, "NSE")
        # 4. instrument roll (front future changes) etc.
        self.refresh_subscriptions()

    # ------------------------------------------------------------------ subscriptions
    def key_for_symbol(self, symbol: str) -> str | None:
        if symbol in self._key_cache:
            return self._key_cache[symbol]
        key: str | None = None
        if symbol == symbol_dir_name(NIFTY_INDEX_KEY):
            key = NIFTY_INDEX_KEY
        else:
            with contextlib.suppress(SymbolNotFound, OSError):
                k = self.store.meta(symbol).instrument.get("instrument_key")
                key = str(k) if k else None
        self._key_cache[symbol] = key
        return key

    def subscription_keys(self) -> list[str]:
        keys = list(self.cfg.base_keys)
        idx = current_index(self.cfg.instruments_dir)
        fut = idx.front_future() if idx is not None else None
        if fut is not None:
            keys.append(fut.key)
        for sym in sorted(self.hub.watched_symbols()):
            k = self.key_for_symbol(sym)
            if k:
                keys.append(k)
        return list(dict.fromkeys(keys))[:MAX_SUBSCRIPTIONS]

    def refresh_subscriptions(self) -> None:
        self.connection.set_keys(self.subscription_keys())

    # ------------------------------------------------------------------ status
    def _volume_known(self, symbol: str) -> bool:
        key = self.key_for_symbol(symbol)
        if key is None:
            return True
        b = self.engine.builders.get(key)
        if b is None:
            return True
        bars = b.bars()
        return not bars or bars[-1].volume is not None

    def status(self) -> dict[str, Any]:
        now = self.now_ms()
        last = self.engine.last_live_wall_ms
        since = None if last is None else max(0.0, (now - last) / 1000.0)
        conn_state: State | str = "disabled" if not self.enabled else self.connection.state
        mo = self.market_open()
        return {
            "state": badge_state(conn_state, mo, since),
            "connection": conn_state,
            "since_last_tick_s": None if since is None else round(since, 1),
            "market": {None: "unknown", True: "open", False: "closed"}[mo],
            "day": None if self.engine.day is None else self.engine.day.isoformat(),
            "recording": self.recorder.active,
            "keys": self.connection._desired,  # noqa: SLF001 - informational
            "withheld": [k for k in self.engine.keys() if not self.engine.publishable(k)],
            "unreconciled": [[d.isoformat(), k] for d, k in self.state.pending()],
        }


def _token() -> str:
    from app.config import settings

    t = settings.upstox_token_value()
    if t is None:
        from app.upstox.client import UpstoxAuthError

        raise UpstoxAuthError("no Upstox token configured")
    return t


def build_service() -> LiveService:
    """The production wiring (settings -> LiveService)."""
    from app.config import settings
    from app.routes.candles import get_store
    from app.upstox.deps import make_client

    cfg = LiveConfig(
        candles_dir=settings.candles_dir,
        recordings_dir=settings.feed_recordings_dir,
        state_dir=settings.live_state_dir,
        instruments_dir=settings.instruments_dir,
        open_volume_baseline=settings.live_open_volume_baseline,
        connect_start=settings.live_connect_start,
        connect_end=settings.live_connect_end,
        record_start=settings.live_record_start,
        record_end=settings.live_record_end,
        reconcile_at=settings.live_reconcile_at,
        reconcile_until=settings.live_reconcile_until,
        default_sessions=settings.default_sessions,
    )
    enabled = settings.live_feed_enabled and settings.upstox_token_value() is not None
    return LiveService(cfg, get_store(), client_factory=make_client, enabled=enabled)
