"""Walk stored 5-minute history with the same plan, slippage, and equity intraday charges."""

from __future__ import annotations

from datetime import date

from app.scanner.bars import LAST_BAR_MIN, Bar, day_of, minute_of, session_for
from app.scanner.config import ScannerSettings
from app.scanner.costs import round_trip, slip_points
from app.scanner.history import bar_exit, walk
from app.scanner.plan import build_plan
from app.scanner.setups import run_setups


def _days(bars: list[Bar]) -> list[date]:
    seen: list[date] = []
    cur: date | None = None
    for bar in bars:
        d = day_of(int(bar["time"]))
        if d != cur:
            seen.append(d)
            cur = d
    return seen


def _prev(bars: list[Bar], day: date) -> float | None:
    prior = [b for b in bars if day_of(int(b["time"])) < day]
    return float(prior[-1]["close"]) if prior else None


def backtest_symbol(symbol: str, bars: list[Bar], settings: ScannerSettings) -> list[dict]:
    """One position at a time. A later signal waits until the open trade has closed."""
    slip = slip_points(settings.slippage_ticks, settings.tick_size)
    trades: list[dict] = []
    busy_until = -1
    for day in _days(bars):
        prev = _prev(bars, day)
        window_from_bars = [b for b in bars if day_of(int(b["time"])) <= day][-400:]
        hits = run_setups(window_from_bars, enabled=settings.setups, prev_close=prev, as_of=None)
        day_hits = [h for h in hits if not h.forming and day_of(h.time) == day]
        day_hits.sort(key=lambda h: h.time)
        for hit in day_hits:
            if hit.time <= busy_until:
                continue
            nxt = next((b for b in bars if int(b["time"]) > hit.time), None)
            plan = build_plan(hit, settings, symbol=symbol, next_bar=nxt)
            if (
                plan.entry is None
                or plan.stop is None
                or plan.target is None
                or plan.qty < 1
                or plan.rr is None
                or plan.rr < settings.min_rr
            ):
                continue
            after = [b for b in session_for(bars, day) if int(b["time"]) > plan.entry_time]
            reason, exit_px, r = walk(
                after,
                side=plan.side,
                entry=plan.entry,
                stop=plan.stop,
                target=plan.target,
                target_r=plan.target_r,
            )
            exit_time = plan.entry_time
            for bar in after:
                if minute_of(int(bar["time"])) > LAST_BAR_MIN:
                    break
                exit_time = int(bar["time"])
                if bar_exit(bar, side=plan.side, stop=float(plan.stop), target=float(plan.target)):
                    break
            money = round_trip(
                position_side=plan.side,
                entry=plan.entry,
                exit_price=exit_px,
                qty=plan.qty,
                on=day,
                slippage=slip,
            )
            trades.append(
                {
                    "symbol": symbol,
                    "setup": hit.setup,
                    "side": plan.side,
                    "day": day.isoformat(),
                    "entry": plan.entry,
                    "exit": exit_px,
                    "qty": plan.qty,
                    "r": r,
                    "reason": reason,
                    "gross": money["gross"],
                    "charges": money["charges"],
                    "net": money["net"],
                    "exit_time": exit_time,
                }
            )
            busy_until = exit_time
    return trades


def summarize(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] < 0]
    gross_win = sum(t["net"] for t in wins)
    gross_loss = sum(t["net"] for t in losses)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        equity += float(trade["net"])
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    n = len(trades)
    pf = None
    if gross_loss < 0:
        pf = gross_win / abs(gross_loss)
    elif gross_win > 0:
        pf = None
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n) if n else None,
        "avg_r": (sum(float(t["r"]) for t in trades) / n) if n else None,
        "profit_factor": pf,
        "max_drawdown": max_dd,
        "net": sum(float(t["net"]) for t in trades),
    }


def rank_pairs(by_symbol: dict[str, list[Bar]], settings: ScannerSettings) -> list[dict]:
    rows: list[dict] = []
    grouped: dict[tuple[str, str], list[dict]] = {}
    for symbol, bars in by_symbol.items():
        for trade in backtest_symbol(symbol, bars, settings):
            grouped.setdefault((symbol, trade["setup"]), []).append(trade)
    for (symbol, setup), trades in sorted(grouped.items()):
        rows.append({"symbol": symbol, "setup": setup, **summarize(trades)})
    rows.sort(key=lambda r: float(r["net"]), reverse=True)
    return rows
