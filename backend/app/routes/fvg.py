"""Fair value gap boxes for the chart. Same `fvg_boxes` a backtest calls."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.data.service import TimeframeUnavailable, UnknownSessionType, UnknownTimeframe, get_candles
from app.data.store import SymbolNotFound
from app.indicators.frame import candles_to_frame
from app.indicators.fvg import bar_close_seconds, fvg_boxes
from app.indicators.registry import validate_params
from app.routes.candles import DefaultSessionsDep, StoreDep

router = APIRouter(prefix="/api")


class FvgRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    symbol: str
    timeframe: str
    sessions: list[str] | None = None
    cursor: int | None = None
    chart_last: int | None = Field(default=None, description="start time of the last chart bar on screen")
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class FvgBoxOut(BaseModel):
    direction: str
    start_time: int
    formed_time: int
    end_time: int | None
    bottom: float
    top: float
    extends: bool
    faded: bool
    mitigated: bool


class FvgResponse(BaseModel):
    symbol: str
    timeframe: str
    boxes: list[FvgBoxOut]


def _as_of(chart_tf: str, chart_last: int | None, cursor: int | None) -> int | None:
    """Latest moment the chart is allowed to know.

    Replay uses the cursor. Live uses the last bar's close once that bar has finished,
    and the last bar's start while it is still forming.
    """
    if cursor is not None:
        return cursor
    if chart_last is None:
        return None
    close = bar_close_seconds(chart_tf)
    if int(time.time()) < chart_last + close:
        return chart_last
    return chart_last + close


@router.post("/fvg", response_model=FvgResponse)
def fair_value_gaps(
    req: FvgRequest, store: StoreDep, default_sessions: DefaultSessionsDep
) -> FvgResponse:
    types = list(req.sessions) if req.sessions is not None else list(default_sessions)
    try:
        clean = validate_params("fvg", req.params)
        fvg_tf = str(clean["timeframe"] or req.timeframe)
        from_time = None if req.from_ is None else req.from_ - 7 * 86_400
        result = get_candles(
            store, req.symbol, fvg_tf,
            from_time=from_time, to_time=req.to, session_types=types, cursor=req.cursor,
        )
        frame = candles_to_frame(result.candles)
        boxes = fvg_boxes(
            frame,
            {**clean, "bar_seconds": bar_close_seconds(fvg_tf), "as_of": _as_of(req.timeframe, req.chart_last, req.cursor)},
        )
    except SymbolNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown symbol {req.symbol!r}") from None
    except (ValueError, UnknownTimeframe, TimeframeUnavailable, UnknownSessionType) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    times = frame["time"].tolist()
    out: list[FvgBoxOut] = []
    for box in boxes:
        end = box["end_index"]
        out.append(
            FvgBoxOut(
                direction=str(box["direction"]),
                start_time=int(times[box["start_index"]]),
                formed_time=int(times[box["formed_index"]]),
                end_time=None if end is None else int(times[end]),
                bottom=float(box["bottom"]),
                top=float(box["top"]),
                extends=bool(box["extends"]),
                faded=bool(box["faded"]),
                mitigated=bool(box["mitigated"]),
            )
        )
    return FvgResponse(symbol=req.symbol, timeframe=fvg_tf, boxes=out)
