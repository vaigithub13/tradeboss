"""get_candles = store + resampler + range / Muhurat rules."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.data.candle import Candle
from app.data.resampler import (
    DAY_S,
    IST_OFFSET_S,
    TIMEFRAMES,
    available_timeframes,
    resample,
)
from app.data.sessions import DEFAULT_INCLUDED_SESSION_TYPES, SESSION_TYPES
from app.data.store import CandleStore


class UnknownTimeframe(ValueError):
    pass


class TimeframeUnavailable(ValueError):
    pass


class UnknownSessionType(ValueError):
    pass


@dataclass(frozen=True)
class CandleResult:
    candles: list[Candle]
    source_minutes: int


@dataclass(frozen=True)
class CandlePage:
    """The newest `limit` candles before some time, plus whether older ones exist."""

    candles: list[Candle]
    source_minutes: int
    has_more: bool


def timeframe_seconds(timeframe: str) -> int:
    """Nominal bar length (an upper bound for trading-time bars): used to size look-backs."""
    if timeframe == "1D":
        return DAY_S
    if timeframe == "1W":
        return 7 * DAY_S
    if timeframe in TIMEFRAMES:
        return int(timeframe[:-1]) * 60 if timeframe.endswith("m") else 3600
    raise UnknownTimeframe(f"Unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")


def _bucket_start(t: int, timeframe: str) -> int:
    """Start (unix s) of the IST day / Mon-Sun week containing t."""
    day = (t + IST_OFFSET_S) // DAY_S
    if timeframe == "1W":
        day -= (day + 3) % 7
    return day * DAY_S - IST_OFFSET_S


def _bucket_end(t: int, timeframe: str) -> int:
    """Last second of the IST day / Mon-Sun week containing t."""
    day = (t + IST_OFFSET_S) // DAY_S
    if timeframe == "1W":
        day += 6 - (day + 3) % 7
    return (day + 1) * DAY_S - IST_OFFSET_S - 1


def get_candles(
    store: CandleStore,
    symbol: str,
    timeframe: str,
    *,
    from_time: int | None = None,
    to_time: int | None = None,
    session_types: Iterable[str] = DEFAULT_INCLUDED_SESSION_TYPES,
    cursor: int | None = None,
) -> CandleResult:
    """Candles whose START time lies in [from_time, to_time] (unix seconds).

    Source bars are loaded for the whole IST day / week around the range so edge
    candles are complete, then the result is clipped on candle start time.
    Only sessions whose label is in `session_types` are used. Muhurat sessions (if
    included) are anchored to their own first bar.
    Raises SymbolNotFound, UnknownTimeframe, TimeframeUnavailable, UnknownSessionType.
    """
    if timeframe not in TIMEFRAMES:
        raise UnknownTimeframe(f"Unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")
    types = list(session_types)
    unknown = [t for t in types if t not in SESSION_TYPES]
    if unknown:
        raise UnknownSessionType(
            f"Unknown session type(s) {unknown}; expected any of {list(SESSION_TYPES)}"
        )
    base = store.base_minutes(symbol)
    if timeframe not in available_timeframes(base):
        raise TimeframeUnavailable(
            f"{timeframe} is not available: stored data for {symbol} is {base}m "
            f"(candles are never fabricated). Available: {list(available_timeframes(base))}"
        )

    if cursor is not None and (to_time is None or to_time > cursor):
        to_time = cursor
    load_from = None if from_time is None else _bucket_start(from_time, timeframe)
    # A replay cursor clips source minutes before resample, so a forming bar cannot
    # contain a later minute. The ordinary path still loads the whole edge bucket.
    load_to = cursor if cursor is not None else (None if to_time is None else _bucket_end(to_time, timeframe))
    source, source_minutes = store.load(
        symbol, from_time=load_from, to_time=load_to, session_types=types
    )
    anchored = store.dates_of_type(symbol, "muhurat") if "muhurat" in types else frozenset()
    if cursor is not None:
        from app.replay.cursor import replay_bars

        out = replay_bars(
            source, timeframe, cursor, source_minutes, anchor_to_first_bar_dates=anchored
        )
    else:
        out = resample(
            source, timeframe, source_minutes, anchor_to_first_bar_dates=anchored
        )
    if from_time is not None:
        out = [c for c in out if c["time"] >= from_time]
    if to_time is not None:
        out = [c for c in out if c["time"] <= to_time]
    if cursor is not None and any(c["time"] > cursor for c in out):
        raise RuntimeError("replay response contains a bar after the cursor")
    return CandleResult(candles=out, source_minutes=source_minutes)


def get_candle_page_after(
    store: CandleStore,
    symbol: str,
    timeframe: str,
    *,
    limit: int,
    after: int,
    session_types: Iterable[str] = DEFAULT_INCLUDED_SESSION_TYPES,
    cursor: int | None = None,
) -> CandlePage:
    """The OLDEST `limit` candles whose start time is > `after`; `has_more` = newer ones exist.

    Mirror image of get_candle_page: used by the chart window, which drops far-away bars and
    asks for them again when the user scrolls back toward them."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    types = list(session_types)
    bar_s = timeframe_seconds(timeframe)
    _, data_end = store.time_range(symbol)  # raises SymbolNotFound
    if cursor is not None:
        data_end = min(data_end, cursor)
    from_time = after + 1
    lookback = (limit + 1) * bar_s * 6
    while True:
        to_time = min(data_end, from_time + lookback)
        result = get_candles(
            store, symbol, timeframe, from_time=from_time, to_time=to_time, session_types=types,
            cursor=cursor,
        )
        if len(result.candles) > limit or to_time >= data_end:
            break
        lookback *= 2
    return CandlePage(
        candles=result.candles[:limit],
        source_minutes=result.source_minutes,
        has_more=len(result.candles) > limit,
    )


def get_candle_page(
    store: CandleStore,
    symbol: str,
    timeframe: str,
    *,
    limit: int,
    before: int | None = None,
    session_types: Iterable[str] = DEFAULT_INCLUDED_SESSION_TYPES,
    cursor: int | None = None,
) -> CandlePage:
    """The newest `limit` candles whose start time is < `before` (None = the very latest).

    Used for lazy loading: the chart opens on the newest page and asks for older pages as the
    user scrolls left. Each page is cut from the same resampled series as `get_candles`, so
    pages stitch together exactly. `has_more` tells whether older candles exist.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    types = list(session_types)
    bar_s = timeframe_seconds(timeframe)
    data_start, data_end = store.time_range(symbol)  # raises SymbolNotFound
    to_time = data_end if before is None else before - 1
    if cursor is not None:
        to_time = min(to_time, cursor)
    lookback = (limit + 1) * bar_s * 6  # trading hours are ~1/4 of the clock: start generous, grow
    while True:
        from_time = to_time - lookback
        result = get_candles(
            store, symbol, timeframe, from_time=from_time, to_time=to_time, session_types=types,
            cursor=cursor,
        )
        if len(result.candles) > limit or from_time <= data_start:
            break
        lookback *= 2
    candles = result.candles[-limit:]
    return CandlePage(
        candles=candles,
        source_minutes=result.source_minutes,
        has_more=len(result.candles) > limit,
    )
