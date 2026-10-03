from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import settings
from app.data.resampler import TIMEFRAMES, available_timeframes
from app.data.service import (
    TimeframeUnavailable,
    UnknownSessionType,
    UnknownTimeframe,
    get_candle_page,
    get_candle_page_after,
    get_candles,
)
from app.data.sessions import SESSION_TYPES
from app.data.store import CandleStore, SymbolNotFound

router = APIRouter(prefix="/api")


class CandleOut(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float | None


class CandlesResponse(BaseModel):
    symbol: str
    timeframe: str
    source_minutes: int
    sessions: list[str]
    #: with `limit`: are there older candles than the first one returned?
    has_more: bool
    #: with `after`: are there candles newer than the last one returned?
    has_more_newer: bool = False
    candles: list[CandleOut]


class SymbolInfo(BaseModel):
    symbol: str
    #: human-readable name for the legend (e.g. "Nifty 50", "RELIANCE"); falls back to `symbol`
    display_name: str
    instrument_key: str | None
    kind: str | None
    base_timeframe: str
    available_timeframes: list[str]
    first_time: int
    last_time: int


class SymbolsResponse(BaseModel):
    default_sessions: list[str]
    session_types: list[str]
    timeframes: list[str]
    symbols: list[SymbolInfo]


@lru_cache
def get_store() -> CandleStore:
    return CandleStore(settings.data_dir / "candles")


def get_default_sessions() -> tuple[str, ...]:
    return settings.default_sessions


StoreDep = Annotated[CandleStore, Depends(get_store)]
DefaultSessionsDep = Annotated[tuple[str, ...], Depends(get_default_sessions)]


def parse_sessions(raw: str | None, default: tuple[str, ...]) -> list[str]:
    """`sessions` query value: comma-separated session types; None -> server default."""
    if raw is None:
        return list(default)
    return [t.strip() for t in raw.split(",") if t.strip()]


@router.get("/candles", response_model=CandlesResponse)
def candles(
    store: StoreDep,
    default_sessions: DefaultSessionsDep,
    symbol: str,
    timeframe: str,
    from_: Annotated[int | None, Query(alias="from", description="unix seconds, inclusive")] = None,
    to: Annotated[int | None, Query(description="unix seconds, inclusive")] = None,
    sessions: Annotated[
        str | None,
        Query(description="comma-separated session types to include; default = server setting"),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=200_000, description="newest N candles (lazy loading); omit for the whole range"),
    ] = None,
    before: Annotated[
        int | None,
        Query(description="only candles that START before this unix time (exclusive); pairs with limit"),
    ] = None,
    after: Annotated[
        int | None,
        Query(description="with limit: the OLDEST N candles that start after this unix time (exclusive)"),
    ] = None,
    cursor: Annotated[
        int | None,
        Query(description="replay: no source minute after this unix time is resampled"),
    ] = None,
) -> CandlesResponse:
    """Candles whose start time is within [from, to]; resampled from stored base data.

    With `limit`, returns the newest `limit` candles (older than `before` if given) and `has_more`.
    """
    types = parse_sessions(sessions, default_sessions)
    has_more = False
    has_more_newer = False
    try:
        if cursor is not None and after is not None and after >= cursor:
            result_candles, source_minutes = [], store.base_minutes(symbol)
        elif after is not None:
            if limit is None or from_ is not None or to is not None or before is not None:
                raise HTTPException(status_code=422, detail="after needs limit and cannot be combined with from/to/before")
            fwd = get_candle_page_after(
                store, symbol, timeframe, limit=limit, after=after, session_types=types, cursor=cursor
            )
            result_candles, source_minutes, has_more_newer = fwd.candles, fwd.source_minutes, fwd.has_more
        elif limit is not None:
            if from_ is not None or to is not None:
                raise HTTPException(status_code=422, detail="limit cannot be combined with from/to; use before")
            page = get_candle_page(
                store, symbol, timeframe, limit=limit, before=before, session_types=types, cursor=cursor
            )
            result_candles, source_minutes, has_more = page.candles, page.source_minutes, page.has_more
        else:
            upper = to if before is None else (before - 1 if to is None else min(to, before - 1))
            if cursor is not None:
                upper = cursor if upper is None else min(upper, cursor)
            result = get_candles(
                store, symbol, timeframe, from_time=from_, to_time=upper, session_types=types, cursor=cursor
            )
            result_candles, source_minutes = result.candles, result.source_minutes
        if cursor is not None and any(c["time"] > cursor for c in result_candles):
            raise HTTPException(status_code=500, detail="replay response contains a bar after the cursor")
    except SymbolNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown symbol {symbol!r}") from None
    except (UnknownTimeframe, TimeframeUnavailable, UnknownSessionType) as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return CandlesResponse(
        symbol=symbol,
        timeframe=timeframe,
        source_minutes=source_minutes,
        sessions=types,
        has_more=has_more,
        has_more_newer=has_more_newer,
        candles=[CandleOut(**c) for c in result_candles],
    )


@router.get("/symbols", response_model=SymbolsResponse)
def symbols(store: StoreDep, default_sessions: DefaultSessionsDep) -> SymbolsResponse:
    infos: list[SymbolInfo] = []
    for sym in store.symbols():
        base = store.base_minutes(sym)
        first, last = store.time_range(sym)
        inst = store.meta(sym).instrument
        infos.append(
            SymbolInfo(
                symbol=sym,
                display_name=str(inst.get("symbol") or inst.get("name") or sym),
                instrument_key=inst.get("instrument_key"),
                kind=inst.get("kind"),
                base_timeframe=f"{base}m",
                available_timeframes=list(available_timeframes(base)),
                first_time=first,
                last_time=last,
            )
        )
    return SymbolsResponse(
        default_sessions=list(default_sessions),
        session_types=list(SESSION_TYPES),
        timeframes=list(TIMEFRAMES),
        symbols=infos,
    )
