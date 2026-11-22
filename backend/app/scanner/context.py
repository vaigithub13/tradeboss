"""Per-stock columns computed at scan time. Nothing here is stored as its own dataset."""

from __future__ import annotations

from datetime import date
from statistics import median

from app.scanner.bars import Bar, day_of, minute_of, session_vwap


def day_change_pct(today: list[Bar], prev_close: float | None) -> float | None:
    if not today:
        return None
    last = float(today[-1]["close"])
    base = prev_close if prev_close and prev_close > 0 else float(today[0]["open"])
    if base <= 0:
        return None
    return (last - base) / base * 100.0


def vwap_distance_pct(today: list[Bar]) -> float | None:
    if not today:
        return None
    vwaps = session_vwap(today)
    vwap = vwaps[-1]
    if vwap is None or vwap == 0:
        return None
    return (float(today[-1]["close"]) - vwap) / vwap * 100.0


def relative_volume(today: list[Bar], prior_sessions: list[list[Bar]]) -> float | None:
    if not today:
        return None
    cutoff = minute_of(int(today[-1]["time"]))
    so_far = sum(float(b["volume"]) for b in today if minute_of(int(b["time"])) <= cutoff)
    samples: list[float] = []
    for session in prior_sessions[-20:]:
        samples.append(sum(float(b["volume"]) for b in session if minute_of(int(b["time"])) <= cutoff))
    if not samples:
        return None
    med = median(samples)
    if med <= 0:
        return None
    return so_far / med


def context_row(
    *,
    symbol: str,
    sector: str,
    today: list[Bar],
    prior_sessions: list[list[Bar]],
    prev_close: float | None,
    nifty_pct: float | None,
) -> dict[str, float | str | None]:
    change = day_change_pct(today, prev_close)
    vs = None if change is None or nifty_pct is None else change - nifty_pct
    return {
        "symbol": symbol,
        "sector": sector,
        "change_pct": change,
        "vwap_dist_pct": vwap_distance_pct(today),
        "rvol": relative_volume(today, prior_sessions),
        "vs_nifty": vs,
        "nifty_pct": nifty_pct,
    }


def nifty_change_pct(nifty_bars: list[Bar], day: date, prev_close: float | None) -> float | None:
    today = [b for b in nifty_bars if day_of(int(b["time"])) == day]
    return day_change_pct(today, prev_close)
