"""The shadow exit: what a resting exchange order would have done in one exchange-final option minute.

Paper's premium stop and target trigger on the traded prices the feed sends (LTP snapshots). The exchange's I1 minute
also holds the trades between those snapshots, so its low or high can cross a level no tick reached (slot 4, 4 Nov
2026: the 10:23 bar's low 140.25, paper's lowest tick 141.80, stop 141.24). A resting order would have exited in that
minute: a stop at the level, or at the bar's low when the bar opened through it; a target at the level. Paper's own
exit is kept; the shadow is recorded beside it.
"""

from __future__ import annotations

from typing import Any

from app.exits.rules import first_hit


def shadow_exit(levels: dict[str, float | None] | None, bar: dict[str, float], *, tick_low: float | None,
                tick_high: float | None) -> dict[str, Any] | None:
    """The resting order's exit in `bar` (a bought option's 1m open/high/low/close), or None: no premium level
    crossed, or paper's ticks in that minute (`tick_low`, `tick_high`; None without ticks) reached it."""
    if not levels:
        return None
    stop, target = levels.get("premium_stop"), levels.get("premium_target")
    o, h, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
    hit = first_hit(stop, target, o, h, lo, long=True)
    if hit is None:
        return None
    reason = hit[0]
    if reason == "stop":
        assert stop is not None
        if tick_low is not None and tick_low <= stop:
            return None
        price = lo if o <= stop else float(stop)
    else:
        assert target is not None
        if tick_high is not None and tick_high >= target:
            return None
        price = float(target)
    return {"level": f"premium_{reason}", "reason": reason, "price": price,
            "bar": {"open": o, "high": h, "low": lo, "close": float(bar["close"])},
            "tick_low": tick_low, "tick_high": tick_high}


def priced(sh: dict[str, Any], *, minute: int, entry: float, units: int, charges_entry: float, cost_table: Any,
           day: Any) -> dict[str, Any]:
    """The shadow with its minute and its net: the trade's entry and entry charges, the exit sold at the shadow's
    price with that leg's charges."""
    gross = round((sh["price"] - entry) * units, 2)
    charges_exit = round(float(cost_table.leg_cost("SELL", sh["price"], units, day).total), 2)
    return {"time": minute, **sh, "gross": gross, "charges_exit": charges_exit,
            "net": round(gross - round(charges_entry, 2) - charges_exit, 2)}


def report_shadow(trade: dict[str, Any]) -> dict[str, Any] | None:
    """The trade report's view of a trade's shadow: {exit_time, exit_premium, exit_reason, net}, or None."""
    sh = trade.get("shadow")
    if not sh:
        return None
    return {"exit_time": int(sh["time"]), "exit_premium": float(sh["price"]), "exit_reason": sh["reason"],
            "net": float(sh["net"])}


def shadow_net(trade: dict[str, Any]) -> float:
    """The trade's net had a resting order exited it: the shadow's net, else paper's own."""
    sh = trade.get("shadow")
    return float(sh["net"]) if sh else float(trade["net"])
