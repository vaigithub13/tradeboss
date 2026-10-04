"""compute_indicators = candles (with warm-up history) + registry math, sliced to the visible range.

The math runs on exactly the same session-filtered, resampled series the chart shows
(`get_candles`), so what you see on the chart is what a backtest would compute.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from app.data.service import UnknownTimeframe, get_candles
from app.data.sessions import DEFAULT_INCLUDED_SESSION_TYPES
from app.data.store import CandleStore
from app.indicators.frame import candles_to_frame
from app.indicators.futures_vwap import NIFTY_SYMBOL, values_for_chart
from app.indicators.registry import IndicatorSpec, compute, warmup_bars

_TIMEFRAME_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1D": 1440, "1W": 10080}
_LOOKBACK_FACTOR = 6  # a trading day is ~6.4h of 24h, plus weekends/holidays: start generous, then grow


class IndicatorNotAvailable(ValueError):
    """The indicator cannot be computed on this timeframe (e.g. VWAP on 1D/1W)."""


@dataclass(frozen=True)
class IndicatorOutput:
    id: str
    type: str
    params: dict[str, object]
    outputs: dict[str, list[float | None]]


@dataclass(frozen=True)
class IndicatorsResult:
    times: list[int]
    indicators: list[IndicatorOutput] = field(default_factory=list)


def _to_json_list(values: np.ndarray) -> list[float | None]:
    return [None if v != v else float(v) for v in values.tolist()]


def compute_indicators(
    store: CandleStore,
    symbol: str,
    timeframe: str,
    specs: Sequence[IndicatorSpec],
    *,
    from_time: int | None = None,
    to_time: int | None = None,
    session_types: Iterable[str] = DEFAULT_INCLUDED_SESSION_TYPES,
    cursor: int | None = None,
) -> IndicatorsResult:
    """Indicator values for every candle whose start lies in [from_time, to_time].

    Extra history before `from_time` is loaded for warm-up (as much as the data has, up to each
    indicator's warm-up rule) and discarded, so the first visible values are fully converged.
    Raises ValueError (duplicate ids), IndicatorNotAvailable, VolumeRequired, plus whatever
    `get_candles` raises (SymbolNotFound, TimeframeUnavailable, UnknownSessionType, ...).
    """
    ids = [s.id for s in specs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate indicator id(s): {sorted({i for i in ids if ids.count(i) > 1})}")
    types = list(session_types)
    bar_minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if bar_minutes is None:
        raise UnknownTimeframe(f"Unknown timeframe {timeframe!r}; expected one of {list(_TIMEFRAME_MINUTES)}")
    if timeframe in ("1D", "1W") and any(s.type in ("vwap", "vwap_fut") for s in specs):
        raise IndicatorNotAvailable("VWAP is intraday only: it resets daily, so it is not available on 1D/1W")
    if any(s.type == "vwap_fut" for s in specs) and symbol != NIFTY_SYMBOL:
        raise IndicatorNotAvailable("VWAP (futures volume) is only for the Nifty index")

    if cursor is not None and (to_time is None or to_time > cursor):
        to_time = cursor
    candles = _load_with_warmup(
        store, symbol, timeframe, types, specs, bar_minutes, from_time, to_time, cursor
    )
    frame = candles_to_frame(candles)
    times = frame["time"].tolist()
    first = bisect.bisect_left(times, from_time) if from_time is not None else 0

    outputs: list[IndicatorOutput] = []
    for s in specs:
        if s.type == "vwap_fut":
            projected = values_for_chart(
                store, candles, s.params, types, bar_minutes * 60, to_time=to_time, cursor=cursor,
            )
            values = {"vwap": projected}
        else:
            values = compute(frame, s.type, s.params)  # may raise VolumeRequired
        outputs.append(
            IndicatorOutput(
                id=s.id,
                type=s.type,
                params=dict(s.params),
                outputs={name: _to_json_list(v[first:]) for name, v in values.items()},
            )
        )
    visible = times[first:]
    if cursor is not None and any(t > cursor for t in visible):
        raise RuntimeError("indicator response contains a bar after the cursor")
    return IndicatorsResult(times=visible, indicators=outputs)


def _load_with_warmup(
    store: CandleStore,
    symbol: str,
    timeframe: str,
    types: list[str],
    specs: Sequence[IndicatorSpec],
    bar_minutes: int,
    from_time: int | None,
    to_time: int | None,
    cursor: int | None = None,
) -> list:
    if from_time is None or not specs:
        return get_candles(
            store, symbol, timeframe, to_time=to_time, session_types=types, cursor=cursor
        ).candles
    needed = max(warmup_bars(s.type, s.params, bar_minutes) for s in specs)
    data_start, _ = store.time_range(symbol)
    bar_seconds = bar_minutes * 60
    lookback = needed * bar_seconds * _LOOKBACK_FACTOR
    while True:
        load_from = from_time - lookback
        candles = get_candles(
            store, symbol, timeframe, from_time=load_from, to_time=to_time, session_types=types,
            cursor=cursor,
        ).candles
        before = sum(1 for c in candles if c["time"] < from_time)
        if before >= needed or load_from <= data_start:
            return candles
        lookback *= 2
