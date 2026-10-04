"""Saved drawings. One list per symbol. Anchors stay time + price."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai.run import VIX_SYMBOL
from app.backtest.lots import LotSizeAmbiguous, LotSizeUnknown
from app.config import settings
from app.data.store import CandleStore, SymbolNotFound
from app.drawings.store import DrawingStore
from app.options.position import estimate_position_option, lot_for_position, position_option_applies
from app.options.vix import vix_asof

router = APIRouter(prefix="/api")


@lru_cache
def get_drawing_store() -> DrawingStore:
    return DrawingStore(settings.data_dir / "drawings.sqlite")


StoreDep = Annotated[DrawingStore, Depends(get_drawing_store)]


class DrawingsBody(BaseModel):
    symbol: str = Field(min_length=1)
    drawings: list[dict[str, Any]] = Field(default_factory=list)


class DrawingsOut(BaseModel):
    symbol: str
    drawings: list[dict[str, Any]]


@router.get("/drawings", response_model=DrawingsOut)
def load_drawings(symbol: str, store: StoreDep) -> DrawingsOut:
    return DrawingsOut(symbol=symbol, drawings=store.load(symbol))


@router.put("/drawings", response_model=DrawingsOut)
def save_drawings(body: DrawingsBody, store: StoreDep) -> DrawingsOut:
    try:
        store.replace(body.symbol, body.drawings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return DrawingsOut(symbol=body.symbol, drawings=store.load(body.symbol))


@router.post("/drawings/import", response_model=DrawingsOut)
def import_drawings(body: dict[str, Any], store: StoreDep) -> DrawingsOut:
    try:
        symbol = store.import_payload(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return DrawingsOut(symbol=symbol, drawings=store.load(symbol))


class LotOut(BaseModel):
    lot: int | None
    ambiguous: bool


class OptionBody(BaseModel):
    symbol: str = "NIFTY50"
    side: str
    entry: float
    target: float
    stop: float
    entry_time: int
    target_time: int
    stop_time: int
    status: str
    exit_index: float | None = None
    exit_time: int | None = None
    as_of: int | None = None
    account_size: float = 1_000_000
    risk_mode: str = "percent"
    risk_percent: float = 1
    risk_rupees: float = 10_000


class OptionOut(BaseModel):
    label: str | None = None


@router.get("/position-lot", response_model=LotOut)
def position_lot(symbol: str, on: str) -> LotOut:
    try:
        day = date.fromisoformat(on)
        return LotOut(lot=lot_for_position(symbol, day), ambiguous=False)
    except LotSizeAmbiguous:
        return LotOut(lot=None, ambiguous=True)
    except (LotSizeUnknown, ValueError):
        return LotOut(lot=None, ambiguous=False)


def _vix_at(when: int) -> float | None:
    try:
        rows, _base = CandleStore(settings.data_dir / "candles").load(VIX_SYMBOL, to_time=when)
    except (SymbolNotFound, OSError, ValueError):
        return None
    quote = vix_asof([{"time": row["time"], "open": row["open"]} for row in rows], when)
    return None if quote is None else quote.value


@router.post("/position-option", response_model=OptionOut)
def position_option(body: OptionBody) -> OptionOut:
    if not position_option_applies(body.symbol):
        return OptionOut(label=None)
    cap = body.entry_time if body.as_of is None else min(body.entry_time, body.as_of)
    vix = _vix_at(cap)
    if vix is None:
        return OptionOut(label=None)
    est = estimate_position_option(
        side=body.side,
        entry=body.entry,
        target=body.target,
        stop=body.stop,
        entry_time=body.entry_time,
        target_time=body.target_time,
        stop_time=body.stop_time,
        status=body.status,
        exit_index=body.exit_index,
        exit_time=body.exit_time,
        vix=vix,
        as_of=body.as_of,
        account_size=body.account_size,
        risk_mode=body.risk_mode,
        risk_percent=body.risk_percent,
        risk_rupees=body.risk_rupees,
    )
    return OptionOut(label=None if est is None else est.label)
