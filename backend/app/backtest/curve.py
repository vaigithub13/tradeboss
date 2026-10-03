"""Closed-trade equity and drawdown. The trough matches `compute_metrics` max drawdown."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.backtest.metrics import TradeRecord

CENT = Decimal("0.01")


def _money(x: Decimal) -> float:
    return float(x.quantize(CENT, rounding=ROUND_HALF_UP))


def equity_and_drawdown(trades: list[TradeRecord]) -> list[dict[str, float | int]]:
    """One point per closed trade, same order and paisa rounding as the metrics."""
    ordered = sorted(trades, key=lambda t: (t.exit_time, t.entry_time))
    equity = Decimal("0")
    peak = Decimal("0")
    out: list[dict[str, float | int]] = []
    for trade in ordered:
        equity += Decimal(str(round(trade.net_pnl, 2)))
        peak = max(peak, equity)
        out.append({
            "exit_time": trade.exit_time,
            "equity": _money(equity),
            "drawdown": _money(peak - equity),
        })
    return out
