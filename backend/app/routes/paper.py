"""Live signals (paper): start/stop a strategy on the live feed, status, one day, one week.

Paper trading places no orders. Every route runs on the feed's event loop (async), like the feed itself.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.data.store import CandleStore
from app.live.model import ist_date
from app.live.service import LiveService
from app.paper.desk import PaperDesk, UnknownSlot
from app.paper.live import AlreadyRunning, NotRunning, PaperError, PaperRunner, UnsupportedSettings
from app.paper.real import (
    DEFAULT_ACCOUNT_STOP,
    RealTradeStore,
    join_real_rows,
    order_helper,
    paper_trade_key,
    real_totals,
)
from app.paper.store import load_day, weekly_summary
from app.paper.trades import trade_rows

router = APIRouter(prefix="/api/paper")
STRATEGIES = {
    "log_xz": {"label": "Log XZ (RMA 14, 5m)", "params": {}},
    # stop orders at the channel; the walk-forward's last choice (2026-10-07, slippage 0.2)
    "price_channel": {"label": "Price Channel (length 20, 5m)", "params": {"length": 20}},
}


class StartBody(BaseModel):
    strategy: str = "log_xz"
    params: dict[str, Any] = Field(default_factory=dict)
    #: "1".."4": strategies run side by side, each with its own position and P&L
    slot: str = "1"
    #: who pressed Start: launchd paper_autostart ("auto") or the Paper panel ("hand")
    start_source: Literal["auto", "hand"] = "hand"


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


EXIT_RULES = {"premium_1to2": "1:2 premium (-20% / +40%)", "atr_1to2": "1:2 index ATR(14) (1x / 2x)"}


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    """The strategies paper runs, and the exit rules a slot can add (sent as params.exit_rule)."""
    return {"strategies": [{"name": k, **v} for k, v in STRATEGIES.items()],
            "exit_rules": [{"name": k, "label": v} for k, v in EXIT_RULES.items()]}


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
        # after 15:30 it replays the day, apart; the catch-up runs off the event loop, so the feed goes on
        await runner.start_async(
            day, body.strategy, body.params, now_ms=now, clock=lambda: _now_ms(svc),
            start_source=body.start_source,
        )
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
async def day_file(request: Request, day: date = Query(...), slot: str = Query("1"),
                   replay: bool = Query(False)) -> dict[str, Any]:
    """A slot's live day file, or with replay=true the replay of that day (a Start after the close)."""
    svc = _service(request)
    runner = _runner(svc, slot)
    saved = load_day(runner.directory / "replay", day) if replay else runner.day_file(day)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"no paper day for {day.isoformat()} in slot {slot}")
    return saved


@router.get("/trades")
async def trades(request: Request, slot: str | None = Query(None), from_day: date | None = Query(None, alias="from"),
                 to_day: date | None = Query(None, alias="to"), include_replay: bool = Query(False)) -> dict[str, Any]:
    """Every paper trade over the slots (live days only, unless include_replay): one row each."""
    svc = _service(request)
    dirs = {name: runner.directory for name, runner in svc.paper.runners.items()}
    rows = trade_rows(dirs, from_day=from_day, to_day=to_day, slot=slot, include_replay=include_replay,
                      candles=getattr(svc, "store", None))
    return {"rows": rows}


@router.get("/week")
async def week(request: Request, day: date = Query(...), slot: str = Query("1")) -> dict[str, Any]:
    svc = _service(request)
    return weekly_summary(_runner(svc, slot).directory, day)


# ---------------------------------------------------------------- real trades (manual fills; not paper logic)


def _paper_base(request: Request) -> Path:
    """`data/paper/`: from the live desk when the feed is up, else from settings (logging after the close)."""
    svc = getattr(request.app.state, "live", None)
    if svc is not None and getattr(svc, "enabled", False):
        try:
            return _runner(svc, "1").directory
        except Exception:  # noqa: BLE001
            pass
    return Path(settings.data_dir) / "paper"


def _slot_dirs(base: Path) -> dict[str, Path]:
    return {s: PaperDesk.slot_dir(base, s) for s in ("1", "2", "3", "4")}


def _real_store(request: Request) -> RealTradeStore:
    return RealTradeStore(_paper_base(request) / "real")


def _today_iso(request: Request) -> str:
    svc = getattr(request.app.state, "live", None)
    if svc is not None and getattr(svc, "enabled", False):
        return ist_date(_now_ms(svc)).isoformat()
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()


class RealTradeBody(BaseModel):
    slot: str
    day: date
    fill_time: int
    contract: str | None = None
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    exit_reason: str
    charges: float
    units: int


class RealSettingsBody(BaseModel):
    account_stop: float = DEFAULT_ACCOUNT_STOP


@router.get("/real")
async def real_trades(
    request: Request,
    slot: str | None = Query(None),
    from_day: date | None = Query(None, alias="from"),
    to_day: date | None = Query(None, alias="to"),
) -> dict[str, Any]:
    """Paper trades joined with any logged real fills, plus daily/running totals and the account-stop bar."""
    base = _paper_base(request)
    store = _real_store(request)
    svc = getattr(request.app.state, "live", None)
    candles = getattr(svc, "store", None) if svc is not None else CandleStore(settings.data_dir / "candles")
    paper = trade_rows(_slot_dirs(base), from_day=from_day, to_day=to_day, slot=slot, candles=candles)
    joined = join_real_rows(paper, store.load_trades())
    cfg = store.load_settings()
    return {
        "rows": joined,
        "totals": real_totals(joined, today=_today_iso(request), account_stop=cfg["account_stop"]),
        "settings": cfg,
    }


@router.put("/real")
async def upsert_real_trade(body: RealTradeBody, request: Request) -> dict[str, Any]:
    """Log or replace the real fill for one paper trade. Does not change the paper day file."""
    store = _real_store(request)
    key = paper_trade_key(slot=body.slot, day=body.day.isoformat(), fill_time=body.fill_time)
    try:
        saved = store.upsert({
            "key": key, "slot": body.slot, "day": body.day.isoformat(), "fill_time": body.fill_time,
            "contract": body.contract, "entry_time": body.entry_time, "entry_price": body.entry_price,
            "exit_time": body.exit_time, "exit_price": body.exit_price, "exit_reason": body.exit_reason,
            "charges": body.charges, "units": body.units,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"trade": saved}


@router.delete("/real")
async def delete_real_trade(request: Request, key: str = Query(...)) -> dict[str, Any]:
    if not _real_store(request).delete(key):
        raise HTTPException(status_code=404, detail=f"no real trade {key!r}")
    return {"deleted": key}


@router.get("/real/settings")
async def real_settings(request: Request) -> dict[str, Any]:
    return _real_store(request).load_settings()


@router.put("/real/settings")
async def put_real_settings(body: RealSettingsBody, request: Request) -> dict[str, Any]:
    try:
        return _real_store(request).save_settings(account_stop=body.account_stop)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/real/order-helper")
async def real_order_helper(request: Request, slot: str = Query("3")) -> dict[str, Any]:
    """Contract, quantity, stop trigger/limit and target limit from the slot's open paper position."""
    svc = getattr(request.app.state, "live", None)
    if svc is None or not getattr(svc, "enabled", False):
        return {"slot": slot, "helper": None, "state": "disabled"}
    status = _runner(svc, slot).status(_now_ms(svc))
    helper = order_helper(status.get("position"))
    return {"slot": slot, "helper": helper, "state": status.get("state")}
