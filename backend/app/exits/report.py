"""One trade-report row per trade, for paper day files and backtest results alike.

Signal bar time, trigger index price, fill time, contract, entry premium (real / modelled), stop and target levels
(index and premium), exit time, exit premium, exit reason (target / stop / reversal / square-off / ...), net after
costs, and the R multiple: net / risk, risk = (entry premium - premium stop) x units. Under a premium rule the index
levels are estimated through the model delta at entry; under an ATR rule the premium levels are. Without a rule
there are no levels and no R.
"""

from __future__ import annotations

from typing import Any

from app.exits.rules import ExitRule, premium_levels

COUNTED = ("target", "stop", "reversal", "square-off")
_CATEGORY = {
    "premium_target": "target", "index_target": "target", "target": "target",
    "premium_stop": "stop", "index_stop": "stop",
    "square_off": "square-off",
    "reverse": "reversal", "signal": "reversal", "reversal": "reversal",
    "session_end": "session end", "end_of_data": "session end",
    "exit": "exit signal",
    "stop": "stopped by user",  # paper: the strategy was stopped from the panel
}


def reason_category(raw: str | None) -> str:
    return _CATEGORY.get(str(raw), str(raw))


def report_row(
    *,
    signal_time: int | None,
    trigger_index: float | None,
    fill_time: int,
    contract: str,
    entry_premium: float,
    entry_source: str,
    units: int,
    rule: ExitRule | None,
    direction: str,
    index_entry: float | None,
    delta: float | None,
    exit_time: int | None,
    exit_premium: float | None,
    exit_reason: str | None,
    net: float | None,
    index_stop: float | None = None,
    index_target: float | None = None,
) -> dict[str, Any]:
    p_stop = p_target = None
    estimated: list[str] = []
    usable = delta is not None and abs(delta) > 1e-6 and index_entry is not None
    if rule is not None and rule.kind == "premium":
        p_stop, p_target = premium_levels(entry_premium, rule)
        if usable:
            index_stop = round(index_entry - (entry_premium - p_stop) / delta, 2)  # type: ignore[operator]
            index_target = round(index_entry + (p_target - entry_premium) / delta, 2)  # type: ignore[operator]
            estimated.append("index")
        else:
            index_stop = index_target = None
    elif rule is not None and rule.kind == "atr" and index_stop is not None and index_target is not None and usable:
        p_stop = round(entry_premium + delta * (index_stop - index_entry), 2)  # type: ignore[operator]
        p_target = round(entry_premium + delta * (index_target - index_entry), 2)  # type: ignore[operator]
        estimated.append("premium")
    risk = None if p_stop is None or p_stop >= entry_premium else round((entry_premium - p_stop) * units, 2)
    r_multiple = None if risk is None or net is None else round(float(net) / risk, 3)
    return {
        "direction": direction,
        "signal_time": signal_time,
        "trigger_index": trigger_index,
        "fill_time": fill_time,
        "contract": contract,
        "entry_premium": entry_premium,
        "entry_source": entry_source,
        "units": units,
        "rule": None if rule is None else rule.label,
        "index_stop": index_stop if rule is not None else None,
        "index_target": index_target if rule is not None else None,
        "premium_stop": p_stop,
        "premium_target": p_target,
        "estimated": estimated,
        "exit_time": exit_time,
        "exit_premium": exit_premium,
        "exit_reason": None if exit_reason is None else reason_category(exit_reason),
        "exit_reason_raw": exit_reason,
        "net": net,
        "risk": risk,
        "r_multiple": r_multiple,
    }


def reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Exits by kind: target, stop, reversal, square-off, and everything else as `other`."""
    out = {k: 0 for k in COUNTED}
    out["other"] = 0
    for row in rows:
        reason = row.get("exit_reason")
        if reason is None:
            continue
        if reason in COUNTED:
            out[reason] += 1
        else:
            out["other"] += 1
    return out
