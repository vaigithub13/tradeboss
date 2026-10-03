"""Model name and the cost line. A dollar amount is shown only when both rates are set."""

from __future__ import annotations

from app.pine.openai_client import _setting

DEFAULT_NEUTRAL_BAND = 0.003


def analysis_model() -> str:
    """`AI_ANALYSIS_MODEL`, else `AI_MODEL`, else gpt-4o-mini."""
    return _setting("AI_ANALYSIS_MODEL") or _setting("AI_MODEL") or "gpt-4o-mini"


def neutral_band() -> float:
    """Fraction of price inside which a move is flat. Default 0.3%."""
    raw = _setting("AI_ANALYSIS_NEUTRAL_BAND")
    if not raw:
        return DEFAULT_NEUTRAL_BAND
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_NEUTRAL_BAND


def analysis_language() -> str:
    """`AI_ANALYSIS_LANGUAGE`, otherwise English."""
    return (_setting("AI_ANALYSIS_LANGUAGE") or "en").strip().lower() or "en"


def auto_analysis() -> dict[str, object]:
    """Optional timer. Off unless `AI_ANALYSIS_AUTO` is true. Interval defaults to 15 minutes."""
    raw = (_setting("AI_ANALYSIS_AUTO") or "").strip().lower()
    minutes_raw = _setting("AI_ANALYSIS_AUTO_MINUTES") or "15"
    try:
        minutes = int(minutes_raw)
    except ValueError:
        minutes = 15
    if minutes < 1:
        minutes = 15
    return {
        "language": analysis_language(),
        "auto": raw in {"1", "true", "yes", "on"},
        "auto_minutes": minutes,
    }


def cost_label(usage: dict) -> str:
    incoming = int(usage.get("prompt_tokens") or 0)
    outgoing = int(usage.get("completion_tokens") or 0)
    text = f"{incoming} in / {outgoing} out"
    raw_in = _setting("AI_ANALYSIS_INPUT_USD_PER_MTOK")
    raw_out = _setting("AI_ANALYSIS_OUTPUT_USD_PER_MTOK")
    if not raw_in or not raw_out:
        return text
    try:
        dollars = incoming * float(raw_in) / 1_000_000 + outgoing * float(raw_out) / 1_000_000
    except ValueError:
        return text
    return f"{text} · ${dollars:.6f}"
