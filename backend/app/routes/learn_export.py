"""Save a Learn coach note. Not part of the live feed."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.learn_export import save_export
from app.learn_practice import load_practice, save_practice

router = APIRouter(prefix="/api/learn")


class ExportBody(BaseModel):
    day: str = Field(min_length=10, max_length=10)
    markdown: str = Field(max_length=200_000)
    png: str = ""


class PracticeIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    day: str = Field(max_length=10)
    symbol: str = Field(max_length=20)
    direction: str = Field(max_length=8)
    reason: str = Field(max_length=280)
    tag: str = Field(max_length=40)
    level: float | None = None
    directionHit: bool | None = None
    levelHit: bool | None = None


class PracticeBody(BaseModel):
    reads: list[PracticeIn] = Field(default_factory=list, max_length=500)


@router.post("/export")
def post_export(body: ExportBody) -> dict[str, bool]:
    try:
        save_export(settings.data_dir, body.day, body.markdown, body.png)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/practice")
def get_practice() -> dict[str, object]:
    return load_practice(settings.data_dir)


@router.put("/practice")
def put_practice(body: PracticeBody) -> dict[str, bool]:
    save_practice(settings.data_dir, [row.model_dump() for row in body.reads])
    return {"ok": True}
