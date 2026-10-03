"""Analyse button API. The response describes the chart. It cannot place an order."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.run import AnalysisRunError, run_analysis, track_symbol
from app.config import settings

router = APIRouter(prefix="/api/ai")


class AnalyseIn(BaseModel):
    symbol: str
    sessions: list[str] = Field(default_factory=list)
    image_base64: str | None = None


def _sessions(given: list[str]) -> list[str]:
    return given or list(settings.default_sessions)


@router.post("/analyse")
def analyse_chart(body: AnalyseIn) -> dict:
    try:
        return run_analysis(body.symbol, _sessions(body.sessions), body.image_base64)
    except AnalysisRunError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@router.get("/track")
def track(symbol: str, sessions: str = "") -> dict:
    chosen = [part for part in sessions.split(",") if part] or list(settings.default_sessions)
    return track_symbol(symbol, chosen)
