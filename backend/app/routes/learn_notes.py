"""Read and write the Learn tab's notes file. Not part of the live feed."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings
from app.learn_notes import load_notes, save_notes

router = APIRouter(prefix="/api/learn")


class NoteIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    time: int
    price: float
    text: str = Field(max_length=280)
    symbol: str = Field(min_length=1, max_length=20)


class NotesBody(BaseModel):
    notes: list[NoteIn] = Field(default_factory=list, max_length=200)
    reads: dict[str, str] = Field(default_factory=dict)


@router.get("/notes")
def get_notes() -> dict[str, object]:
    return load_notes(settings.data_dir)


@router.put("/notes")
def put_notes(body: NotesBody) -> dict[str, bool]:
    save_notes(settings.data_dir, body.model_dump())
    return {"ok": True}
