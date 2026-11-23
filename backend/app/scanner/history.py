"""Replay one setup on earlier sessions. This does not call the Time Machine."""

from __future__ import annotations

from datetime import date, timedelta

from app.scanner.bars import LAST_BAR_MIN, Bar, day_of, minute_of, session_for
from app.scanner.config import ScannerSettings
from app.scanner.models import HistoryResult, Hit
from app.scanner.plan import build_plan
from app.scanner.setups import run_setups


def _prev_close(bars: list[Bar], day: date) -> float | None:
    prior = [b for b in bars if day_of(int(b["time"])) < day]
    if not prior:
        return None
    return float(prior[-1]["close"])


def bar_exit(bar: Bar, *, side: str, stop: float, target: float) -> str | None:
    o = float(bar["open"])
    h = float(bar["high"])
    l = float(bar["low"])
    if side == "long":
        stop_hit = l <= stop
        target_hit = h >= target
    else:
        stop_hit = h >= stop
        target_hit = l <= target
    if stop_hit and target_hit:
        return "stop" if abs(o - stop) <= abs(o - target) else "target"
    if stop_hit:
        return "stop"
    if target_hit:
        return "target"
    return None


def walk(bars_after: list[Bar], *, side: str, entry: float, stop: float, target: float, target_r: float) -> tuple[str, float, float]:
    """Return reason, exit price, R. Time exit uses the 15:10 close (or the last bar before that)."""
    risk = abs(entry - stop)
    last_close = entry
    for bar in bars_after:
        if minute_of(int(bar["time"])) > LAST_BAR_MIN:
            break
        last_close = float(bar["close"])
        reason = bar_exit(bar, side=side, stop=stop, target=target)
        if reason == "stop":
            return "stop", stop, -1.0
        if reason == "target":
            return "target", target, target_r
    signed = (last_close - entry) if side == "long" else (entry - last_close)
    r = signed / risk if risk else 0.0
    return "time", last_close, r


def history_for(
    symbol: str,
    universe: dict[str, list[Bar]],
    *,
    setup: str,
    up_to: date,
    settings: ScannerSettings,
    cache: dict | None = None,
) -> tuple[HistoryResult, str]:
    """Use this stock's fires. Under tm_min_fires, use every name in the universe for the same setup."""
    own_key = ("own", symbol, setup, up_to.isoformat())
    if cache is not None and own_key in cache:
        own = cache[own_key]
    else:
        own = occurrences(universe.get(symbol, []), setup=setup, up_to=up_to, settings=settings)
        if cache is not None:
            cache[own_key] = own
    if own.fires >= settings.tm_min_fires:
        return own, "stock"
    key = (setup, up_to.isoformat())
    if cache is not None and key in cache:
        return cache[key], "pooled"
    parts: list[tuple[str, HistoryResult]] = []
    for name, bars in universe.items():
        if name == symbol:
            parts.append((name, own))
        else:
            parts.append((name, occurrences(bars, setup=setup, up_to=up_to, settings=settings)))
    combined = _merge(parts)
    if cache is not None:
        cache[key] = combined
    return combined, "pooled"


def _merge(parts: list[tuple[str, HistoryResult]]) -> HistoryResult:
    rows: list[dict] = []
    wins = losses = neither = 0
    r_sum = 0.0
    for name, part in parts:
        wins += part.wins
        losses += part.losses
        neither += part.neither
        r_sum += (part.avg_r or 0.0) * part.fires
        for row in part.occurrences:
            rows.append({**row, "symbol": name})
    fires = wins + losses + neither
    return HistoryResult(
        fires=fires,
        wins=wins,
        losses=losses,
        neither=neither,
        win_rate=(wins / fires) if fires else None,
        avg_r=(r_sum / fires) if fires else None,
        occurrences=rows,
    )


def occurrences(
    bars: list[Bar],
    *,
    setup: str,
    up_to: date,
    settings: ScannerSettings,
    limit: int = 59,
) -> HistoryResult:
    days = []
    seen: set[date] = set()
    for bar in bars:
        d = day_of(int(bar["time"]))
        if d < up_to and d not in seen:
            seen.add(d)
            days.append(d)
    days = days[-limit:]
    from app.runtime.lag import pace

    rows: list[dict] = []
    for day in days:
        pace()
        prev = _prev_close(bars, day)
        window_from = day - timedelta(days=21)
        window = [b for b in bars if window_from <= day_of(int(b["time"])) <= day]
        hits = run_setups(window, enabled=settings.setups, prev_close=prev, as_of=None)
        for hit in hits:
            if hit.forming or hit.setup != setup or day_of(hit.time) != day:
                continue
            plan = build_plan(hit, settings, symbol="", next_bar=_next_bar(bars, hit))
            if plan.entry is None or plan.stop is None or plan.target is None or plan.rr is None:
                continue
            after = [b for b in session_for(bars, day) if int(b["time"]) > plan.entry_time]
            reason, exit_px, r = walk(
                after,
                side=plan.side,
                entry=plan.entry,
                stop=plan.stop,
                target=plan.target,
                target_r=plan.target_r,
            )
            rows.append(
                {
                    "day": day.isoformat(),
                    "time": hit.time,
                    "side": hit.side,
                    "entry": plan.entry,
                    "exit": exit_px,
                    "reason": reason,
                    "r": r,
                }
            )
    wins = sum(1 for r in rows if r["reason"] == "target")
    losses = sum(1 for r in rows if r["reason"] == "stop")
    neither = sum(1 for r in rows if r["reason"] == "time")
    fires = len(rows)
    return HistoryResult(
        fires=fires,
        wins=wins,
        losses=losses,
        neither=neither,
        win_rate=(wins / fires) if fires else None,
        avg_r=(sum(float(r["r"]) for r in rows) / fires) if fires else None,
        occurrences=rows,
    )


def _next_bar(bars: list[Bar], hit: Hit) -> Bar | None:
    for bar in bars:
        if int(bar["time"]) > hit.time:
            return bar
    return None
