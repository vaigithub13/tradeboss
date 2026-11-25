"""Similar cash sessions for NIFTY, SENSEX, and BANKNIFTY.

Features are known at the chosen clock. Outcomes are how that session ended,
and they are never used as features. The holdout window is not scored.
"""

from __future__ import annotations

import math

import numpy as np

from app.market_view.view import structure
from app.similar.features import (
    DAY_S,
    IST_OFFSET_S,
    SIMILAR_DAYS,
    date_of,
    holdout_mask,
    in_holdout,
    ist_day,
    wilder_atr,
)
from app.similar.match import euclidean
from app.similar.walkforward import verdict

FEATURE_KEYS = (
    "gap_atr",
    "box_atr",
    "open_vs_pivot_atr",
    "open_vs_cpr",
    "vix",
    "vix_change",
    "prior_trend",
    "prior_range_atr",
)


def _minute(times: np.ndarray) -> np.ndarray:
    return ((times.astype(np.int64) + IST_OFFSET_S) % DAY_S) // 60


def _groups(times: np.ndarray) -> list[tuple[int, int, int]]:
    """(day, start_index, end_index) for each IST date, end exclusive."""
    if len(times) == 0:
        return []
    days = ist_day(times)
    cuts = np.flatnonzero(np.diff(days)) + 1
    bounds = np.concatenate([[0], cuts, [len(times)]])
    return [(int(days[bounds[i]]), int(bounds[i]), int(bounds[i + 1])) for i in range(len(bounds) - 1)]


def _vix_at(day: int, clock_minute: int, vix_days: np.ndarray, vix_minutes: np.ndarray, vix_close: np.ndarray) -> float | None:
    same = np.flatnonzero(vix_days == day)
    if len(same) == 0:
        return None
    known = same[vix_minutes[same] <= clock_minute]
    if len(known) == 0:
        return None
    return float(vix_close[known[-1]])


def build_day_rows(
    times: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    clock_minute: int,
    vix_times: np.ndarray | None = None,
    vix_close: np.ndarray | None = None,
) -> list[dict]:
    """One row per session. Features stop at ``clock_minute``. Outcomes use the whole session."""
    groups = _groups(times)
    if len(groups) < 3:
        return []
    daily = []
    for day, a, b in groups:
        daily.append({
            "time": int(times[a]),
            "open": float(open_[a]),
            "high": float(high[a:b].max()),
            "low": float(low[a:b].min()),
            "close": float(close[b - 1]),
        })
    d_high = np.array([d["high"] for d in daily])
    d_low = np.array([d["low"] for d in daily])
    d_close = np.array([d["close"] for d in daily])
    atr = wilder_atr(d_high, d_low, d_close)
    vix_days = ist_day(vix_times) if vix_times is not None and len(vix_times) else np.empty(0, dtype=np.int64)
    vix_minutes = _minute(vix_times) if len(vix_days) else np.empty(0, dtype=np.int64)
    vix_c = vix_close if vix_close is not None else np.empty(0)
    rows: list[dict] = []
    for i, (day, a, b) in enumerate(groups):
        if i == 0:
            continue
        scale = float(atr[i - 1]) if float(atr[i - 1]) > 1e-9 else None
        if scale is None:
            continue
        minutes = _minute(times[a:b])
        known = minutes <= clock_minute
        if not np.any(known):
            continue
        ka = a + int(np.flatnonzero(known)[0])
        kb = a + int(np.flatnonzero(known)[-1])
        session_open = float(open_[a])
        prev_close = float(close[groups[i - 1][2] - 1])
        st = structure(daily[:i])
        if st is None:
            continue
        box = known & (minutes < 10 * 60 + 15)
        if not np.any(box):
            box = known
        box_h = float(high[a:b][box].max())
        box_l = float(low[a:b][box].min())
        pivot = float(st["pivot"])
        if session_open > st["tc"]:
            cpr_pos = 1.0
        elif session_open < st["bc"]:
            cpr_pos = -1.0
        else:
            cpr_pos = 0.0
        trend = {"up": 1.0, "down": -1.0}.get(st["trend"], 0.0)
        vix_now = _vix_at(day, clock_minute, vix_days, vix_minutes, vix_c) if len(vix_days) else None
        prev_day = groups[i - 1][0]
        vix_prev = None
        if len(vix_days):
            prev_hits = np.flatnonzero(vix_days == prev_day)
            if len(prev_hits):
                vix_prev = float(vix_c[prev_hits[-1]])
        vix_change = None
        if vix_now is not None and vix_prev not in (None, 0.0):
            vix_change = (vix_now - vix_prev) / vix_prev
        day_high = float(high[a:b].max())
        day_low = float(low[a:b].min())
        day_close = float(close[b - 1])
        span = day_high - day_low
        trend_day = bool(span > 0 and abs(day_close - session_open) >= 0.5 * span)
        rows.append({
            "day": day,
            "date": date_of(day).isoformat(),
            "holdout": in_holdout(day),
            "end_time": int(times[b - 1]),
            "features": {
                "gap_atr": (session_open - prev_close) / scale,
                "box_atr": (box_h - box_l) / scale,
                "open_vs_pivot_atr": (session_open - pivot) / scale,
                "open_vs_cpr": cpr_pos,
                "vix": vix_now,
                "vix_change": vix_change,
                "prior_trend": trend,
                "prior_range_atr": (float(high[groups[i - 1][1]:groups[i - 1][2]].max()) - float(low[groups[i - 1][1]:groups[i - 1][2]].min())) / scale,
            },
            "close_vs_open_pct": (day_close / session_open - 1.0) * 100.0,
            "up_close": day_close > session_open,
            "broke_high": day_high > box_h,
            "broke_low": day_low < box_l,
            "day_type": "trend" if trend_day else "range",
            "known_through": int(times[kb]),
        })
    return rows


