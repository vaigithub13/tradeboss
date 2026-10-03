"""Saved drawings. One list per symbol. Anchors stay time + price."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.drawings.store import DrawingStore

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
