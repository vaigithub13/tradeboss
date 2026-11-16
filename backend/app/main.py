import logging
import os
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
from app.routes.drawings import router as drawings_router
from app.routes.fvg import router as fvg_router
from app.routes.gex import router as gex_router
from app.routes.indicators import router as indicators_router
from app.routes.live import router as live_router
from app.routes.market_view import router as market_view_router
from app.routes.options import router as options_router
from app.routes.paper import router as paper_router
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
    from app.ownership import clear, path_for, write as write_ownership

    own_path = path_for(settings.live_state_dir)
    if settings.backend_log_file:
        from app.logsetup import configure_logging

        configure_logging(settings.data_dir / "logs")
        log.info("backend started (pid %d)", os.getpid())
    own = write_ownership(own_path)
    log.info("backend ownership %s (pid %d, started_on %s)", own_path, own.pid, own.started_on)
    if settings.snapshot_on_startup and snapshot_due(settings.instruments_dir):
        threading.Thread(target=_startup_snapshot, daemon=True).start()
    live = None
    if settings.live_feed_enabled:
        from app.live.service import build_service

        live = build_service()
        _app.state.live = live
        await live.start()
    gex = None
    if settings.gex_recorder_enabled and settings.upstox_token_value() is not None:
        from app.options.gex_scheduler import build_scheduler

        gex = build_scheduler()
        await gex.start()
    options_rec = None
    if settings.options_recorder_enabled and settings.upstox_token_value() is not None:
        from app.options.chain_scheduler import build_scheduler as build_options_scheduler

        options_rec = build_options_scheduler()
        await options_rec.start()
    try:
        yield
    finally:
        if options_rec is not None:
            await options_rec.stop()
        if gex is not None:
            await gex.stop()
        if live is not None:
            await live.stop()
        clear(own_path)


app = FastAPI(title="Chart Analyser API", version="0.1.0", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(backtests_router)
app.include_router(candles_router)
app.include_router(indicators_router)
app.include_router(drawings_router)
app.include_router(fvg_router)
app.include_router(gex_router)
app.include_router(options_router)
app.include_router(market_view_router)
app.include_router(upstox_router)
app.include_router(live_router)
app.include_router(paper_router)
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
