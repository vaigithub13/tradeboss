"""Read-only Upstox endpoints: data-token status, instrument search, history sync jobs.

There is NO order endpoint here (or anywhere in this phase)."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import settings
from app.data.history import MIN_HISTORY_DATE, read_meta, symbol_dir_name
from app.data.jobs import JobManager
from app.data.store import CandleStore
from app.upstox.client import UpstoxClient
from app.upstox.deps import (
    get_candles_dir,
    get_instruments_dir,
    get_jobs,
    get_status_cache,
    make_client,
)
from app.upstox.instruments import (
    KINDS,
    NIFTY_INDEX_KEY,
    VIX_KEY,
    Instrument,
    InstrumentIndex,
    SnapshotError,
    current_index,
    take_snapshot,
)
from app.upstox.status import StatusCache, TokenStatus, check_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

StatusCacheDep = Annotated[StatusCache, Depends(get_status_cache)]
InstrumentsDirDep = Annotated[Path, Depends(get_instruments_dir)]
CandlesDirDep = Annotated[Path, Depends(get_candles_dir)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]

IST = timezone(timedelta(hours=5, minutes=30))

#: first fetch of an instrument that is not Nifty 50 / VIX: only this much history (more on demand)
DEFAULT_STOCK_DAYS = 90
DEFAULT_FUTURE_DAYS = 120


# ------------------------------------------------------------------ data token
class TokenStatusOut(BaseModel):
    state: Literal["valid", "invalid", "expired", "missing", "unreachable"]
    message: str
    expires_at: str | None
    days_left: int | None
    expires_soon: bool
    market_status: str | None
    checked_at: str | None


def _status_client() -> UpstoxClient:
    c = make_client(max_attempts=2)
    assert c is not None
    return c


@router.get("/upstox/status", response_model=TokenStatusOut)
def upstox_status(
    cache: StatusCacheDep,
    refresh: Annotated[bool, Query(description="bypass the 5-minute cache")] = False,
) -> TokenStatusOut:
    token = settings.upstox_token_value()
    status: TokenStatus = cache.get(lambda: check_token(token, _status_client), refresh=refresh)
    return TokenStatusOut(**asdict(status))


# ------------------------------------------------------------------ instruments
class InstrumentOut(BaseModel):
    instrument_key: str
    symbol: str
    name: str
    kind: Literal["index", "equity", "future", "option"]
    segment: str
    instrument_type: str
    expiry: str | None
    strike: float | None
    lot_size: int | None
    underlying_key: str | None
    #: folder / symbol id the chart uses for this instrument
    symbol_id: str
    #: stored candles exist (any timeframe)
    has_data: bool
    #: 1m candles exist (the 1m / 3m buttons work)
    has_1m: bool


class InstrumentSearchResponse(BaseModel):
    snapshot_date: str | None
    message: str | None
    items: list[InstrumentOut]


def _stored(candles_dir: Path) -> dict[str, int]:
    """symbol folder -> base bar minutes, for every stored symbol."""
    store = CandleStore(candles_dir)
    return {s: store.base_minutes(s) for s in store.symbols()}


def _instrument_out(i: Instrument, stored: dict[str, int]) -> InstrumentOut:
    sid = symbol_dir_name(i.key)
    base = stored.get(sid)
    return InstrumentOut(
        **i.as_dict(), symbol_id=sid, has_data=base is not None, has_1m=base == 1
    )


@router.get("/instruments/search", response_model=InstrumentSearchResponse)
def instruments_search(
    instruments_dir: InstrumentsDirDep,
    candles_dir: CandlesDirDep,
    q: str = "",
    kind: Annotated[str | None, Query(description=f"one of {list(KINDS)}")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> InstrumentSearchResponse:
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(KINDS)}")
    index = current_index(instruments_dir)
    if index is None:
        return InstrumentSearchResponse(
            snapshot_date=None,
            message="No instrument file yet. It is downloaded automatically after 06:30 IST "
            "(or run: uv run python -m scripts.snapshot_instruments).",
            items=[],
        )
    stored = _stored(candles_dir)
    found = index.search(
        q,
        kind=kind,  # type: ignore[arg-type]
        limit=limit,
        prefer=lambda i: symbol_dir_name(i.key) in stored,  # empty query: stored symbols first
    )
    return InstrumentSearchResponse(
        snapshot_date=index.snapshot_day.isoformat() if index.snapshot_day else None,
        message=None,
        items=[_instrument_out(i, stored) for i in found],
    )


@router.post("/instruments/snapshot")
def instruments_snapshot(instruments_dir: InstrumentsDirDep) -> dict[str, Any]:
    """Download today's public instrument file now (no token needed)."""
    try:
        result = take_snapshot(instruments_dir)
    except (SnapshotError, httpx.HTTPError) as e:
        raise HTTPException(status_code=502, detail=f"instrument download failed: {type(e).__name__}") from None
    return {"date": result.day.isoformat(), "created": result.created, "outcome": result.outcome}


# ------------------------------------------------------------------ history sync
class SyncRequest(BaseModel):
    instrument_key: str
    #: first IST date wanted (inclusive); default depends on the instrument
    from_date: date | None = None


class JobOut(BaseModel):
    id: str
    instrument_key: str
    symbol: str
    status: Literal["running", "done", "error"]
    windows_total: int
    windows_done: int
    bars_added: int
    message: str
    error: str | None
    auth_error: bool
    started_at: str
    finished_at: str | None


def default_start(instrument: Instrument, today: date) -> date:
    if instrument.key in (NIFTY_INDEX_KEY, VIX_KEY):
        return MIN_HISTORY_DATE
    if instrument.kind == "future":
        return today - timedelta(days=DEFAULT_FUTURE_DAYS)
    return today - timedelta(days=DEFAULT_STOCK_DAYS)


def _index_or_404(instruments_dir: Path) -> InstrumentIndex:
    index = current_index(instruments_dir)
    if index is None:
        raise HTTPException(status_code=409, detail="No instrument file yet; take a snapshot first.")
    return index


@router.post("/history/sync", response_model=JobOut)
def history_sync(body: SyncRequest, instruments_dir: InstrumentsDirDep, jobs: JobsDep) -> JobOut:
    """Fetch the missing 1m candles for an instrument (background job; poll /history/jobs/{id})."""
    instrument = _index_or_404(instruments_dir).get(body.instrument_key)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Unknown instrument {body.instrument_key!r}")
    today = datetime.now(IST).date()
    start = body.from_date or default_start(instrument, today)
    if start < MIN_HISTORY_DATE:
        start = MIN_HISTORY_DATE
    if start > today:
        raise HTTPException(status_code=422, detail="from_date is in the future")
    return JobOut(**jobs.start(instrument, start, None).as_dict())


@router.get("/history/jobs/{job_id}", response_model=JobOut)
def history_job(job_id: str, jobs: JobsDep) -> JobOut:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return JobOut(**job.as_dict())


@router.get("/history/coverage")
def history_coverage(
    candles_dir: CandlesDirDep, instrument_key: Annotated[str, Query()]
) -> dict[str, Any]:
    """1m date ranges already fetched for an instrument (for 'load more history')."""
    meta = read_meta(candles_dir / symbol_dir_name(instrument_key))
    return {
        "instrument_key": instrument_key,
        "symbol_id": symbol_dir_name(instrument_key),
        "covered": [[a.isoformat(), b.isoformat()] for a, b in meta.covered],
        "min_history_date": MIN_HISTORY_DATE.isoformat(),
    }
