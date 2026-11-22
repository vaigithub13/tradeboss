"""GOOD / COIN FLIP / AVOID from the Time Machine and the history pass."""

from __future__ import annotations

from app.scanner.config import ScannerSettings


def judge(
    *,
    target_pct: float,
    stop_pct: float,
    win_rate: float | None,
    fires: int,
    settings: ScannerSettings,
) -> str:
    rate = 0.0 if win_rate is None else win_rate
    good = (
        target_pct >= stop_pct + settings.tm_edge_points
        and rate >= settings.tm_min_win_rate
        and fires >= settings.tm_min_fires
    )
    if good:
        return "GOOD"
    if target_pct + settings.tm_edge_points > stop_pct:
        return "COIN_FLIP"
    return "AVOID"
