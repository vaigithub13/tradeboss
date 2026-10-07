"""Live signals (paper): start/stop a strategy on the live feed, status, one day, one week.

Paper trading places no orders. Every route runs on the feed's event loop (async), like the feed itself.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.live.model import ist_date
from app.live.service import LiveService
from app.paper.desk import UnknownSlot
from app.paper.live import AlreadyRunning, NotRunning, PaperError, PaperRunner, UnsupportedSettings
from app.paper.store import weekly_summary

router = APIRouter(prefix="/api/paper")
STRATEGIES = {"log_xz": {"label": "Log XZ (RMA 14, 5m)", "params": {}}}


class StartBody(BaseModel):
    strategy: str = "log_xz"
    params: dict[str, Any] = Field(default_factory=dict)
    #: "1" or "2": two strategies run side by side, each with its own position and P&L
    slot: str = "1"


def _service(request: Request) -> LiveService:
    svc = getattr(request.app.state, "live", None)
    if svc is None or not svc.enabled:
        raise HTTPException(status_code=409, detail="the live feed is off, so there is nothing to trade on")
    return svc


def _now_ms(svc: LiveService) -> int:
    return svc.now_ms()


def _runner(svc: LiveService, slot: str) -> PaperRunner:
    try:
        return svc.paper.runner(slot)
    except UnknownSlot as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _slot_status(svc: LiveService, slot: str, now: int) -> dict[str, Any]:
    return {"slot": slot, **_runner(svc, slot).status(now)}


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    return {"strategies": [{"name": k, **v} for k, v in STRATEGIES.items()]}


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    """Both slots: {"state": running if either runs, "slots": [{slot, state, ...}, ...]}."""
    svc = getattr(request.app.state, "live", None)
    if svc is None:
        return {"state": "disabled", "slots": []}
    return svc.paper.status(_now_ms(svc))


@router.post("/start")
async def start(body: StartBody, request: Request) -> dict[str, Any]:
    svc = _service(request)
    if body.strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"unknown strategy {body.strategy!r}")
    runner = _runner(svc, body.slot)
    now = _now_ms(svc)
    day = ist_date(now)
    try:
        runner.start(day, body.strategy, body.params)
    except AlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError, TypeError, UnsupportedSettings) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PaperError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _slot_status(svc, body.slot, now)


@router.post("/stop")
async def stop(request: Request, slot: str = Query("1")) -> dict[str, Any]:
    svc = _service(request)
    try:
        _runner(svc, slot).stop(_now_ms(svc))
    except NotRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _slot_status(svc, slot, _now_ms(svc))


@router.get("/day")
async def day_file(request: Request, day: date = Query(...), slot: str = Query("1")) -> dict[str, Any]:
    svc = _service(request)
    saved = _runner(svc, slot).day_file(day)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"no paper day for {day.isoformat()} in slot {slot}")
    return saved


@router.get("/week")
async def week(request: Request, day: date = Query(...), slot: str = Query("1")) -> dict[str, Any]:
    svc = _service(request)
    return weekly_summary(_runner(svc, slot).directory, day)
