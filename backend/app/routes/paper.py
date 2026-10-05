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
from app.paper.live import AlreadyRunning, NotRunning, PaperError
from app.paper.store import weekly_summary

router = APIRouter(prefix="/api/paper")
STRATEGIES = {"log_xz": {"label": "Log XZ (RMA 14, 5m)", "params": {}}}


class StartBody(BaseModel):
    strategy: str = "log_xz"
    params: dict[str, Any] = Field(default_factory=dict)


def _service(request: Request) -> LiveService:
    svc = getattr(request.app.state, "live", None)
    if svc is None or not svc.enabled:
        raise HTTPException(status_code=409, detail="the live feed is off, so there is nothing to trade on")
    return svc


def _now_ms(svc: LiveService) -> int:
    return svc.now_ms()


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    return {"strategies": [{"name": k, **v} for k, v in STRATEGIES.items()]}


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    svc = getattr(request.app.state, "live", None)
    if svc is None:
        return {"state": "disabled"}
    return svc.paper.status(_now_ms(svc))


@router.post("/start")
async def start(body: StartBody, request: Request) -> dict[str, Any]:
    svc = _service(request)
    if body.strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"unknown strategy {body.strategy!r}")
    now = _now_ms(svc)
    day = ist_date(now)
    try:
        svc.paper.start(day, body.strategy, body.params)
    except AlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PaperError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return svc.paper.status(now)


@router.post("/stop")
async def stop(request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        svc.paper.stop(_now_ms(svc))
    except NotRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return svc.paper.status(_now_ms(svc))


@router.get("/day")
async def day_file(request: Request, day: date = Query(...)) -> dict[str, Any]:
    svc = _service(request)
    saved = svc.paper.day_file(day)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"no paper day for {day.isoformat()}")
    return saved


@router.get("/week")
async def week(request: Request, day: date = Query(...)) -> dict[str, Any]:
    svc = _service(request)
    return weekly_summary(svc.paper.directory, day)
