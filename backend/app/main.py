import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from app.config import settings
from app.routes.ai import router as ai_router
from app.routes.backtests import router as backtests_router
from app.routes.candles import router as candles_router
from app.routes.indicators import router as indicators_router
from app.routes.live import router as live_router
from app.routes.pine import router as pine_router
from app.routes.upstox import router as upstox_router
from app.upstox.instruments import snapshot_due, take_snapshot

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("app.startup")


def _startup_snapshot() -> None:
    try:
        result = take_snapshot(settings.instruments_dir)
        log.info("instrument snapshot %s: %s", result.day, result.outcome)
    except Exception as e:  # network down etc.: the daily job / next start will retry
        log.warning("startup instrument snapshot failed: %s", type(e).__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup fallback for the launchd job: if today's instrument file is missing and it is past
    06:30 IST, fetch it in the background (never blocks or fails the start)."""
    if settings.snapshot_on_startup and snapshot_due(settings.instruments_dir):
        threading.Thread(target=_startup_snapshot, daemon=True).start()
    live = None
    if settings.live_feed_enabled:
        from app.live.service import build_service

        live = build_service()
        _app.state.live = live
        await live.start()
    yield
    if live is not None:
        await live.stop()


app = FastAPI(title="Chart Analyser API", version="0.1.0", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(backtests_router)
app.include_router(candles_router)
app.include_router(indicators_router)
app.include_router(upstox_router)
app.include_router(live_router)
app.include_router(pine_router)
app.include_router(ai_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    time_ist: str
    live_trading: bool


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="chart-analyser-backend",
        version=app.version,
        time_ist=datetime.now(IST).isoformat(timespec="seconds"),
        live_trading=settings.live_trading,
    )