def _matrix(rows: list[dict], columns: list[str]) -> np.ndarray:
    return np.array([[row["features"][key] for key in columns] for row in rows], dtype=np.float64)


def _usable_columns(rows: list[dict]) -> list[str]:
    columns = []
    for key in FEATURE_KEYS:
        values = [row["features"][key] for row in rows]
        if all(v is not None and math.isfinite(float(v)) for v in values):
            columns.append(key)
    return columns


def _z(rows: list[dict], columns: list[str], mean: np.ndarray, scale: np.ndarray) -> np.ndarray:
    raw = _matrix(rows, columns)
    sd = np.where(scale < 1e-8, 1.0, scale)
    out = (raw - mean) / sd
    out[:, scale < 1e-8] = 0.0
    return out


def outcome_line(label: str, hits: int, n: int, base_rate: float | None) -> str:
    base = "—" if base_rate is None else f"{round(base_rate * 100)}%"
    return f"{label}: {hits}/{n} similar vs {base} all days"


def _rate(flags: list[bool]) -> float | None:
    if not flags:
        return None
    return float(np.mean(np.asarray(flags, dtype=np.float64)))


def describe_outcomes(matches: list[dict], population: list[dict]) -> dict:
    keys = (
        ("trend", "trend day", lambda row: row["day_type"] == "trend"),
        ("up_close", "close above open", lambda row: bool(row["up_close"])),
        ("broke_high", "high through the box", lambda row: bool(row["broke_high"])),
        ("broke_low", "low through the box", lambda row: bool(row["broke_low"])),
    )
    out = {}
    for key, label, fn in keys:
        hits = sum(1 for row in matches if fn(row))
        n = len(matches)
        base = _rate([fn(row) for row in population])
        out[key] = {
            "label": label,
            "hits": hits,
            "n": n,
            "base_rate": base,
            "base_n": len(population),
            "line": outcome_line(label, hits, n, base),
        }
    return out


