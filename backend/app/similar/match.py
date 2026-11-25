"""Match a query window to earlier windows. No bar after the query is a candidate."""

from __future__ import annotations

import numpy as np

from app.similar.features import (
    DAY_GAP_S,
    TOP_K,
    ist_day,
    zscore_window,
)


def euclidean(query: np.ndarray, bank: np.ndarray) -> np.ndarray:
    if len(bank) == 0:
        return np.empty(0, dtype=np.float64)
    diff = bank.astype(np.float64) - query.astype(np.float64)
    return np.sqrt(np.einsum("ij,ij->i", diff, diff))


def dtw_distance(left: np.ndarray, right: np.ndarray, band: int) -> float:
    """Sakoe-Chiba DTW on two (window, features) sequences. ``band`` is in bars."""
    n = left.shape[0]
    band = max(0, int(band))
    inf = 1e18
    prev = np.full(n, inf)
    prev[0] = float(np.sum((left[0] - right[0]) ** 2))
    for i in range(1, n):
        curr = np.full(n, inf)
        j0 = max(0, i - band)
        j1 = min(n, i + band + 1)
        for j in range(j0, j1):
            cost = float(np.sum((left[i] - right[j]) ** 2))
            best = prev[j]
            if j > 0:
                best = min(best, curr[j - 1], prev[j - 1])
            curr[j] = cost + best
        prev = curr
    return float(np.sqrt(prev[-1])) if prev[-1] < inf / 2 else float("inf")


def dtw_band(window: int) -> int:
    return max(1, window // 6)


def pick_matches(
    distances: np.ndarray,
    end_times: np.ndarray,
    *,
    query_start: int,
    k: int = TOP_K,
    min_gap_s: int = DAY_GAP_S,
    pool: int = 400,
) -> list[int]:
    """Greedy nearest matches.

    A candidate is dropped when it overlaps the query (its end is at or after
    the query start) or when it sits within ``min_gap_s`` of the query or of a
    match already kept.
    """
    if len(distances) == 0 or k < 1:
        return []
    eligible = end_times <= query_start - min_gap_s
    idx = np.flatnonzero(eligible)
    if len(idx) == 0:
        return []
    order = idx[np.argsort(distances[idx], kind="mergesort")]
    if len(order) > pool:
        # The full sort above is only used when the caller already sliced.
        order = order[:pool]
    chosen: list[int] = []
    chosen_times: list[int] = []
    for pos in order:
        end = int(end_times[pos])
        if any(abs(end - prev) < min_gap_s for prev in chosen_times):
            continue
        chosen.append(int(pos))
        chosen_times.append(end)
        if len(chosen) == k:
            break
    return chosen


def rerank_dtw(
    features: np.ndarray,
    ends: np.ndarray,
    query_end: int,
    window: int,
    shortlist: list[int],
) -> list[int]:
    """Reorder a Euclidean shortlist by banded DTW. The shortlist is not widened."""
    query = zscore_window(features[query_end - window + 1 : query_end + 1])
    band = dtw_band(window)
    scored = []
    for pos in shortlist:
        end = int(ends[pos])
        block = zscore_window(features[end - window + 1 : end + 1])
        scored.append((dtw_distance(query, block, band), pos))
    scored.sort(key=lambda item: item[0])
    return [pos for _, pos in scored]


def forward_slice(times: np.ndarray, end: int, horizon: int, *, as_of: int | None, intraday: bool) -> tuple[int, int] | None:
    """Inclusive [first, last] index of the next ``horizon`` bars, or None.

    Intraday forwards that leave the session day are dropped. A bar after
    ``as_of`` is dropped.
    """
    last = end + horizon
    if last >= len(times):
        return None
    if as_of is not None and int(times[last]) > int(as_of):
        return None
    if intraday:
        days = ist_day(times)
        if int(days[end]) != int(days[last]) or int(days[end]) != int(days[end + 1]):
            return None
    return end + 1, last


def excursion(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, end: int, last: int) -> dict[str, float]:
    ref = float(close[end])
    scale = float(atr[end]) if float(atr[end]) > 1e-12 else 1e-12
    segment_h = high[end + 1 : last + 1]
    segment_l = low[end + 1 : last + 1]
    ret = (float(close[last]) / ref - 1.0) * 100.0
    return {
        "return_pct": ret,
        "mfe_atr": (float(segment_h.max()) - ref) / scale,
        "mae_atr": (ref - float(segment_l.min())) / scale,
    }


def summary(returns: np.ndarray) -> dict[str, float | int | None]:
    clean = returns[np.isfinite(returns)]
    if len(clean) == 0:
        return {"n": 0, "pct_up": None, "median_return": None, "spread": None}
    return {
        "n": int(len(clean)),
        "pct_up": float(np.mean(clean > 0) * 100.0),
        "median_return": float(np.median(clean)),
        "spread": float(np.quantile(clean, 0.75) - np.quantile(clean, 0.25)),
    }


def base_returns(
    close: np.ndarray,
    times: np.ndarray,
    ends: np.ndarray,
    horizon: int,
    *,
    as_of: int | None,
    intraday: bool,
) -> np.ndarray:
    """Forward returns of every allowed window whose outcome is already known at ``as_of``."""
    if len(ends) == 0:
        return np.empty(0, dtype=np.float64)
    last = ends + horizon
    ok = last < len(close)
    if as_of is not None:
        ok &= times[np.clip(last, 0, len(times) - 1)] <= int(as_of)
        ok &= last < len(times)
    if intraday and np.any(ok):
        days = ist_day(times)
        clipped = np.clip(last, 0, len(times) - 1)
        ok &= days[ends] == days[clipped]
        nxt = np.clip(ends + 1, 0, len(times) - 1)
        ok &= days[ends] == days[nxt]
    if not np.any(ok):
        return np.empty(0, dtype=np.float64)
    ref = close[ends[ok]]
    out = (close[last[ok]] / np.maximum(ref, 1e-12) - 1.0) * 100.0
    return out[np.isfinite(out)]


def horizon_block(returns_match: list[float], returns_all: np.ndarray) -> dict:
    matched = summary(np.asarray(returns_match, dtype=np.float64))
    base = summary(returns_all)
    return {
        "matches": matched,
        "base": {
            "n": base["n"],
            "pct_up": base["pct_up"],
            "median_return": base["median_return"],
            "spread": base["spread"],
        },
    }


def pack_horizons(per_horizon: dict[int, list[float]], close, times, ends, *, as_of, intraday) -> dict[str, dict]:
    out = {}
    for horizon in HORIZONS:
        out[str(horizon)] = horizon_block(
            per_horizon.get(horizon, []),
            base_returns(close, times, ends, horizon, as_of=as_of, intraday=intraday),
        )
    return out
