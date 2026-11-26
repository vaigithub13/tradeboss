"""Closed-trade stats for a deployment check. Research only. No orders.

Money follows the backtest: each trade is rounded to the paisa, equity starts at 0,
and a trade that nets exactly 0 is neither a win nor a loss and ends a losing streak.
A drawdown's length is calendar days in IST from the exit that set the peak to the
exit that makes a new peak, or to the last exit if the book is still under water.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.backtest.metrics import CENT, TradeRecord, compute_metrics

IST = timezone(timedelta(hours=5, minutes=30))


def _money(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _day(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, IST)


def losing_streaks(pnls: list[Decimal]) -> dict[str, float | int]:
    """The longest run of losses, and the run whose rupee sum is the most negative."""
    longest_n = 0
    longest_sum = Decimal("0")
    deepest_n = 0
    deepest_sum = Decimal("0")
    run_n = 0
    run_sum = Decimal("0")
    for pnl in pnls:
        if pnl < 0:
            run_n += 1
            run_sum += pnl
            if run_n > longest_n or (run_n == longest_n and run_sum < longest_sum):
                longest_n = run_n
                longest_sum = run_sum
            if run_sum < deepest_sum:
                deepest_n = run_n
                deepest_sum = run_sum
        else:
            run_n = 0
            run_sum = Decimal("0")
    return {
        "longest_trades": longest_n,
        "longest_rupees": _money(longest_sum),
        "deepest_trades": deepest_n,
        "deepest_rupees": _money(deepest_sum),
    }


def worst_month(trades: list[TradeRecord]) -> dict[str, float | str] | None:
    """The exit month (IST) with the lowest net. A flat book has no worst month."""
    if not trades:
        return None
    totals: dict[str, Decimal] = {}
    for trade in trades:
        key = _day(trade.exit_time).strftime("%Y-%m")
        totals[key] = totals.get(key, Decimal("0")) + Decimal(str(round(trade.net_pnl, 2)))
    month = min(totals, key=lambda key: (totals[key], key))
    return {"month": month, "net": _money(totals[month])}


def longest_drawdown_days(trades: list[TradeRecord]) -> int:
    """Calendar days under water. Zero when every close is a new high."""
    ordered = sorted(trades, key=lambda trade: (trade.exit_time, trade.entry_time))
    equity = Decimal("0")
    peak = Decimal("0")
    peak_at: int | None = None
    underwater_from: int | None = None
    longest = 0
    for trade in ordered:
        equity += Decimal(str(round(trade.net_pnl, 2)))
        if equity >= peak:
            if underwater_from is not None:
                longest = max(longest, (_day(trade.exit_time).date() - _day(underwater_from).date()).days)
                underwater_from = None
            peak = equity
            peak_at = trade.exit_time
        elif underwater_from is None:
            underwater_from = peak_at if peak_at is not None else trade.exit_time
    if underwater_from is not None and ordered:
        last = ordered[-1].exit_time
        longest = max(longest, (_day(last).date() - _day(underwater_from).date()).days)
    return longest


def summarise(trades: list[TradeRecord]) -> dict:
    """One window, or one exit year. Drawdown and the streak use closed-trade order."""
    metrics = compute_metrics(trades)
    ordered = sorted(trades, key=lambda trade: (trade.exit_time, trade.entry_time))
    pnls = [Decimal(str(round(trade.net_pnl, 2))) for trade in ordered]
    streaks = losing_streaks(pnls)
    return {
        "trades": int(metrics["trades"]),
        "wins": int(metrics["wins"]),
        "win_rate": metrics["win_rate"],
        "net": metrics["net_pnl"],
        "profit_factor": metrics["profit_factor"],
        "max_dd": metrics["max_drawdown"],
        "streak": streaks,
        "worst_month": worst_month(ordered),
        "dd_days": longest_drawdown_days(ordered),
    }


def by_year(trades: list[TradeRecord]) -> list[dict]:
    """The same stats for each exit year. A year's drawdown starts again at zero."""
    grouped: dict[int, list[TradeRecord]] = {}
    for trade in trades:
        grouped.setdefault(_day(trade.exit_time).year, []).append(trade)
    rows = []
    for year in sorted(grouped):
        stats = summarise(grouped[year])
        stats["year"] = year
        rows.append(stats)
    return rows
