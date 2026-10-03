from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.data.service import TimeframeUnavailable, UnknownSessionType, UnknownTimeframe
from app.data.store import SymbolNotFound
from app.indicators.registry import IndicatorSpec, validate_params
from app.indicators.service import IndicatorNotAvailable, compute_indicators
from app.indicators.volume import VolumeRequired
from app.routes.candles import DefaultSessionsDep, StoreDep

router = APIRouter(prefix="/api")


class IndicatorIn(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class IndicatorsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    symbol: str
    timeframe: str
    from_: int | None = Field(default=None, alias="from", description="unix seconds, inclusive")
    to: int | None = Field(default=None, description="unix seconds, inclusive")
    sessions: list[str] | None = Field(
        default=None, description="session types to include; default = server setting"
    )
    indicators: list[IndicatorIn] = Field(max_length=50)
    cursor: int | None = Field(default=None, description="replay: no bar after this unix time")


class IndicatorOut(BaseModel):
    id: str
    type: str
    params: dict[str, Any]
    outputs: dict[str, list[float | None]]


class IndicatorsResponse(BaseModel):
    symbol: str
    timeframe: str
    sessions: list[str]
    times: list[int]
    indicators: list[IndicatorOut]


@router.post("/indicators", response_model=IndicatorsResponse)
def indicators(
    req: IndicatorsRequest, store: StoreDep, default_sessions: DefaultSessionsDep
) -> IndicatorsResponse:
    """Indicator values aligned to the chart's candles (`times`), NaN -> null.

    Computed on the same session-filtered series as GET /api/candles, with extra warm-up
    history loaded before `from` so the first visible values are converged.
    """
    types = list(req.sessions) if req.sessions is not None else list(default_sessions)
    try:
        specs = [
            IndicatorSpec(id=i.id, type=i.type, params=validate_params(i.type, i.params))
            for i in req.indicators
        ]
        result = compute_indicators(
            store, req.symbol, req.timeframe, specs,
            from_time=req.from_, to_time=req.to, session_types=types, cursor=req.cursor,
        )  # fmt: skip
    except SymbolNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown symbol {req.symbol!r}") from None
    except (
        ValueError,  # bad params / duplicate ids (also the base of the errors below)
        UnknownTimeframe,
        TimeframeUnavailable,
        UnknownSessionType,
        IndicatorNotAvailable,
        VolumeRequired,
    ) as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return IndicatorsResponse(
        symbol=req.symbol,
        timeframe=req.timeframe,
        sessions=types,
        times=result.times,
        indicators=[
            IndicatorOut(id=o.id, type=o.type, params=o.params, outputs=o.outputs)
            for o in result.indicators
        ],
    )
