"""Live feed endpoints: WebSocket /api/live/ws, GET /api/live/status, POST /api/live/reconcile."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.live.service import LiveService

log = logging.getLogger("tradeboss.live.route")
router = APIRouter(prefix="/api/live")

_OFF: dict[str, Any] = {
    "type": "status",
    "state": "disabled",
    "connection": "disabled",
    "since_last_tick_s": None,
    "market": "unknown",
}


def _service(app_state: Any) -> LiveService | None:
    return getattr(app_state, "live", None)


@router.get("/status")
def live_status(request: Request) -> dict[str, Any]:
    svc = _service(request.app.state)
    if svc is None:
        return {"state": "disabled", "connection": "disabled"}
    return svc.status()


@router.post("/reconcile")
async def live_reconcile(request: Request) -> dict[str, Any]:
    """Run the 15:45 reconcile by hand (ends the session first). Needs the data token."""
    svc = _service(request.app.state)
    if svc is None:
        raise HTTPException(status_code=503, detail="live feed is disabled (LIVE_FEED_ENABLED=false)")
    reports = await svc.reconcile_now()
    return {
        "reconciled": [
            {
                "day": r.day.isoformat(),
                "key": r.key,
                "ok": r.ok,
                "official_bars": r.official_bars,
                "differences": len(r.diffs),
                "replaced_rows": r.replaced_rows,
                "error": r.error,
            }
            for r in reports
        ]
    }


async def _serve_disabled(ws: WebSocket) -> None:
    """The feed is off: stay connected (a closed socket would make the tab reconnect in a loop) and
    repeat the status every second like the real hub, so the tab's watchdog stays quiet and it
    shows "live feed off". "ping" gets its pong; everything else is ignored."""
    try:
        await ws.send_json(_OFF)
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                await ws.send_json(_OFF)
                continue
            try:
                kind = json.loads(text).get("type")
            except (ValueError, AttributeError):
                kind = None
            if kind == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass


@router.websocket("/ws")
async def live_ws(ws: WebSocket) -> None:
    svc = _service(ws.app.state)
    await ws.accept()
    if svc is None:
        await _serve_disabled(ws)
        return
    client = await svc.hub.connect(ws)  # type: ignore[arg-type]
    try:
        while True:
            text = await ws.receive_text()
            await svc.hub.handle_message(client, text)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("live websocket closed: %s", type(exc).__name__)
    finally:
        svc.hub.disconnect(client)
