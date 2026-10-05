"""End-of-day check: the live signals against the normal backtest on the same day.

The backtest is the real engine (`run_backtest`) on the stored candles, with the same strategy and
parameters. A backtest entry fills at the next bar's open, so its decision bar starts one bar earlier.
Each mismatch gets a reason: a live gap, a different bar, or the same bar with a different state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.contracts import Strategy
from app.backtest.costs import get_cost_model
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.sources import StoreSource
from app.data.resampler import resample
from app.data.store import CandleStore
from app.data.sessions import DEFAULT_INCLUDED_SESSION_TYPES

IST = timezone(timedelta(hours=5, minutes=30))
PRICE_TOLERANCE = 0.005


def _hhmm(t: int) -> str:
    return datetime.fromtimestamp(t, IST).strftime("%H:%M")


def _bar_diff(live: dict[str, Any], bt: dict[str, Any]) -> str | None:
    parts = []
    for field in ("open", "high", "low", "close"):
        a, b = float(live[field]), float(bt[field])
        if abs(a - b) > PRICE_TOLERANCE:
            parts.append(f"{field} {a} live vs {b} backtest")
    return "bar differs: " + "; ".join(parts) if parts else None


def _reason(t: int, present: str, *, live_bars: dict[int, dict], bt_bars: dict[int, dict],
            incomplete: set[int]) -> str:
    """Why the other side has no signal at `t`. `present` is the side that does have it."""
    if present == "backtest":
        if t in incomplete:
            return "live 5m bar incomplete (feed gap)"
        if t not in live_bars:
            return "no live bar at this time"
    else:
        if t not in bt_bars:
            return "no backtest bar at this time"
    if t not in live_bars or t not in bt_bars:
        return "no bar to compare"
    diff = _bar_diff(live_bars[t], bt_bars[t])
    if diff is not None:
        return diff
    return "same bar and prices, so the strategy state differed (position or warm-up)"


def compare_signals(
    live: list[dict[str, Any]],
    backtest: list[dict[str, Any]],
    *,
    live_bars: dict[int, dict[str, Any]],
    bt_bars: dict[int, dict[str, Any]],
    incomplete: set[int],
) -> list[dict[str, Any]]:
    """One entry per (time, side) that only one side has: {time: HH:MM IST, side, source: the side that HAS it,
    reason}. Empty when they agree."""
    lk = {(e["time"], e["side"]) for e in live}
    bk = {(e["time"], e["side"]) for e in backtest}
    diffs = []
    for t, side in sorted(bk - lk):
        diffs.append({"time": _hhmm(t), "side": side, "source": "backtest",
                      "reason": _reason(t, "backtest", live_bars=live_bars, bt_bars=bt_bars, incomplete=incomplete)})
    for t, side in sorted(lk - bk):
        diffs.append({"time": _hhmm(t), "side": side, "source": "live",
                      "reason": _reason(t, "live", live_bars=live_bars, bt_bars=bt_bars, incomplete=incomplete)})
    return diffs


def backtest_day(store: CandleStore, symbol: str, day: date, strategy: Strategy,
                 timeframe: str = "5m") -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """The normal backtest on one day: its entries (decision bar time, side) and its bars."""
    config = BacktestConfig(
        timeframe=timeframe,
        start=day.isoformat(),
        end=day.isoformat(),
        session_types=tuple(DEFAULT_INCLUDED_SESSION_TYPES),
        underlying=None,
        lot_table=None,
        lot_size=1,
        contract=None,
        cost_model=get_cost_model("zero"),
    )
    result = run_backtest(strategy, StoreSource(store, symbol), config)
    step = 60 * int(timeframe[:-1])
    entries = [
        {"time": t.entry_time - step, "side": "BUY" if t.direction == "LONG" else "SELL"}
        for t in result.trades
    ]
    start = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    minutes, _ = store.load(symbol, from_time=start, to_time=start + 86_400, session_types=DEFAULT_INCLUDED_SESSION_TYPES)
    bars = {int(c["time"]): c for c in resample(minutes, timeframe)}
    return entries, bars


def end_of_day_check(
    *,
    day: date,
    live_entries: list[dict[str, Any]],
    live_bars: dict[int, dict[str, Any]],
    incomplete: set[int],
    store: CandleStore,
    symbol: str,
    make_strategy: Callable[[], Strategy],
    timeframe: str = "5m",
) -> dict[str, Any]:
    backtest, bt_bars = backtest_day(store, symbol, day, make_strategy(), timeframe)
    diffs = compare_signals(live_entries, backtest, live_bars=live_bars, bt_bars=bt_bars, incomplete=incomplete)
    return {
        "day": day.isoformat(),
        "live_signals": len(live_entries),
        "backtest_signals": len(backtest),
        "differences": diffs,
    }
