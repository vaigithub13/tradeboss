"""Slot 8 cash book. It records a paper fill. It does not send an order."""

from __future__ import annotations

from datetime import date

from app.scanner.bars import ENTRY_CUTOFF_MIN, LAST_BAR_MIN, Bar, minute_of
from app.scanner.config import ScannerSettings
from app.scanner.costs import round_trip, slip_points
from app.scanner.history import bar_exit


def open_position(row: dict, *, day: date) -> dict:
    return {
        "symbol": row["symbol"],
        "setup": row["setup"],
        "side": row["side"],
        "instrument": row.get("instrument", "equity_intraday"),
        "qty": int(row["qty"]),
        "entry": float(row["entry"]),
        "stop": float(row["stop"]),
        "target": float(row["target"]),
        "initial_stop": float(row["stop"]),
        "entry_time": int(row["entry_time"]),
        "target_r": float(row.get("target_r") or 2),
        "risk_per_share": float(row["risk_per_share"]),
        "day": day.isoformat(),
        "tm_target_pct": row.get("target_pct"),
        "tm_stop_pct": row.get("stop_pct"),
        "tm_open_pct": row.get("open_pct"),
        "tm_avg_rupees": row.get("avg_rupees"),
        "history_win_rate": row.get("win_rate"),
        "verdict": row.get("verdict"),
        "trail_armed": False,
    }


def take_signals(rows: list[dict], settings: ScannerSettings, state: dict) -> list[dict]:
    """Move the best PASS rows into `state["open"]`, up to the trades still allowed."""
    if not settings.auto_trade:
        return []
    ranked = sorted(
        [r for r in rows if r.get("decision") == "PASS" and r.get("status") == "triggered"],
        key=lambda r: (
            1 if r.get("proven") else 0,
            float(r.get("target_pct") or 0) - float(r.get("stop_pct") or 0),
            float(r.get("win_rate") or 0),
        ),
        reverse=True,
    )
    taken: list[dict] = []
    open_names = {p["symbol"] for p in state["open"]}
    for row in ranked:
        if len(state["trades"]) + len(state["open"]) >= settings.max_trades_per_day:
            break
        if len(state["open"]) >= settings.max_open_positions:
            break
        if float(state["net"]) <= -settings.daily_loss_limit:
            break
        entry_time = row.get("entry_time")
        if entry_time is not None and minute_of(int(entry_time)) >= ENTRY_CUTOFF_MIN:
            continue
        if row["symbol"] in open_names:
            continue
        pos = open_position(row, day=date.fromisoformat(state["day"]))
        state["open"].append(pos)
        open_names.add(pos["symbol"])
        row["status"] = "in_trade"
        taken.append(row)
    return taken


def on_bar(state: dict, symbol: str, bar: Bar, settings: ScannerSettings) -> list[dict]:
    """Apply one closed bar to this symbol's open position. Trail moves the stop only after the bar."""
    closed: list[dict] = []
    still: list[dict] = []
    for pos in state["open"]:
        if pos["symbol"] != symbol:
            still.append(pos)
            continue
        if int(bar["time"]) <= int(pos["entry_time"]):
            still.append(pos)
            continue
        reason = bar_exit(bar, side=pos["side"], stop=float(pos["stop"]), target=float(pos["target"]))
        if reason is None and minute_of(int(bar["time"])) >= LAST_BAR_MIN:
            reason = "time"
        if reason is None:
            _arm_trail(pos, bar, settings)
            still.append(pos)
            continue
        exit_px = float(pos["stop"]) if reason == "stop" else float(pos["target"]) if reason == "target" else float(bar["close"])
        trade = _close(pos, exit_px, reason, int(bar["time"]), settings)
        state["trades"].append(trade)
        state["net"] = float(state["net"]) + trade["net"]
        closed.append(trade)
    state["open"] = still
    return closed


def flatten(state: dict, last_prices: dict[str, tuple[float, int]], settings: ScannerSettings) -> list[dict]:
    closed: list[dict] = []
    for pos in list(state["open"]):
        px, ts = last_prices.get(pos["symbol"], (float(pos["entry"]), int(pos["entry_time"])))
        trade = _close(pos, px, "time", ts, settings)
        state["trades"].append(trade)
        state["net"] = float(state["net"]) + trade["net"]
        closed.append(trade)
    state["open"] = []
    state["flattened"] = True
    return closed


def _arm_trail(pos: dict, bar: Bar, settings: ScannerSettings) -> None:
    if not settings.trail_breakeven or pos["trail_armed"]:
        return
    risk = float(pos["risk_per_share"])
    if pos["side"] == "long" and float(bar["high"]) >= float(pos["entry"]) + risk:
        pos["stop"] = float(pos["entry"])
        pos["trail_armed"] = True
    elif pos["side"] == "short" and float(bar["low"]) <= float(pos["entry"]) - risk:
        pos["stop"] = float(pos["entry"])
        pos["trail_armed"] = True


def _close(pos: dict, exit_price: float, reason: str, exit_time: int, settings: ScannerSettings) -> dict:
    slip = slip_points(settings.slippage_ticks, settings.tick_size)
    money = round_trip(
        position_side=pos["side"],
        entry=float(pos["entry"]),
        exit_price=exit_price,
        qty=int(pos["qty"]),
        on=date.fromisoformat(pos["day"]),
        slippage=slip,
    )
    risk = float(pos["risk_per_share"]) or 1.0
    if reason == "target":
        r = float(pos["target_r"])
    elif reason == "stop":
        r = -1.0 if abs(float(pos["stop"]) - float(pos["initial_stop"])) < 1e-9 else 0.0
    else:
        signed = (exit_price - float(pos["entry"])) if pos["side"] == "long" else (float(pos["entry"]) - exit_price)
        r = signed / risk
    return {
        "symbol": pos["symbol"],
        "setup": pos["setup"],
        "side": pos["side"],
        "instrument": pos["instrument"],
        "qty": pos["qty"],
        "entry": pos["entry"],
        "exit": exit_price,
        "stop": pos["initial_stop"],
        "target": pos["target"],
        "entry_time": pos["entry_time"],
        "exit_time": exit_time,
        "exit_reason": reason,
        "r": r,
        "gross": money["gross"],
        "charges": money["charges"],
        "net": money["net"],
        "tm_target_pct": pos.get("tm_target_pct"),
        "tm_stop_pct": pos.get("tm_stop_pct"),
        "tm_open_pct": pos.get("tm_open_pct"),
        "tm_avg_rupees": pos.get("tm_avg_rupees"),
        "history_win_rate": pos.get("history_win_rate"),
        "verdict": pos.get("verdict"),
    }
