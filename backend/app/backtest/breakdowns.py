"""Result splits: the option splits that must add back to the option net, and the stats per entry-tag context."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")
RATIO = Decimal("0.0001")
TAG_SEP = "|"  # `base|tag1,tag2`: context tags a strategy put on its entry (five_ema)
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


def entry_tags(entry_tag: str) -> list[str]:
    _, _, rest = entry_tag.partition(TAG_SEP)
    return [tag for tag in rest.split(",") if tag]


def _ratio(x: Decimal) -> float:
    return float(x.quantize(RATIO, rounding=ROUND_HALF_UP))


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [Decimal(str(round(float(t["net_pnl"]), 2))) for t in rows]
    rs = [Decimal(str(t["r"])) for t in rows if t.get("r") is not None]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    profit, loss = sum(wins, Decimal("0")), sum(losses, Decimal("0"))
    return {
        "trades": len(pnl),
        "wins": len(wins),
        "win_rate": _ratio(Decimal(len(wins)) / len(pnl)) if pnl else None,
        "avg_r": _ratio(sum(rs, Decimal("0")) / len(rs)) if rs else None,
        "profit_factor": _ratio(profit / -loss) if losses else None,
        "net_pnl": _money(sum(pnl, Decimal("0"))),
    }


def tag_breakdown(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Trades, wins, win rate, average R, profit factor and net per context tag, untagged first. A trade with two
    tags counts in both groups. Rows: `entry_tag`, `net_pnl`, `r` (None without a stop). Empty when no trade has a
    tag (the strategy does not tag)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        for tag in entry_tags(str(trade["entry_tag"])) or ["untagged"]:
            grouped.setdefault(tag, []).append(trade)
    if set(grouped) <= {"untagged"}:
        return {}
    order = (["untagged"] if "untagged" in grouped else []) + sorted(k for k in grouped if k != "untagged")
    return {name: _stats(grouped[name]) for name in order}
