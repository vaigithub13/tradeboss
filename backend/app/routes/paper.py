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
from app.paper.assist import AssistJournal, chart_for_close, ticket_from_session
from app.paper.assist_book import pending_trail
from app.paper.desk import SLOTS, PaperDesk, UnknownSlot
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
    # slots 5 (calls) and 6 (puts): the Quantman version, 3m bars, strike by premium, trailing premium stop
    "dd_five_confirm": {"label": "DD 5-confirmation (Quantman, 3m)", "params": {}},
}


class StartBody(BaseModel):
    strategy: str = "log_xz"
    params: dict[str, Any] = Field(default_factory=dict)
    #: "1".."7": strategies run side by side, each with its own position and P&L
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


def _start_expiry(svc: LiveService, slot: str) -> dict[str, Any]:
    """Slot 9 only. The desk's strategies stay on slots 1-7."""
    if slot != "9":
        raise HTTPException(status_code=400, detail="expiry_straddle runs on slot 9")
    book = getattr(svc, "expiry_slot", None)
    if book is None:
        raise HTTPException(status_code=400, detail="unknown slot '9'")
    now = _now_ms(svc)
    try:
        book.start(ist_date(now))
    except AlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaperError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    refresh = getattr(svc, "refresh_subscriptions", None)
    if refresh is not None:
        refresh()
    return book.status_body(now)


EXIT_RULES = {"premium_1to2": "1:2 premium (-20% / +40%)", "atr_1to2": "1:2 index ATR(14) (1x / 2x)",
              "premium_trail_10": "premium -10% / no target, trail +10% per +15%"}


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
    body = svc.paper.status(_now_ms(svc))
    slot = getattr(svc, "expiry_slot", None)
    if slot is not None:
        body = {**body, "slots": [*body["slots"], slot.status_body(_now_ms(svc))]}
    return body


@router.post("/start")
async def start(body: StartBody, request: Request) -> dict[str, Any]:
    svc = _service(request)
    if body.strategy == "expiry_straddle":
        return _start_expiry(svc, body.slot)
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
    if slot == "9":
        book = getattr(svc, "expiry_slot", None)
        if book is None:
            raise HTTPException(status_code=400, detail="unknown slot '9'")
        return book.weekly_summary(day)
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
    return {s: PaperDesk.slot_dir(base, s) for s in SLOTS}


def _real_store(request: Request) -> RealTradeStore:
    return RealTradeStore(_paper_base(request) / "real")


def _assist_store(request: Request) -> AssistJournal:
    return AssistJournal(_paper_base(request) / "assist")


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


# ------------------------------------------------------- manual assist (tickets and a journal; no orders)


class AssistSettingsBody(BaseModel):
    enabled: bool = True
    slots: list[str] = ["5", "7"]
    slot: str = "5"
    lots: int = 1
    max_trades: int = 3
    day_loss: float = 4000
    account_pause: float = 12000
    start_balance: float | None = None


class AssistTakenBody(BaseModel):
    key: str
    slot: str
    day: date
    fill_time: int
    decided_at_ms: int | None = None
    contract: str | None = None
    option_side: str | None = None
    signal_side: str | None = None
    fill_price: float
    entry_time: int
    sl_set: bool = False
    tp_set: bool = False
    lot_size: int
    paper_fill: float | None = None
    suggested_limit: float | None = None
    why: str = ""
    chart: dict[str, Any] | None = None
    price_action: dict[str, Any] | None = None
    market: str | None = None
    trailing: bool = False
    stop_frac: float | None = None
    target_frac: float | None = None


class AssistSkipBody(BaseModel):
    key: str
    slot: str
    day: date
    fill_time: int
    reason: str
    contract: str | None = None


class AssistCloseBody(BaseModel):
    key: str
    exit_price: float
    exit_time: int
    paper_exit_price: float | None = None
    paper_exit_time: int | None = None
    paper_exit_reason: str | None = None
    paper_net: float | None = None
    chart: dict[str, Any] | None = None


def _assist_state(request: Request) -> dict[str, Any]:
    body = _assist_store(request).state(today=_today_iso(request))
    body["trails"] = _live_trails(request)
    return body


