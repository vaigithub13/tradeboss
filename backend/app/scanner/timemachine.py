"""Bootstrap the rest of the session from the same clock-time candles, with a fixed seed."""

from __future__ import annotations

from datetime import date

import numpy as np

from app.scanner.bars import Bar, forward_minutes, minute_of
from app.scanner.costs import fast_exit_costs, order_sides
from app.scanner.models import TimeMachineResult
from app.backtest.instruments import cost_model_for


def ratio_buckets(sessions: list[list[Bar]]) -> dict[int, list[tuple[float, float, float, float]]]:
    """Each past bar becomes open/high/low/close as a multiple of the previous close in that session."""
    buckets: dict[int, list[tuple[float, float, float, float]]] = {}
    for session in sessions:
        prev: float | None = None
        for bar in session:
            close = float(bar["close"])
            if prev is not None and prev > 0:
                buckets.setdefault(minute_of(int(bar["time"])), []).append(
                    (
                        float(bar["open"]) / prev,
                        float(bar["high"]) / prev,
                        float(bar["low"]) / prev,
                        close / prev,
                    )
                )
            prev = close
    return buckets


def _both_order(open_px: np.ndarray, stop: float, target: float) -> np.ndarray:
    """True where the stop is closer to the open than the target (tie goes to the stop)."""
    return np.abs(open_px - stop) <= np.abs(open_px - target)


def simulate(
    *,
    entry: float,
    stop: float,
    target: float,
    side: str,
    buckets: dict[int, list[tuple[float, float, float, float]]],
    minutes: list[int],
    paths: int,
    seed: int,
    qty: int,
    on: date,
) -> TimeMachineResult:
    rng = np.random.default_rng(seed)
    n = int(paths)
    price = np.full(n, float(entry))
    outcome = np.zeros(n, dtype=np.int8)  # 0 still open, 1 target, 2 stop
    exit_px = np.full(n, float(entry))
    for minute in minutes:
        pool = buckets.get(minute) or []
        active = outcome == 0
        if not pool or not np.any(active):
            continue
        ratios = np.asarray(pool, dtype=float)
        pick = rng.integers(0, len(ratios), size=n)
        chosen = ratios[pick]
        o = price * chosen[:, 0]
        h = price * chosen[:, 1]
        l = price * chosen[:, 2]
        c = price * chosen[:, 3]
        # keep a candle legal if a sampled ratio is out of order
        h = np.maximum(h, np.maximum(o, c))
        l = np.minimum(l, np.minimum(o, c))
        if side == "long":
            stop_hit = l <= stop
            target_hit = h >= target
        else:
            stop_hit = h >= stop
            target_hit = l <= target
        both = active & stop_hit & target_hit
        stop_first = both & _both_order(o, stop, target)
        target_first = both & ~stop_first
        only_stop = active & stop_hit & ~target_hit
        only_target = active & target_hit & ~stop_hit
        became_stop = stop_first | only_stop
        became_target = target_first | only_target
        outcome = np.where(became_stop, 2, outcome)
        outcome = np.where(became_target, 1, outcome)
        exit_px = np.where(became_stop, stop, exit_px)
        exit_px = np.where(became_target, target, exit_px)
        still = outcome == 0
        price = np.where(still, c, price)
        exit_px = np.where(still, c, exit_px)

    target_n = int(np.sum(outcome == 1))
    stop_n = int(np.sum(outcome == 2))
    open_n = int(np.sum(outcome == 0))
    sign = 1.0 if side == "long" else -1.0
    gross = (exit_px - entry) * sign * qty
    entry_side, _exit_side = order_sides(side)
    entry_cost = _entry_cost(entry_side, entry, qty, on)
    exit_costs = fast_exit_costs(side, exit_px, qty, on)
    pnl = gross - entry_cost - exit_costs
    hist = {
        "target": target_n,
        "stop": stop_n,
        "open": open_n,
    }
    return TimeMachineResult(
        paths=n,
        seed=int(seed),
        target_pct=100.0 * target_n / n,
        stop_pct=100.0 * stop_n / n,
        open_pct=100.0 * open_n / n,
        avg_rupees=float(np.mean(pnl)),
        histogram=hist,
    )


def _entry_cost(entry_side: str, entry: float, qty: int, on: date) -> float:
    model = cost_model_for("equity_intraday")
    return float(model.leg_cost(entry_side, entry, qty, on).total)


def run_time_machine(spec: dict) -> dict:
    """Picklable entry point for a process pool. `spec` holds plain numbers and ratio lists."""
    buckets = {int(k): [tuple(row) for row in rows] for k, rows in spec["buckets"].items()}
    result = simulate(
        entry=float(spec["entry"]),
        stop=float(spec["stop"]),
        target=float(spec["target"]),
        side=str(spec["side"]),
        buckets=buckets,
        minutes=[int(m) for m in spec["minutes"]],
        paths=int(spec["paths"]),
        seed=int(spec["seed"]),
        qty=int(spec["qty"]),
        on=date.fromisoformat(spec["on"]),
    )
    return result.to_dict()


def minutes_for(entry_time: int, *, include_fill_bar: bool) -> list[int]:
    return forward_minutes(minute_of(entry_time), include_fill_bar=include_fill_bar)
