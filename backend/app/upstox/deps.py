"""Process-wide Upstox singletons (FastAPI dependencies, overridable in tests)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.data.history import SyncProgress, SyncResult, symbol_dir_name, sync_symbol
from app.data.jobs import JobManager, Runner
from app.upstox.client import UpstoxClient
from app.upstox.instruments import Instrument
from app.upstox.ratelimit import SlidingWindowLimiter
from app.upstox.status import StatusCache
from app.upstox.token import DataToken


@lru_cache
def get_limiter() -> SlidingWindowLimiter:
    """ONE limiter for every Upstox call made by this process."""
    return SlidingWindowLimiter(
        [
            (settings.upstox_rate_per_second, 1.0),
            (settings.upstox_rate_per_minute, 60.0),
            (settings.upstox_rate_per_half_hour, 1800.0),
        ]
    )


def make_client(*, max_attempts: int = 5) -> UpstoxClient | None:
    value = settings.upstox_token_value()
    if value is None:
        return None
    return UpstoxClient(DataToken(value), limiter=get_limiter(), max_attempts=max_attempts)


def get_client() -> UpstoxClient | None:
    return make_client()


@lru_cache
def get_status_cache() -> StatusCache:
    return StatusCache()


def get_candles_dir() -> Path:
    return settings.candles_dir


def get_instruments_dir() -> Path:
    return settings.instruments_dir


def _runner() -> Runner | None:
    client = get_client()
    if client is None:
        return None
    candles_dir = get_candles_dir()

    def run(
        instrument: Instrument,
        from_date: date | None,
        to_date: date | None,
        on_progress: Callable[[SyncProgress], None],
    ) -> SyncResult:
        return sync_symbol(
            client, candles_dir, instrument, from_date=from_date, to_date=to_date, on_progress=on_progress
        )

    return run


@lru_cache
def get_jobs() -> JobManager:
    return JobManager(_runner, symbol_dir_name, on_auth_error=get_status_cache().invalidate)
