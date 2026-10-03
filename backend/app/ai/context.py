"""Compact chart context. Callers pass candles and calendars; this function does not fetch them."""

from __future__ import annotations

import json
import math
from datetime import datetime
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from app.ai.chain import NIFTY_CHAIN_KEY, UNAVAILABLE, fetch_option_chain, weekly_expiry
from app.indicators.frame import candles_to_frame
from app.indicators.registry import compute
from app.indicators.volume import VolumeRequired
from app.options.events import EventCalendar

IST = ZoneInfo("Asia/Kolkata")
CANDLE_LIMIT = 40
LEVEL_BAND = 0.05
VIX_STALE_SECONDS = 300
TIMEFRAMES = ("5m", "15m", "1h", "1D")


def swing_points(bars: list[dict], *, wing: int = 2) -> dict[str, list[dict]]:
    """Strict local extremes: a high above the `wing` bars on each side, and the same for a low."""
    highs: list[dict] = []
    lows: list[dict] = []
    for i in range(wing, len(bars) - wing):
        high = bars[i]["high"]
        low = bars[i]["low"]
        if all(high > bars[i + d]["high"] and high > bars[i - d]["high"] for d in range(1, wing + 1)):
            highs.append({"time": bars[i]["time"], "price": high})
        if all(low < bars[i + d]["low"] and low < bars[i - d]["low"] for d in range(1, wing + 1)):
            lows.append({"time": bars[i]["time"], "price": low})
    return {"highs": highs, "lows": lows}


def context_hash(context: dict) -> str:
    body = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(body.encode("ascii")).hexdigest()


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _candle(row: dict) -> dict:
    return {
        "time": int(row["time"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
    }


def _at(values, index: int) -> float | None:
    if values is None or len(values) < abs(index):
        return None
    return _finite(values[index])


def _indicators(bars: list[dict]) -> dict[str, float | None]:
    frame = candles_to_frame(bars)
    ema = compute(frame, "ema", {"length": 20, "source": "close"})["ema"]
    rsi = compute(frame, "rsi", {"length": 14, "source": "close"})["rsi"]
    macd = compute(frame, "macd", {"fast": 12, "slow": 26, "signal": 9, "source": "close"})
    try:
        vwap = _at(compute(frame, "vwap", {"source": "hlc3"})["vwap"], -1)
    except VolumeRequired:
        vwap = None
    return {
        "ema20": _at(ema, -1),
        "ema20_prev": _at(ema, -2),
        "rsi14": _at(rsi, -1),
        "vwap": vwap,
        "macd": _at(macd["macd"], -1),
        "macd_signal": _at(macd["signal"], -1),
        "macd_hist": _at(macd["hist"], -1),
    }


def _day_range(bars: list[dict]) -> dict[str, float | None]:
    if not bars:
        return {"open": None, "high": None, "low": None, "close": None}
    return {
        "open": float(bars[0]["open"]),
        "high": max(float(bar["high"]) for bar in bars),
        "low": min(float(bar["low"]) for bar in bars),
        "close": float(bars[-1]["close"]),
    }


def _events(calendar: EventCalendar, day) -> dict[str, Any]:
    names = [event.name for event in calendar.days if event.date == day]
    for event in calendar.days:
        if calendar.reaction_of(event.date) == day and event.name not in names:
            names.append(event.name)
    return {
        "event_day": calendar.is_event(day),
        "reaction_day": calendar.is_reaction(day),
        "names": names,
    }


def _vix(bars: list[dict], as_of: int) -> dict[str, Any]:
    prior = [bar for bar in bars if int(bar["time"]) <= as_of]
    if not prior:
        return {"value": None, "stale": True}
    last = prior[-1]
    return {"value": float(last["close"]), "stale": as_of - int(last["time"]) > VIX_STALE_SECONDS}


def _options(symbol: str, as_of: int, chain: Any) -> dict[str, Any] | None:
    if symbol != "NIFTY50":
        return None
    if chain is None:
        return {"available": False, "reason": UNAVAILABLE}
    when = datetime.fromtimestamp(as_of, IST).date()
    expiry = weekly_expiry(when)
    try:
        return fetch_option_chain(chain.get_json, NIFTY_CHAIN_KEY, expiry)
    except Exception:
        return {"available": False, "reason": UNAVAILABLE, "expiry": expiry}


def build_context(
    *,
    symbol: str,
    as_of: int,
    candles: dict[str, list[dict]],
    vix: list[dict],
    events: EventCalendar,
    chain: Any = None,
) -> dict[str, Any]:
    day = datetime.fromtimestamp(as_of, IST).date()
    frames: dict[str, Any] = {}
    five_all: list[dict] = []
    for name in TIMEFRAMES:
        kept = [_candle(row) for row in candles.get(name, []) if int(row["time"]) <= as_of]
        if name == "5m":
            five_all = kept
        shown = kept[-CANDLE_LIMIT:]
        frames[name] = {
            "candles": shown,
            "indicators": _indicators(shown) if shown else {
                "ema20": None, "ema20_prev": None, "rsi14": None, "vwap": None,
                "macd": None, "macd_signal": None, "macd_hist": None,
            },
        }
    swings = swing_points(frames["5m"]["candles"], wing=2)
    session = [_candle(row) for row in five_all if datetime.fromtimestamp(row["time"], IST).date() == day]
    last_price = float(frames["5m"]["candles"][-1]["close"]) if frames["5m"]["candles"] else None
    return {
        "symbol": symbol,
        "as_of": int(as_of),
        "last_price": last_price,
        "timeframes": frames,
        "levels": {
            "support": swings["lows"][-3:],
            "resistance": swings["highs"][-3:],
        },
        "day": _day_range(session),
        "vix": _vix(vix, as_of),
        "events": _events(events, day),
        "options": _options(symbol, as_of, chain),
    }
