"""Scanner HTTP API. There is no order route."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.scanner.bars import IST
from app.scanner.config import ScannerSettings
from app.scanner.service import ScannerService

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


def get_service(request: Request) -> ScannerService:
    svc = getattr(request.app.state, "scanner", None)
    if svc is None:
        from app.config import settings
        from app.routes.candles import get_store

        svc = ScannerService(
            settings.data_dir / "scanner",
            store=get_store(),
            instruments_dir=settings.instruments_dir,
        )
        request.app.state.scanner = svc
    return svc


class SettingsIn(BaseModel):
    account_size: float | None = None
    max_loss_per_trade: float | None = None
    daily_loss_limit: float | None = None
    max_trades_per_day: int | None = None
    max_open_positions: int | None = None
    max_capital_per_trade: float | None = None
    min_rr: float | None = None
    entry_mode: str | None = None
    orb_stop: str | None = None
    target_mode: str | None = None
    trail_breakeven: bool | None = None
    tm_paths: int | None = None
    tm_seed: int | None = None
    tm_edge_points: float | None = None
    tm_min_win_rate: float | None = None
    tm_min_fires: int | None = None
    universe: str | None = None
    only_proven: bool | None = None
    auto_trade: bool | None = None
    live_subscribe: bool | None = None
    slippage_ticks: float | None = None
    tick_size: float | None = None
    setups: dict[str, bool] | None = None


class ReplayIn(BaseModel):
    day: date
    paths: int | None = Field(default=None, ge=1, le=10_000)


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    return get_service(request).status()


@router.get("/signals")
def signals(request: Request, day: date | None = None) -> dict[str, Any]:
    return {"signals": get_service(request).signals(day)}


@router.get("/signals/{signal_id}")
def signal(signal_id: str, request: Request, day: date | None = None) -> dict[str, Any]:
    row = get_service(request).signal(signal_id, day)
    if row is None:
        raise HTTPException(status_code=404, detail="signal not found")
    return row


@router.post("/scan")
async def scan(request: Request) -> dict[str, Any]:
    svc = get_service(request)
    return await asyncio.to_thread(svc.scan)


@router.get("/heatmap")
def heatmap(request: Request) -> dict[str, Any]:
    return {"rows": get_service(request).heatmap()}


@router.get("/settings")
def read_settings(request: Request) -> dict[str, Any]:
    return _settings_body(get_service(request))


@router.put("/settings")
def write_settings(body: SettingsIn, request: Request) -> dict[str, Any]:
    raw = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        get_service(request).update_settings(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _settings_body(get_service(request))


@router.get("/universes")
def universes(request: Request) -> dict[str, Any]:
    from app.scanner.universe import load_universes

    spec = load_universes()
    names = {
        uid: {"label": body.get("label") or uid, "symbols": [row["symbol"] for row in body.get("stocks") or []]}
        for uid, body in (spec.get("universes") or {}).items()
    }
    return {"as_of": spec.get("as_of"), "universes": names, "selected": get_service(request).settings().universe}


@router.get("/trades")
def trades(request: Request, day: date | None = None) -> dict[str, Any]:
    return {"trades": get_service(request).trades(day)}


@router.get("/report")
def report(request: Request, day: date | None = None) -> dict[str, Any]:
    body = get_service(request).report(day)
    if body is None:
        return {"report": None}
    return {"report": body}


@router.post("/replay")
async def replay(body: ReplayIn, request: Request) -> dict[str, Any]:
    svc = get_service(request)
    return await asyncio.to_thread(_replay, svc, body)


@router.get("/rankings")
def rankings(request: Request) -> dict[str, Any]:
    return get_service(request).rankings() or {"as_of": None, "pairs": []}


def _settings_body(svc: ScannerService) -> dict[str, Any]:
    from app.scanner.service import replay_gate

    body = svc.settings().to_dict()
    ready, reason = replay_gate(svc.root)
    body["replay_ready"] = ready
    body["replay_reason"] = reason
    return body


def _replay(svc: ScannerService, body: ReplayIn) -> dict[str, Any]:
    when = datetime.combine(body.day, datetime.min.time(), tzinfo=IST).replace(hour=15, minute=35)
    symbols, sectors, nifty, unmapped = svc._live_feed(when)
    if not symbols:
        raise HTTPException(status_code=400, detail="no candles for that day")
    settings = svc.settings()
    if body.paths is not None:
        settings = ScannerSettings.from_dict({**settings.to_dict(), "tm_paths": body.paths})
    state = svc.replay(body.day, symbols, sectors, nifty, settings)
    return {
        "day": body.day.isoformat(),
        "source": "replay",
        "unmapped": unmapped,
        "net": state["net"],
        "trades": state["trades"],
        "signals": state["signals"],
        "report": state.get("report"),
    }