def _live_trails(request: Request) -> list[dict[str, Any]]:
    """Stop moves on an open real trade. The rupee level is from the user's fill."""
    svc = getattr(request.app.state, "live", None)
    if svc is None or not getattr(svc, "enabled", False):
        return []
    journal = _assist_store(request)
    settings = journal.load_settings()
    records = journal.load()
    notices: list[dict[str, Any]] = []
    for slot in settings.get("slots") or []:
        try:
            runner = _runner(svc, str(slot))
        except HTTPException:
            continue
        session = runner.session
        position = None if session is None else session.book.position
        if session is None or position is None or not position.levels:
            continue
        paper_stop = position.levels.get("premium_stop")
        moved_ms = position.levels.get("premium_stop_at_ms")
        if paper_stop is None or moved_ms is None:
            continue
        best = session._best.get(position.key, position.entry.price)
        for rec in records:
            if rec.get("slot") != str(slot) or rec.get("kind") != "taken" or rec.get("exit_time") is not None:
                continue
            if rec.get("contract") and rec.get("contract") != position.symbol:
                continue
            notice = pending_trail(
                rec, paper_stop=float(paper_stop), best=float(best), moved_at=int(moved_ms) // 1000,
            )
            if notice is None:
                continue
            journal.note_trail_seen(rec["key"], float(paper_stop), int(moved_ms) // 1000)
            notices.append(notice)
    return notices


def _close_chart(request: Request, key: str, exit_time: int) -> dict[str, Any] | None:
    """Bars from the signal through the real exit, when the paper session is still up."""
    svc = getattr(request.app.state, "live", None)
    if svc is None or not getattr(svc, "enabled", False):
        return None
    record = next((row for row in _assist_store(request).load() if row.get("key") == key), None)
    if record is None:
        return None
    try:
        runner = _runner(svc, str(record["slot"]))
    except HTTPException:
        return None
    session = runner.session
    if session is None:
        return None
    return chart_for_close(record, list(session.shown), int(exit_time))


@router.get("/assist")
async def assist_state(request: Request) -> dict[str, Any]:
    """Settings, today's guard, open real trades, and the real-versus-paper table. No orders."""
    return _assist_state(request)


@router.put("/assist/settings")
async def put_assist_settings(body: AssistSettingsBody, request: Request) -> dict[str, Any]:
    try:
        saved = _assist_store(request).save_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved


@router.get("/assist/ticket")
async def assist_ticket(
    request: Request,
    slot: str = Query("5"),
    decided_at_ms: int = Query(...),
) -> dict[str, Any]:
    """The ticket for one filled paper signal: quote, limit, stop, target, and the channel facts."""
    svc = getattr(request.app.state, "live", None)
    if svc is None or not getattr(svc, "enabled", False):
        return {"ticket": None, "state": "disabled"}
    runner = _runner(svc, slot)
    settings = _assist_store(request).load_settings()
    ticket = ticket_from_session(
        runner.session,
        decided_at_ms=decided_at_ms,
        settings=settings,
        now_ms=_now_ms(svc),
        slot=slot,
        day=_today_iso(request),
    )
    return {"ticket": ticket, "state": runner.state}


@router.post("/assist/taken")
async def assist_taken(body: AssistTakenBody, request: Request) -> dict[str, Any]:
    """Log the fill the user took by hand. Recomputes the stop and target from that fill."""
    try:
        saved = _assist_store(request).take(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": saved}


@router.post("/assist/skipped")
async def assist_skipped(body: AssistSkipBody, request: Request) -> dict[str, Any]:
    try:
        saved = _assist_store(request).skip(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": saved}


@router.post("/assist/closed")
async def assist_closed(body: AssistCloseBody, request: Request) -> dict[str, Any]:
    """Complete a logged real trade at the price the user closed. Does not close the paper position."""
    payload = body.model_dump()
    payload["chart"] = _close_chart(request, payload["key"], payload["exit_time"]) or payload.get("chart")
    try:
        saved = _assist_store(request).close(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": saved}


class AssistTrailBody(BaseModel):
    key: str
    paper_stop: float
    real_stop: float
    moved_at: int
    updated_at: int


class PracticeBarsBody(BaseModel):
    bars: list[dict[str, Any]]


class PracticeReadBody(BaseModel):
    id: str
    direction: str
    reason: str
    tag: str
    level: float | None = None


@router.post("/assist/trail")
async def assist_trail(body: AssistTrailBody, request: Request) -> dict[str, Any]:
    """Log that the user moved the real stop to the trailed level. No order is sent."""
    try:
        saved = _assist_store(request).log_trail(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": saved}


@router.post("/assist/practice")
async def assist_practice_open(body: PracticeBarsBody, request: Request) -> dict[str, Any]:
    """Open a practice window. The last 6 bars stay on the server until the read is saved."""
    try:
        opened = _assist_store(request).open_practice(body.bars)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return opened


@router.post("/assist/practice/read")
async def assist_practice_read(body: PracticeReadBody, request: Request) -> dict[str, Any]:
    try:
        saved = _assist_store(request).save_practice_read(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved


@router.post("/assist/practice/reveal")
async def assist_practice_reveal(request: Request) -> dict[str, Any]:
    try:
        revealed = _assist_store(request).reveal_practice()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return revealed
