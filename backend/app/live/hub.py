"""Browser-facing side of the live feed: one WebSocket per tab, all sharing the single feed.

Protocol (JSON text frames)

client -> server
    {"type": "view", "symbol": "NIFTY50", "timeframe": "5m", "sessions": ["normal"],
     "indicators": [{"id": "ema20", "type": "ema", "params": {"length": 20}}]}
    {"type": "ping"}

server -> client
    {"type": "status", "state": "live|stale|reconnecting|closed|auth_failed|locked|disabled",
     "since_last_tick_s": 3.2, "market": "open|closed", ...}      about once a second
    {"type": "bar", "symbol", "timeframe", "candles": [last 2 candles of the timeframe],
     "volume_known": true, "times": [...], "indicators": [{"id", "outputs": {name: [...]}}]}
    {"type": "reload", "symbol": "NIFTY50"}      older bars changed (backfill / reconcile): refetch

At most `1 / push_interval` (5) `bar` messages per second per client: updates are coalesced
server-side. Candles come from the SAME code as /api/candles (`get_candle_page`) and indicators
from the SAME module as /api/indicators (`compute_indicators`, tail only: warm-up history is
loaded by the module itself), so the live view cannot drift from the REST view.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.data.service import get_candle_page
from app.data.store import CandleStore
from app.indicators.registry import IndicatorSpec, validate_params
from app.indicators.service import compute_indicators

log = logging.getLogger("tradeboss.live.hub")

PUSH_INTERVAL_S = 0.2  # <= 5 bar messages per second per client
STATUS_INTERVAL_S = 1.0
MAX_INDICATORS = 20


class Socket(Protocol):
    async def send_text(self, data: str) -> None: ...


@dataclass(frozen=True)
class ClientView:
    symbol: str
    timeframe: str
    sessions: tuple[str, ...]
    indicators: tuple[IndicatorSpec, ...] = ()


@dataclass(eq=False)
class Client:
    ws: Socket
    view: ClientView | None = None
    dirty: bool = False
    computing: bool = False
    last_push: float = -1e9
    last_status: float = -1e9
    sent_bars: int = 0
    sent_status: int = 0
    closed: bool = False


def parse_view(msg: dict[str, Any], default_sessions: tuple[str, ...]) -> ClientView:
    symbol, timeframe = msg.get("symbol"), msg.get("timeframe")
    if not isinstance(symbol, str) or not symbol or not isinstance(timeframe, str) or not timeframe:
        raise ValueError("view needs symbol and timeframe")
    sessions = msg.get("sessions")
    types = tuple(sessions) if isinstance(sessions, list) and all(isinstance(s, str) for s in sessions) else default_sessions
    raw = msg.get("indicators") or []
    if not isinstance(raw, list) or len(raw) > MAX_INDICATORS:
        raise ValueError("bad indicators")
    specs = tuple(
        IndicatorSpec(id=str(i["id"]), type=str(i["type"]), params=validate_params(str(i["type"]), dict(i.get("params") or {})))
        for i in raw
    )
    return ClientView(symbol, timeframe, types, specs)


def compute_update(store: CandleStore, view: ClientView, volume_known: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """The live tail for one view: the last two candles (+ indicator values for them)."""
    page = get_candle_page(store, view.symbol, view.timeframe, limit=2, session_types=list(view.sessions))
    candles = page.candles
    out: dict[str, Any] = {
        "type": "bar",
        "symbol": view.symbol,
        "timeframe": view.timeframe,
        "candles": candles,
        "volume_known": True if volume_known is None else volume_known(view.symbol),
    }
    if view.indicators and candles:
        res = compute_indicators(
            store, view.symbol, view.timeframe, view.indicators,
            from_time=candles[0]["time"], to_time=None, session_types=list(view.sessions),
        )  # fmt: skip
        out["times"] = res.times
        out["indicators"] = [{"id": o.id, "outputs": o.outputs} for o in res.indicators]
    return out


class LiveHub:
    def __init__(
        self,
        store: CandleStore,
        status_fn: Callable[[], dict[str, Any]],
        default_sessions: Callable[[], tuple[str, ...]],
        *,
        volume_known: Callable[[str], bool] | None = None,
        on_views_changed: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        push_interval: float = PUSH_INTERVAL_S,
        status_interval: float = STATUS_INTERVAL_S,
    ) -> None:
        self.store = store
        self.status_fn = status_fn
        self.default_sessions = default_sessions
        self.volume_known = volume_known
        self.on_views_changed = on_views_changed
        self.clock = clock
        self.push_interval = push_interval
        self.status_interval = status_interval
        self.clients: list[Client] = []
        self.errors = 0

    # -------------------------------------------------------------- connections
    async def connect(self, ws: Socket) -> Client:
        c = Client(ws)
        self.clients.append(c)
        await self._send(c, {"type": "status", **self.status_fn()})
        return c

    def disconnect(self, c: Client) -> None:
        c.closed = True
        if c in self.clients:
            self.clients.remove(c)
        if self.on_views_changed:
            self.on_views_changed()

    async def handle_message(self, c: Client, text: str) -> None:
        try:
            msg = json.loads(text)
            if not isinstance(msg, dict):
                raise ValueError("not an object")
            kind = msg.get("type")
            if kind == "ping":
                await self._send(c, {"type": "pong"})
            elif kind == "view":
                c.view = parse_view(msg, self.default_sessions())
                c.dirty = True  # send the current tail right away
                if self.on_views_changed:
                    self.on_views_changed()
            else:
                raise ValueError(f"unknown message type {kind!r}")
        except (ValueError, KeyError, TypeError) as exc:
            await self._send(c, {"type": "error", "message": str(exc)[:200]})

    # -------------------------------------------------------------- what is being watched
    def watched_symbols(self) -> set[str]:
        return {c.view.symbol for c in self.clients if c.view is not None}

    def mark_dirty(self, symbols: set[str] | None = None) -> None:
        """Bars changed for these symbol folders (None = all)."""
        for c in self.clients:
            if c.view is not None and (symbols is None or c.view.symbol in symbols):
                c.dirty = True

    async def broadcast_reload(self, symbols: set[str] | None = None) -> None:
        for c in list(self.clients):
            if c.view is not None and (symbols is None or c.view.symbol in symbols):
                await self._send(c, {"type": "reload", "symbol": c.view.symbol})

    # -------------------------------------------------------------- the pump
    async def pump_once(self) -> None:
        now = self.clock()
        status: dict[str, Any] | None = None
        for c in list(self.clients):
            if c.closed:
                continue
            if now - c.last_status >= self.status_interval:
                status = status or self.status_fn()
                c.last_status = now
                c.sent_status += 1
                await self._send(c, {"type": "status", **status})
            if c.dirty and not c.computing and c.view is not None and now - c.last_push >= self.push_interval:
                c.dirty = False
                c.computing = True
                c.last_push = now
                asyncio.create_task(self._push(c, c.view))

    async def _push(self, c: Client, view: ClientView) -> None:
        try:
            msg = await asyncio.to_thread(compute_update, self.store, view, self.volume_known)
            if not c.closed:
                c.sent_bars += 1
                await self._send(c, msg)
        except Exception as exc:  # noqa: BLE001 - e.g. unknown symbol / timeframe: tell the tab, keep going
            self.errors += 1
            if not c.closed:
                await self._send(c, {"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:160]}", "symbol": view.symbol})
        finally:
            c.computing = False

    async def run(self) -> None:
        while True:
            await self.pump_once()
            await asyncio.sleep(self.push_interval / 2)

    async def _send(self, c: Client, msg: dict[str, Any]) -> None:
        try:
            await c.ws.send_text(json.dumps(msg, separators=(",", ":"), allow_nan=False, default=str))
        except Exception:  # noqa: BLE001 - the tab went away
            c.closed = True

