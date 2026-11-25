"""Similar-days magnitude test. Offline. No orders, no UI.

At 09:30 and 10:15 the 20 nearest earlier sessions (holdout excluded) vote on
three outcomes. A call is useful only when it beats a two-feature baseline:
yesterday's range and India VIX.

Trend day is the market view's realised definition: the close-to-open move is
at least half the session range. The overnight gap is not a feature.
"""

from __future__ import annotations

import math

import numpy as np

from app.similar.days import SIMILAR_DAYS, _groups, _matrix, _minute, _usable_columns, _z
from app.similar.match import euclidean
from app.similar.walkforward import verdict

OPEN_MIN = 9 * 60 + 15
CLOCKS = {"09:30": 9 * 60 + 30, "10:15": 10 * 60 + 15}
TARGETS = ("range", "trend", "fhb")


def is_trend_day(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> bool:
    """Same rule the market view uses after the close."""
    span = float(np.max(high) - np.min(low))
    move = abs(float(close[-1]) - float(open_[0]))
    return bool(span > 0 and move >= 0.5 * span)


def rest_of_day_range(high: np.ndarray, low: np.ndarray, minute: np.ndarray, clock_minute: int) -> float | None:
    """High minus low of bars that start after the clock. The clock bar is already known."""
    after = minute > int(clock_minute)
    if not np.any(after):
        return None
    span = float(np.max(high[after]) - np.min(low[after]))
    if not math.isfinite(span) or span < 0:
        return None
    return span


def fhb_break(times: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> bool | None:
    """True when any completed 30m bar from 10:15 onward closes outside the 09:15–10:15 box.

    The box is the high and low of the 09:15 and 09:45 half-hours. A half-hour
    counts only when all six 5-minute bars are present. A missing box is None.
    """
    minute = _minute(times)
    rel = minute - OPEN_MIN
    keep = (rel >= 0) & (minute < 15 * 60 + 15)
    if not np.any(keep):
        return None
    slot = (rel[keep] // 30).astype(np.int64)
    high_k, low_k, close_k = high[keep], low[keep], close[keep]
    boxes: dict[int, tuple[float, float, float, int]] = {}
    for slot_id in np.unique(slot):
        mask = slot == int(slot_id)
        if int(np.sum(mask)) != 6:
            continue
        last = int(np.flatnonzero(mask)[-1])
        boxes[int(slot_id)] = (float(np.max(high_k[mask])), float(np.min(low_k[mask])), float(close_k[last]), int(np.sum(mask)))
    if 0 not in boxes or 1 not in boxes:
        return None
    box_high = max(boxes[0][0], boxes[1][0])
    box_low = min(boxes[0][1], boxes[1][1])
    broke = False
    for slot_id, (_h, _l, slot_close, _n) in boxes.items():
        if slot_id < 2:
            continue
        if slot_close > box_high or slot_close < box_low:
            broke = True
            break
    return broke


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    ordered = values[order]
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and ordered[j + 1] == ordered[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * ((i + 1) + (j + 1))
        i = j + 1
    return ranks


def spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    x = _rank(np.asarray(left, dtype=np.float64))
    y = _rank(np.asarray(right, dtype=np.float64))
    x = x - x.mean()
    y = y - y.mean()
    denom = math.sqrt(float(x @ x) * float(y @ y))
    if denom < 1e-12:
        return None
    return float(x @ y / denom)


def logistic_score(train_x: np.ndarray, train_y: np.ndarray, now: np.ndarray) -> float | None:
    """Walk-forward score from yesterday's range and VIX. None when a class is missing."""
    if len(train_x) < 30 or len(np.unique(train_y)) < 2:
        return None
    mu = train_x.mean(axis=0)
    sd = np.where(train_x.std(axis=0) < 1e-8, 1.0, train_x.std(axis=0))
    z_train = (train_x - mu) / sd
    design = np.column_stack([np.ones(len(z_train)), z_train])
    weights = np.zeros(design.shape[1], dtype=np.float64)
    target = train_y.astype(np.float64)
    for _ in range(25):
        eta = np.clip(design @ weights, -20.0, 20.0)
        prob = 1.0 / (1.0 + np.exp(-eta))
        grad = design.T @ (target - prob) / len(target)
        hess = design.T @ (design * (prob * (1.0 - prob))[:, None]) / len(target)
        hess.flat[:: hess.shape[0] + 1] += 1e-4
        try:
            weights = weights + np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            weights = weights + grad
    point = (np.asarray(now, dtype=np.float64) - mu) / sd
    eta_now = float(np.clip(np.concatenate([[1.0], point]) @ weights, -20.0, 20.0))
    return eta_now


def versus_baseline(
    panel_hit: float | None,
    baseline_hit: float | None,
    n: int,
    *,
    panel_rho: float | None = None,
    baseline_rho: float | None = None,
    require_rank: bool = False,
) -> str:
    """Useful only when the hit rate beats the baseline. Range also needs a stronger rank correlation."""
    if n < 10 or panel_hit is None or baseline_hit is None:
        return "too few samples"
    if panel_hit <= baseline_hit or abs(panel_hit - baseline_hit) <= 3.0:
        return "no better than the baseline"
    p = min(max(baseline_hit / 100.0, 1e-6), 1.0 - 1e-6)
    se = math.sqrt(p * (1.0 - p) / n) * 100.0
    z = (panel_hit - baseline_hit) / se if se > 0 else 0.0
    if z < 1.96:
        return "no better than the baseline"
    if require_rank and (panel_rho is None or baseline_rho is None or panel_rho <= baseline_rho):
        return "hit rate beats the baseline; rank correlation does not"
    return "beats the baseline"


def _majority(flags: list[bool]) -> bool | None:
    if len(flags) < SIMILAR_DAYS:
        return None
    ups = sum(1 for flag in flags if flag)
    if ups * 2 == len(flags):
        return None
    return ups > len(flags) / 2


def _nearest(rows: list[dict], index: int) -> list[dict]:
    query = rows[index]
    earlier = [row for row in rows[:index] if not row["holdout"]]
    if len(earlier) < SIMILAR_DAYS:
        return []
    columns = _usable_columns(earlier + [query])
    if len(columns) < 4:
        return []
    raw = _matrix(earlier, columns)
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0)
    bank = _z(earlier, columns, mean, scale)
    probe = _z([query], columns, mean, scale)[0]
    dist = euclidean(probe, bank)
    chosen = np.argpartition(dist, SIMILAR_DAYS - 1)[:SIMILAR_DAYS]
    return [earlier[int(pos)] for pos in chosen]


def session_facts(
    times: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    clock_minute: int,
) -> dict[int, dict]:
    """Outcomes for every session that has a previous day. Features are not read here."""
    facts: dict[int, dict] = {}
    groups = _groups(times)
    for i, (day, start, end) in enumerate(groups):
        if i == 0:
            continue
        prev_start, prev_end = groups[i - 1][1], groups[i - 1][2]
        sl = slice(start, end)
        minute = _minute(times[sl])
        facts[int(day)] = {
            "rest": rest_of_day_range(high[sl], low[sl], minute, clock_minute),
            "trend": is_trend_day(open_[sl], high[sl], low[sl], close[sl]),
            "fhb": fhb_break(times[sl], high[sl], low[sl], close[sl]),
            "yday_range": float(np.max(high[prev_start:prev_end]) - np.min(low[prev_start:prev_end])),
        }
    return facts


def _empty_target() -> dict:
    return {
        "calls": 0,
        "hits": 0,
        "positives": 0,
        "called_positive": 0,
        "paired": [],
        "rho_x": [],
        "rho_y": [],
        "rho_base": [],
    }


def _facts_from_rows(rows: list[dict]) -> dict[int, dict]:
    return {
        int(row["day"]): {
            "yday_range": row.get("yday_range"),
            "trend": row.get("trend"),
            "fhb": row.get("fhb"),
            "rest": row.get("rest"),
        }
        for row in rows
    }


def walk_magnitude(rows: list[dict], facts: dict[int, dict]) -> dict:
    """One call per non-holdout session. Matches and the median use only earlier sessions."""
    book = {name: _empty_target() for name in TARGETS}
    above: dict[int, bool] = {}
    history: list[float] = []
    for index, query in enumerate(rows):
        fact = facts.get(int(query["day"]))
        if query["holdout"] or fact is None or fact["rest"] is None:
            continue
        if len(history) >= SIMILAR_DAYS:
            above[int(query["day"])] = float(fact["rest"]) > float(np.median(history))
        history.append(float(fact["rest"]))
        matches = _nearest(rows, index)
        if len(matches) < SIMILAR_DAYS:
            continue
        vix = query["features"].get("vix")
        yday = fact.get("yday_range")
        baseline_x = None
        if vix is not None and yday is not None and math.isfinite(float(vix)) and math.isfinite(float(yday)):
            baseline_x = np.array([float(yday), float(vix)], dtype=np.float64)
        _vote_range(book["range"], matches, above, fact, rows, index, baseline_x)
        _vote_flag(book["trend"], matches, fact, "trend", rows, index, baseline_x)
        _vote_flag(book["fhb"], matches, fact, "fhb", rows, index, baseline_x)
    return {name: _finish(name, book[name], len([row for row in rows if not row["holdout"]])) for name in TARGETS}


def _past_matrix(rows: list[dict], facts: dict[int, dict], index: int, key: str, labels: dict[int, bool] | None) -> tuple[np.ndarray, np.ndarray] | None:
    xs: list[list[float]] = []
    ys: list[float] = []
    for row in rows[:index]:
        if row["holdout"]:
            continue
        fact = facts.get(int(row["day"]))
        vix = row["features"].get("vix")
        if fact is None or vix is None or not math.isfinite(float(vix)):
            continue
        if key == "range":
            if labels is None or int(row["day"]) not in labels:
                continue
            label = labels[int(row["day"])]
        else:
            label = fact.get(key)
            if label is None:
                continue
        yday = fact.get("yday_range")
        if yday is None or not math.isfinite(float(yday)):
            continue
        xs.append([float(yday), float(vix)])
        ys.append(1.0 if label else 0.0)
    if len(xs) < 30:
        return None
    return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)


def _baseline_call(rows: list[dict], index: int, now: np.ndarray | None, key: str, labels: dict[int, bool] | None, facts: dict[int, dict]) -> tuple[bool | None, float | None]:
    if now is None:
        return None, None
    packed = _past_matrix(rows, facts, index, key, labels)
    if packed is None:
        return None, None
    score = logistic_score(packed[0], packed[1], now)
    if score is None:
        return None, None
    if abs(score) < 1e-12:
        return None, score
    return bool(score > 0), score


def _vote_range(book: dict, matches: list[dict], above: dict[int, bool], fact: dict, rows: list[dict], index: int, now: np.ndarray | None) -> None:
    flags = [above[int(row["day"])] for row in matches if int(row["day"]) in above]
    ranges = [float(row["rest"]) for row in matches if row.get("rest") is not None]
    base_call, score = _baseline_call(rows, index, now, "range", above, _facts_from_rows(rows))
    if len(ranges) == SIMILAR_DAYS and fact["rest"] is not None:
        book["rho_x"].append(float(np.median(ranges)))
        book["rho_y"].append(float(fact["rest"]))
        book["rho_base"].append(np.nan if score is None else float(score))
    call = _majority(flags)
    actual = above.get(int(rows[index]["day"]))
    if call is None or actual is None:
        return
    book["calls"] += 1
    book["positives"] += int(actual)
    book["called_positive"] += int(call)
    book["hits"] += int(call == actual)
    if base_call is not None:
        book["paired"].append((call == actual, base_call == actual))


def _vote_flag(book: dict, matches: list[dict], fact: dict, key: str, rows: list[dict], index: int, now: np.ndarray | None) -> None:
    flags = []
    for row in matches:
        value = row.get(key)
        if value is None:
            continue
        flags.append(bool(value))
    actual = fact.get(key)
    if actual is None:
        return
    base_call, score = _baseline_call(rows, index, now, key, None, _facts_from_rows(rows))
    if len(flags) == SIMILAR_DAYS:
        book["rho_x"].append(float(np.mean(flags)))
        book["rho_y"].append(1.0 if actual else 0.0)
        book["rho_base"].append(np.nan if score is None else float(score))
    call = _majority(flags)
    if call is None:
        return
    book["calls"] += 1
    book["positives"] += int(bool(actual))
    book["called_positive"] += int(call)
    book["hits"] += int(call == bool(actual))
    if base_call is not None:
        book["paired"].append((call == bool(actual), base_call == bool(actual)))


def _rate(hits: int, n: int) -> float | None:
    if n <= 0:
        return None
    return round(100.0 * hits / n, 1)


def _finish(name: str, book: dict, planned: int) -> dict:
    calls = int(book["calls"])
    hit = _rate(book["hits"], calls)
    positive = _rate(book["positives"], calls)
    base = None if positive is None else round(max(positive, 100.0 - positive), 1)
    paired = book["paired"]
    panel_paired = _rate(sum(1 for panel, _base in paired if panel), len(paired))
    base_paired = _rate(sum(1 for _panel, base_hit in paired if base_hit), len(paired))
    rho_x = np.asarray(book["rho_x"], dtype=np.float64)
    rho_y = np.asarray(book["rho_y"], dtype=np.float64)
    rho_b = np.asarray(book["rho_base"], dtype=np.float64)
    rho = spearman(rho_x, rho_y)
    paired_rank = np.isfinite(rho_b) if len(rho_b) == len(rho_y) else np.zeros(len(rho_y), dtype=bool)
    rho_base = spearman(rho_b[paired_rank], rho_y[paired_rank]) if np.any(paired_rank) else None
    rho_panel_paired = spearman(rho_x[paired_rank], rho_y[paired_rank]) if np.any(paired_rank) else None
    return {
        "target": name,
        "calls": calls,
        "called_positive": _rate(book["called_positive"], calls),
        "hit_rate": hit,
        "positive_rate": positive,
        "base_rate": base,
        "verdict_vs_base": verdict(hit, base, calls) if hit is not None and base is not None else "too few samples",
        "paired": len(paired),
        "panel_hit_paired": panel_paired,
        "baseline_hit": base_paired,
        "spearman": None if rho is None else round(rho, 3),
        "spearman_n": len(book["rho_x"]),
        "baseline_spearman": None if rho_base is None else round(rho_base, 3),
        "baseline_spearman_n": int(np.sum(paired_rank)),
        "verdict": versus_baseline(
            panel_paired,
            base_paired,
            len(paired),
            panel_rho=rho_panel_paired,
            baseline_rho=rho_base,
            require_rank=name == "range",
        ),
        "sample_size": planned,
        "holdout_excluded": "2026-07-01..2026-10-01",
    }


def attach_facts(rows: list[dict], facts: dict[int, dict]) -> list[dict]:
    """Copy outcomes onto rows. Holdout rows stay in the list so the walker can skip them."""
    attached = []
    for row in rows:
        fact = facts.get(int(row["day"]))
        if fact is None:
            continue
        nxt = dict(row)
        nxt["rest"] = fact["rest"]
        nxt["trend"] = fact["trend"]
        nxt["fhb"] = fact["fhb"]
        nxt["yday_range"] = fact["yday_range"]
        attached.append(nxt)
    return attached


def evaluate_clock(rows: list[dict], facts: dict[int, dict]) -> dict:
    return walk_magnitude(attach_facts(rows, facts), facts)


def run_symbol(store, symbol: str = "NIFTY50") -> dict[str, dict]:
    """Score one symbol at 09:30 and 10:15. Reads candles. Writes nothing."""
    from app.similar.days import build_day_rows
    from app.similar.features import VIX_SYMBOL
    from app.similar.service import load_normal

    bars = load_normal(store, symbol, "5m")
    try:
        vix = load_normal(store, VIX_SYMBOL, "5m")
    except Exception:
        vix = {"times": np.empty(0, dtype=np.int64), "close": np.empty(0, dtype=np.float64)}
    reports = {}
    for clock, minute in CLOCKS.items():
        rows = build_day_rows(
            bars["times"], bars["open"], bars["high"], bars["low"], bars["close"],
            clock_minute=minute, vix_times=vix["times"], vix_close=vix["close"],
        )
        facts = session_facts(bars["times"], bars["open"], bars["high"], bars["low"], bars["close"], minute)
        reports[clock] = evaluate_clock(rows, facts)
    return reports


def format_report(symbol: str, reports: dict[str, dict]) -> str:
    lines = [
        f"{symbol} similar-days magnitude",
        "Trend day: |close-open| >= half the session range (market-view outcome).",
        "Rest-of-day range starts after the clock. First-hour break: any later 30m close outside the 09:15/09:45 box.",
        "Baseline: yesterday's range and India VIX. Holdout 2026-07-01..2026-10-01 excluded.",
    ]
    for clock, targets in reports.items():
        lines.append(clock)
        for name in TARGETS:
            block = targets[name]
            lines.append(
                f"  {name}: hit {block['hit_rate']}% vs base {block['base_rate']}% "
                f"on {block['calls']} calls (outcome {block['positive_rate']}%, called yes {block['called_positive']}%, "
                f"{block['verdict_vs_base']}; sample {block['sample_size']})"
            )
            lines.append(
                f"    paired {block['paired']}: panel {block['panel_hit_paired']}% vs baseline {block['baseline_hit']}% "
                f"— {block['verdict']}"
            )
            lines.append(
                f"    spearman {block['spearman']} (n={block['spearman_n']}) "
                f"baseline {block['baseline_spearman']} (n={block['baseline_spearman_n']})"
            )
    return "\n".join(lines)


def main() -> None:
    from app.config import settings
    from app.data.store import CandleStore

    store = CandleStore(settings.data_dir / "candles")
    reports = run_symbol(store, "NIFTY50")
    print(format_report("NIFTY50", reports), flush=True)


if __name__ == "__main__":
    main()
