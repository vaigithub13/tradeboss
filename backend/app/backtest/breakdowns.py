"""Option-result splits that must add back to the option net."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")
DTE_BUCKETS = ("0", "1", "2", "3-4", "5+")
FLAG_NAMES = ("expiry_day", "event_day", "reaction_day", "gap", "vix_stale", "expired_while_held", "modelled")


def _money(x: Decimal) -> float:
    return float(x.quantize(CENT, rounding=ROUND_HALF_UP))


def dte_bucket(dte: int) -> str:
    if dte <= 0:
        return "0"
    if dte == 1:
        return "1"
    if dte == 2:
        return "2"
    if dte <= 4:
        return "3-4"
    return "5+"


def _block(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    return {
        "trades": len(rows),
        "net_pnl": _money(sum((Decimal(str(t["net_pnl"])) for t in rows), Decimal("0"))),
    }


def dte_breakdown(trades: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(dte_bucket(int(trade["dte"])), []).append(trade)
    return {name: _block(grouped[name]) for name in DTE_BUCKETS if name in grouped}


def flag_breakdown(trades: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float | int]]]:
    out: dict[str, dict[str, dict[str, float | int]]] = {}
    for name in FLAG_NAMES:
        flagged = [t for t in trades if name in t.get("flags", [])]
        clean = [t for t in trades if name not in t.get("flags", [])]
        out[name] = {"flagged": _block(flagged), "unflagged": _block(clean)}
    return out
