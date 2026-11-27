"""Read the Learn signal log and run today's review. Not part of the live feed."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.config import settings
from app.learn_signals import IST, load_summary, review_day

router = APIRouter(prefix="/api/learn")


@router.get("/signals")
def get_signals() -> dict[str, object]:
    return load_summary(settings.data_dir)


@router.post("/signals/review")
def post_review() -> dict[str, object]:
    day = datetime.now(IST).date()
    return review_day(settings.data_dir, settings.data_dir / "candles", day)
