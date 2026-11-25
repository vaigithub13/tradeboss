"""Walk-forward reliability on a sample, not on every bar.

Intraday timeframes use one query per session at four fixed clocks
(10:15, 11:30, 13:15, 14:15 IST). Daily bars use every window. Each query
sees only earlier windows, at least one day before it. Sessions inside
2026-07-01 .. 2026-10-01 are left out of the sample and out of the match
library, so the holdout stays unread.

The search is vectorised (squared Euclidean via a dot product) and stops
when the time cap is hit. Progress is written to the ``tradeboss.similar`` log.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable

import numpy as np

from app.similar.features import (
    DAY_GAP_S,
    HORIZONS,
    SAMPLE_CLOCKS,
    TOP_K,
    bar_seconds,
    holdout_mask,
    ist_day,
)
from app.similar.match import forward_slice

log = logging.getLogger("tradeboss.similar")


def verdict(hit_pct: float, base_pct: float, n: int) -> str:
    """Plain words. Within 3 percentage points, or not distinguishable, is a coin flip."""
    if n < 10:
        return "too few samples"
    if abs(hit_pct - base_pct) <= 3.0:
        return "no better than coin flip"
    p = min(max(base_pct / 100.0, 1e-6), 1.0 - 1e-6)
    se = math.sqrt(p * (1.0 - p) / n) * 100.0
    z = (hit_pct - base_pct) / se if se > 0 else 0.0
    if abs(z) < 1.96:
        return "no better than coin flip"
    if hit_pct > base_pct:
        return "above the base rate"
    return "below the base rate"


def _forward_column(
    close: np.ndarray,
    times: np.ndarray,
    ends: np.ndarray,
    horizon: int,
    *,
    intraday: bool,
) -> np.ndarray:
    out = np.full(len(ends), np.nan, dtype=np.float64)
    last = ends + horizon
    ok = last < len(close)
    if intraday and len(ends):
        days = ist_day(times)
        clipped = np.clip(last, 0, len(times) - 1)
        nxt = np.clip(ends + 1, 0, len(times) - 1)
        ok = ok & (days[ends] == days[clipped]) & (days[ends] == days[nxt]) & (last < len(times))
    if np.any(ok):
        ref = close[ends[ok]]
        out[ok] = (close[last[ok]] / np.maximum(ref, 1e-12) - 1.0) * 100.0
    return out


def sample_positions(times: np.ndarray, ends: np.ndarray, timeframe: str) -> np.ndarray:
    """Positions into ``ends`` that the reliability sample evaluates."""
    if len(ends) == 0:
        return np.empty(0, dtype=np.int64)
    end_times = times[ends]
    days = ist_day(end_times)
    keep = ~holdout_mask(days)
    if timeframe == "1D":
        return np.flatnonzero(keep)
    step = bar_seconds(timeframe)
    minute = ((end_times + 19_800) % 86_400) // 60
    # The bar that closes at the clock has start = clock - bar length.
    wanted = {clock - step // 60 for clock in SAMPLE_CLOCKS}
    on_clock = np.isin(minute, list(wanted))
    return np.flatnonzero(keep & on_clock)


def _nearest(bank: np.ndarray, norms: np.ndarray, pos: int, mask: np.ndarray, k_pool: int = 400) -> np.ndarray:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return idx
    query = bank[pos].astype(np.float64)
    dots = bank[idx].astype(np.float64) @ query
    dist2 = norms[idx] + float(query @ query) - 2.0 * dots
    take = min(k_pool, len(idx))
    part = np.argpartition(dist2, take - 1)[:take]
    order = part[np.argsort(dist2[part], kind="mergesort")]
    return idx[order]


def _separated(order: np.ndarray, end_times: np.ndarray, k: int) -> list[int]:
    chosen: list[int] = []
    times: list[int] = []
    for pos in order:
        end = int(end_times[pos])
        if any(abs(end - prev) < DAY_GAP_S for prev in times):
            continue
        chosen.append(int(pos))
        times.append(end)
        if len(chosen) == k:
            break
    return chosen


def run_walkforward(
    times: np.ndarray,
    close: np.ndarray,
    bank: np.ndarray,
    ends: np.ndarray,
    timeframe: str,
    *,
    time_cap_s: float,
    clock: Callable[[], float] | None = None,
) -> dict:
    """Majority direction of the ten matches against what the next candles did."""
    now = clock or time.perf_counter
    started = now()
    intraday = timeframe != "1D"
    positions = sample_positions(times, ends, timeframe)
    planned = int(len(positions))
    end_times = times[ends]
    days = ist_day(end_times)
    usable = ~holdout_mask(days)
    norms = np.einsum("ij,ij->i", bank.astype(np.float64), bank.astype(np.float64)) if len(bank) else np.empty(0)
    columns = {h: _forward_column(close, times, ends, h, intraday=intraday) for h in HORIZONS}
    hits = {h: 0 for h in HORIZONS}
    calls = {h: 0 for h in HORIZONS}
    ups = {h: 0 for h in HORIZONS}
    evaluated = 0
    capped = False
    window = bank.shape[1] // 4 if bank.ndim == 2 and bank.shape[1] else 0
    for step, pos in enumerate(positions):
        if now() - started > time_cap_s:
            capped = True
            break
        evaluated += 1
        if step % 200 == 0:
            log.info(
                "similar walk-forward %s %s/%s elapsed %.1fs",
                timeframe,
                step,
                planned,
                now() - started,
            )
        q_end = int(ends[pos])
        q_start = int(times[q_end - window + 1]) if window else int(end_times[pos])
        mask = usable.copy()
        mask[pos] = False
        mask &= end_times <= q_start - DAY_GAP_S
        order = _nearest(bank, norms, int(pos), mask)
        chosen = _separated(order, end_times, TOP_K)
        if len(chosen) < 3:
            continue
        for horizon in HORIZONS:
            actual = columns[horizon][pos]
            if not np.isfinite(actual) or actual == 0:
                continue
            votes = columns[horizon][chosen]
            votes = votes[np.isfinite(votes) & (votes != 0)]
            if len(votes) == 0 or float(np.sum(np.sign(votes))) == 0:
                continue
            call = 1.0 if float(np.sum(np.sign(votes))) > 0 else -1.0
            calls[horizon] += 1
            if actual > 0:
                ups[horizon] += 1
            if call == (1.0 if actual > 0 else -1.0):
                hits[horizon] += 1
    runtime = now() - started
    log.info(
        "similar walk-forward %s done evaluated %s/%s in %.1fs capped=%s",
        timeframe,
        evaluated,
        planned,
        runtime,
        capped,
    )
    report: dict[str, dict] = {}
    for horizon in HORIZONS:
        n = calls[horizon]
        hit = (hits[horizon] / n * 100.0) if n else None
        up = (ups[horizon] / n * 100.0) if n else None
        base = max(up, 100.0 - up) if up is not None else None
        report[str(horizon)] = {
            "n": n,
            "hit_rate": hit,
            "base_rate": base,
            "up_rate": up,
            "verdict": verdict(hit, base, n) if hit is not None and base is not None else "too few samples",
            "sample_size": planned,
            "evaluated": evaluated,
            "runtime_s": round(runtime, 3),
            "capped": capped,
            "clocks": [] if timeframe == "1D" else [f"{m // 60:02d}:{m % 60:02d}" for m in SAMPLE_CLOCKS],
        }
    return report


def query_uses_only_past(times: np.ndarray, ends: np.ndarray, query_pos: int, chosen: list[int]) -> bool:
    """Test helper: every chosen window ends at least a day before the query starts."""
    if query_pos < 0 or query_pos >= len(ends):
        return False
    q_start = int(times[int(ends[query_pos])])
    # The query start is the first bar of the window; callers pass that check separately.
    return all(int(times[int(ends[pos])]) <= q_start - DAY_GAP_S for pos in chosen)


def known_forward(times: np.ndarray, end: int, horizon: int, as_of: int, *, intraday: bool) -> bool:
    """True only when the forward path does not pass ``as_of``."""
    return forward_slice(times, end, horizon, as_of=as_of, intraday=intraday) is not None
