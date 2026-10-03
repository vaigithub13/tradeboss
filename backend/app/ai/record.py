"""Save an analysis and score it once each horizon has happened."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.ai.context import context_hash
from app.ai.cost import neutral_band

IST = ZoneInfo("Asia/Kolkata")
HORIZONS = ("60m", "session_close", "1", "3", "5")
SESSION_CLOSE_MINUTE = 15 * 60 + 15


def save_analysis(directory: Path, *, context: dict, analysis: dict) -> dict[str, Any]:
    record = {
        "id": uuid.uuid4().hex,
        "time": context["as_of"],
        "symbol": context["symbol"],
        "context": context,
        "analysis": analysis,
        "context_hash": context_hash(context),
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record['id']}.json").write_text(json.dumps(record))
    return record


def load_analyses(directory: Path, symbol: str | None = None) -> list[dict]:
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        row = json.loads(path.read_text())
        if symbol is None or row.get("symbol") == symbol:
            rows.append(row)
    return rows


def _ist_date(timestamp: int):
    return datetime.fromtimestamp(int(timestamp), IST).date()


def _minute(timestamp: int) -> int:
    when = datetime.fromtimestamp(int(timestamp), IST)
    return when.hour * 60 + when.minute


def _classify(price: float, close: float, band: float) -> str:
    if price == 0:
        return "flat"
    change = (close - price) / price
    if abs(change) <= band:
        return "flat"
    return "up" if change > 0 else "down"


def _direction_right(bias: str, move: str) -> bool:
    if bias == "neutral":
        return move == "flat"
    if bias == "bull":
        return move == "up"
    if bias == "bear":
        return move == "down"
    return False


def trend_bias(context: dict) -> str:
    """Sign of the 1D EMA 20 slope. Missing values are neutral."""
    indicators = ((context.get("timeframes") or {}).get("1D") or {}).get("indicators") or {}
    last = indicators.get("ema20")
    prev = indicators.get("ema20_prev")
    if last is None or prev is None:
        return "neutral"
    if last > prev:
        return "bull"
    if last < prev:
        return "bear"
    return "neutral"


def _horizon_bar(as_of: int, later_bars: list[dict], horizon: str) -> dict | None:
    ordered = sorted(later_bars, key=lambda bar: bar["time"])
    if horizon == "60m":
        target = as_of + 3600
        return next((bar for bar in ordered if bar["time"] >= target), None)
    if horizon == "session_close":
        day = _ist_date(as_of)
        same = [
            bar for bar in ordered
            if _ist_date(bar["time"]) == day and _minute(bar["time"]) >= SESSION_CLOSE_MINUTE
        ]
        return same[-1] if same else None
    count = int(horizon)
    dates = []
    analysis_day = _ist_date(as_of)
    for bar in ordered:
        day = _ist_date(bar["time"])
        if day > analysis_day and (not dates or dates[-1] != day):
            dates.append(day)
    if len(dates) < count:
        return None
    want = dates[count - 1]
    same = [bar for bar in ordered if _ist_date(bar["time"]) == want]
    return same[-1] if same else None


def _empty(horizon: str, trend: str) -> dict[str, Any]:
    blank = {"bias_right": None, "trigger_hit": None, "levels_respected": None}
    return {
        "status": "pending",
        "horizon": horizon,
        "move": None,
        "ai": blank,
        "always_bullish": {"bias": "bull", "bias_right": None},
        "follow_trend": {"bias": trend, "bias_right": None},
    }


def score_analysis(
    saved: dict,
    later_bars: list[dict],
    *,
    horizon: str,
    band: float | None = None,
) -> dict[str, Any]:
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    context = saved["context"]
    analysis = saved["analysis"]
    trend = trend_bias(context)
    chosen = _horizon_bar(int(context["as_of"]), later_bars, horizon)
    if chosen is None:
        return _empty(horizon, trend)
    width = neutral_band() if band is None else band
    price = float(context["last_price"])
    move = _classify(price, float(chosen["close"]), width)
    window = [bar for bar in later_bars if int(context["as_of"]) < int(bar["time"]) <= int(chosen["time"])]
    closes = [float(bar["close"]) for bar in window]
    respected = True
    for level in analysis.get("key_levels") or []:
        if level["kind"] == "support" and any(close < float(level["price"]) for close in closes):
            respected = False
        if level["kind"] == "resistance" and any(close > float(level["price"]) for close in closes):
            respected = False
    bias = analysis["bias"]
    if bias == "neutral":
        trigger = None
    elif bias == "bull":
        trigger = any(float(bar["high"]) >= float(analysis["bull"]["trigger"]) for bar in window)
    else:
        trigger = any(float(bar["low"]) <= float(analysis["bear"]["trigger"]) for bar in window)
    return {
        "status": "scored",
        "horizon": horizon,
        "move": move,
        "ai": {
            "bias_right": _direction_right(bias, move),
            "trigger_hit": trigger,
            "levels_respected": respected if analysis.get("key_levels") else True,
        },
        "always_bullish": {"bias": "bull", "bias_right": _direction_right("bull", move)},
        "follow_trend": {"bias": trend, "bias_right": _direction_right(trend, move)},
    }


def _rate(flags: list[bool | None]) -> float | None:
    kept = [flag for flag in flags if flag is not None]
    if not kept:
        return None
    return sum(1 for flag in kept if flag) / len(kept)


def hit_rates(scores: list[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for score in scores:
        if score.get("status") != "scored":
            continue
        grouped.setdefault(score["horizon"], []).append(score)
    out: dict[str, dict] = {}
    for horizon, rows in grouped.items():
        out[horizon] = {
            "scored": len(rows),
            "ai": {
                "bias": _rate([row["ai"]["bias_right"] for row in rows]),
                "triggers": _rate([row["ai"]["trigger_hit"] for row in rows]),
                "levels": _rate([row["ai"]["levels_respected"] for row in rows]),
            },
            "always_bullish": {"bias": _rate([row["always_bullish"]["bias_right"] for row in rows])},
            "follow_trend": {"bias": _rate([row["follow_trend"]["bias_right"] for row in rows])},
        }
    return out
