"""Performance metrics on a trade list, all AFTER costs. Spec: tests/test_bt_metrics.py.

Max drawdown is measured on closed-trade equity starting from 0 (the start is a peak). A trade
with net P&L exactly 0 is neither a win nor a loss and ends a losing streak. Money totals are
rounded to the paisa, averages and ratios to 4 decimals. Time-of-day buckets are clock-aligned
30-minute slots of the ENTRY time (IST); weekdays are the entry day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
CENT = Decimal("0.01")
RATIO = Decimal("0.0001")


@dataclass(frozen=True)
class TradeRecord:
    entry_time: int
    exit_time: int
    net_pnl: float


def _money(x: Decimal) -> float:
    return float(x.quantize(CENT, rounding=ROUND_HALF_UP))


def _ratio(x: Decimal) -> float:
    return float(x.quantize(RATIO, rounding=ROUND_HALF_UP))


def compute_metrics(trades: list[TradeRecord]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda t: (t.exit_time, t.entry_time))
    pnl = [Decimal(str(round(t.net_pnl, 2))) for t in ordered]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    n = len(pnl)
    net = sum(pnl, Decimal("0"))
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum(losses, Decimal("0"))

    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    streak = longest = 0
    for p in pnl:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if p < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0

    by_day: dict[str, list[Decimal]] = {}
    by_slot: dict[str, list[Decimal]] = {}
    for t, p in zip(ordered, pnl):
        local = datetime.fromtimestamp(t.entry_time, IST)
        by_day.setdefault(WEEKDAYS[local.weekday()], []).append(p)
        minute = (local.hour * 60 + local.minute) // 30 * 30
        by_slot.setdefault(f"{minute // 60:02d}:{minute % 60:02d}", []).append(p)

    def block(d: dict[str, list[Decimal]], order: list[str]) -> dict[str, dict[str, Any]]:
        return {k: {"trades": len(d[k]), "net_pnl": _money(sum(d[k], Decimal("0")))} for k in order if k in d}

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": n - len(wins) - len(losses),
        "net_pnl": _money(net),
        "gross_profit": _money(gross_profit),
        "gross_loss": _money(gross_loss),
        "win_rate": _ratio(Decimal(len(wins)) / n) if n else None,
        "avg_win": _ratio(gross_profit / len(wins)) if wins else None,
        "avg_loss": _ratio(gross_loss / len(losses)) if losses else None,
        "expectancy": _ratio(net / n) if n else None,
        "profit_factor": _ratio(gross_profit / -gross_loss) if losses else None,
        "max_drawdown": _money(max_dd),
        "longest_losing_streak": longest,
        "pnl_by_weekday": block(by_day, [d for d in WEEKDAYS]),
        "pnl_by_time_of_day": block(by_slot, sorted(by_slot)),
    }
