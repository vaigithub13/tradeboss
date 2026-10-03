"""Load stored candles, ask the model, and score the saved file. No order is sent."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from app.ai.analyse import analyse
from app.ai.client import OpenAIAnalysisClient, require_key
from app.ai.context import build_context
from app.ai.cost import analysis_model, cost_label
from app.ai.record import HORIZONS, hit_rates, load_analyses, save_analysis, score_analysis
from app.config import settings
from app.data.service import TimeframeUnavailable, UnknownTimeframe, get_candle_page, get_candles
from app.data.store import CandleStore, SymbolNotFound
from app.options.events import load_default_events
from app.pine.openai_client import OpenAIError
from app.upstox.deps import get_client

VIX_SYMBOL = "NSE_INDEX_India_VIX"
PAGE = 80


class AnalysisRunError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


def analysis_dir():
    return settings.data_dir / "ai"


class _Chain:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_json(self, path: str, params: dict) -> dict:
        return self._client._get(path, {key: str(value) for key, value in params.items()})


def _rows(store: CandleStore, symbol: str, timeframe: str, sessions: list[str]) -> list[dict]:
    page = get_candle_page(store, symbol, timeframe, limit=PAGE, session_types=sessions)
    return [dict(candle) for candle in page.candles]


def run_analysis(symbol: str, sessions: list[str], image_base64: str | None) -> dict[str, Any]:
    image = _image(image_base64)
    store = CandleStore(settings.candles_dir)
    try:
        candles = {name: _rows(store, symbol, name, sessions) for name in ("5m", "15m", "1h", "1D")}
    except SymbolNotFound as exc:
        raise AnalysisRunError(404, f"unknown symbol {symbol}") from exc
    if not candles["5m"]:
        raise AnalysisRunError(400, f"no candles for {symbol}")
    try:
        vix = _rows(store, VIX_SYMBOL, "1m", sessions)
    except (SymbolNotFound, TimeframeUnavailable, UnknownTimeframe):
        vix = []
    client = get_client()
    context = build_context(
        symbol=symbol,
        as_of=int(candles["5m"][-1]["time"]),
        candles=candles,
        vix=vix,
        events=load_default_events(),
        chain=_Chain(client) if client is not None else None,
    )
    try:
        result = analyse(context, client=OpenAIAnalysisClient(require_key()), image=image)
    except OpenAIError as exc:
        raise AnalysisRunError(502, str(exc)) from exc
    saved_id = None
    if result["ready"]:
        saved = save_analysis(analysis_dir(), context=context, analysis=result["analysis"])
        saved_id = saved["id"]
    error = None if result["ready"] else (result["errors"][-1] if result["errors"] else "analysis was not ready")
    return {
        "ready": result["ready"],
        "id": saved_id,
        "analysis": result["analysis"],
        "error": error,
        "usage": result["usage"],
        "cost": cost_label(result["usage"]),
        "model": analysis_model(),
        "attempts": result["attempts"],
    }


def track_symbol(symbol: str, sessions: list[str]) -> dict[str, Any]:
    records = [row for row in load_analyses(analysis_dir(), symbol)]
    empty = {
        horizon: {
            "scored": 0,
            "ai": {"bias": None, "triggers": None, "levels": None},
            "always_bullish": {"bias": None},
            "follow_trend": {"bias": None},
        }
        for horizon in HORIZONS
    }
    if not records:
        return {"symbol": symbol, "horizons": empty}
    store = CandleStore(settings.candles_dir)
    earliest = min(int(row["time"]) for row in records)
    try:
        loaded = get_candles(store, symbol, "5m", from_time=earliest, session_types=sessions)
    except (SymbolNotFound, TimeframeUnavailable, UnknownTimeframe, ValueError):
        return {"symbol": symbol, "horizons": empty}
    bars = [dict(candle) for candle in loaded.candles]
    scores = []
    for record in records:
        for horizon in HORIZONS:
            scores.append(score_analysis(record, bars, horizon=horizon))
    rates = hit_rates(scores)
    for horizon, block in rates.items():
        empty[horizon] = block
    return {"symbol": symbol, "horizons": empty}


def _image(encoded: str | None) -> bytes | None:
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AnalysisRunError(400, "screenshot is not valid base64") from exc
    if len(raw) > 1_500_000:
        raise AnalysisRunError(400, "screenshot is too large")
    return raw
