"""The analysis JSON. Anything that looks like an order is refused."""

from __future__ import annotations

from typing import Any

from app.ai.context import LEVEL_BAND

ORDER_KEYS = ("order", "orders", "side", "qty", "action")
TRENDS = ("5m", "15m", "1h", "1D")
DIRECTIONS = ("up", "down", "sideways")
BIASES = ("bull", "bear", "neutral")
LEVEL_KINDS = ("support", "resistance")
FIELDS = ("trends", "bias", "key_levels", "patterns", "bull", "bear", "confidence", "reasoning")


class AnalysisError(ValueError):
    """The reply is not an analysis."""


def _price(value: Any, field: str, last_price: float) -> float:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise AnalysisError(f"{field} must be a number") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisError(f"{field} must be a number")
    price = float(value)
    lo = last_price * (1 - LEVEL_BAND)
    hi = last_price * (1 + LEVEL_BAND)
    if price < lo or price > hi:
        shown = int(value) if isinstance(value, int) else value
        raise AnalysisError(f"{field} {shown} is outside {lo:.2f}..{hi:.2f}")
    return price


def _scenario(raw: Any, name: str, last_price: float) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AnalysisError(f"{name} is required")
    note = raw.get("note")
    if not isinstance(note, str) or not note.strip():
        raise AnalysisError(f"{name}.note is required")
    return {
        "trigger": _price(raw.get("trigger"), f"{name}.trigger", last_price),
        "invalidation": _price(raw.get("invalidation"), f"{name}.invalidation", last_price),
        "note": note.strip(),
    }


def validate_analysis(raw: Any, *, last_price: float) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AnalysisError("reply is not an analysis")
    for key in raw:
        if key in ORDER_KEYS:
            raise AnalysisError("analysis cannot contain an order")
        if key not in FIELDS:
            raise AnalysisError(f"remove key {key}")
    trends = raw.get("trends")
    if not isinstance(trends, dict) or set(trends) != set(TRENDS):
        raise AnalysisError("trends must be 5m, 15m, 1h, and 1D")
    clean_trends = {}
    for name in TRENDS:
        if trends[name] not in DIRECTIONS:
            raise AnalysisError(f"trends.{name} must be up, down, or sideways")
        clean_trends[name] = trends[name]
    bias = raw.get("bias")
    if bias not in BIASES:
        raise AnalysisError("bias must be bull, bear, or neutral")
    levels = raw.get("key_levels")
    if not isinstance(levels, list):
        raise AnalysisError("key_levels must be a list")
    clean_levels = []
    for index, level in enumerate(levels):
        if not isinstance(level, dict):
            raise AnalysisError(f"key_levels[{index}] must be an object")
        kind = level.get("kind")
        if kind not in LEVEL_KINDS:
            raise AnalysisError(f"key_levels[{index}].kind must be support or resistance")
        label = level.get("label")
        if not isinstance(label, str) or not label.strip():
            raise AnalysisError(f"key_levels[{index}].label is required")
        clean_levels.append({
            "price": _price(level.get("price"), f"key_levels[{index}].price", last_price),
            "kind": kind,
            "label": label.strip(),
        })
    patterns = raw.get("patterns")
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise AnalysisError("patterns must be a list of strings")
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise AnalysisError("confidence must be from 0 to 1")
    reasoning = raw.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise AnalysisError("reasoning is required")
    return {
        "trends": clean_trends,
        "bias": bias,
        "key_levels": clean_levels,
        "patterns": list(patterns),
        "bull": _scenario(raw.get("bull"), "bull", last_price),
        "bear": _scenario(raw.get("bear"), "bear", last_price),
        "confidence": float(confidence),
        "reasoning": reasoning.strip(),
    }