def match_days(rows: list[dict], *, query_day: int, k: int = SIMILAR_DAYS) -> dict:
    """Nearest earlier sessions. Standardised on those earlier sessions only."""
    earlier = [row for row in rows if row["day"] < query_day and not row["holdout"]]
    query = next((row for row in rows if row["day"] == query_day), None)
    if query is None or len(earlier) < 5:
        return {"matches": [], "outcomes": describe_outcomes([], earlier), "columns": []}
    columns = _usable_columns(earlier + [query])
    if len(columns) < 4:
        return {"matches": [], "outcomes": describe_outcomes([], earlier), "columns": columns}
    raw = _matrix(earlier, columns)
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0)
    bank = _z(earlier, columns, mean, scale)
    probe = _z([query], columns, mean, scale)[0]
    order = np.argsort(euclidean(probe, bank), kind="mergesort")
    chosen = [earlier[int(i)] for i in order[:k]]
    distances = euclidean(probe, bank)
    packed = []
    dim = len(columns)
    for row in chosen:
        pos = earlier.index(row)
        dist = float(distances[pos])
        packed.append({
            "date": row["date"],
            "day": row["day"],
            "similarity": round(100.0 / (1.0 + dist / math.sqrt(dim)), 1),
            "close_vs_open_pct": round(row["close_vs_open_pct"], 3),
            "up_close": row["up_close"],
            "broke_high": row["broke_high"],
            "broke_low": row["broke_low"],
            "day_type": row["day_type"],
            "end_time": row["end_time"],
        })
    return {"matches": packed, "outcomes": describe_outcomes(chosen, earlier), "columns": columns}


def walk_days(rows: list[dict], *, time_cap_s: float = 30.0) -> dict:
    """One call per session, using only earlier sessions. Holdout sessions are skipped."""
    import time

    started = time.perf_counter()
    hits = 0
    calls = 0
    ups = 0
    evaluated = 0
    planned = sum(1 for row in rows if not row["holdout"])
    for i, query in enumerate(rows):
        if time.perf_counter() - started > time_cap_s:
            break
        if query["holdout"]:
            continue
        evaluated += 1
        earlier = [row for row in rows[:i] if not row["holdout"]]
        if len(earlier) < 20:
            continue
        columns = _usable_columns(earlier + [query])
        if len(columns) < 4:
            continue
        raw = _matrix(earlier, columns)
        mean = raw.mean(axis=0)
        scale = raw.std(axis=0)
        bank = _z(earlier, columns, mean, scale)
        probe = _z([query], columns, mean, scale)[0]
        dist = euclidean(probe, bank)
        take = min(SIMILAR_DAYS, len(dist))
        nearest = np.argpartition(dist, take - 1)[:take]
        votes = [earlier[int(j)]["up_close"] for j in nearest]
        call_up = sum(votes) > len(votes) / 2
        if sum(votes) == len(votes) / 2:
            continue
        calls += 1
        actual = bool(query["up_close"])
        if actual:
            ups += 1
        if call_up == actual:
            hits += 1
    runtime = time.perf_counter() - started
    hit = (hits / calls * 100.0) if calls else None
    up = (ups / calls * 100.0) if calls else None
    base = max(up, 100.0 - up) if up is not None else None
    return {
        "n": calls,
        "hit_rate": hit,
        "base_rate": base,
        "up_rate": up,
        "verdict": verdict(hit, base, calls) if hit is not None and base is not None else "too few samples",
        "sample_size": planned,
        "evaluated": evaluated,
        "runtime_s": round(runtime, 3),
        "capped": evaluated < planned,
        "holdout_excluded": "2026-07-01..2026-10-01",
    }


def population_before(rows: list[dict], query_day: int, as_of: int | None) -> list[dict]:
    out = []
    for row in rows:
        if row["day"] >= query_day or row["holdout"]:
            continue
        if as_of is not None and row["end_time"] > as_of:
            continue
        out.append(row)
    return out
