"""End-of-day check: the paper trades against the normal backtest on the same day.

The backtest is the real engine (`run_backtest`) on the stored candles, with the same strategy, parameters and exit
rule, on live timing (a market entry fills one minute after its decision bar ends, as in paper), then the option
overlay (the contract at the fill, delta-adjusted fills, slippage 0.2, the premium walk on the stored option bars).

Trades are matched one to one on the direction and the fill minute, and compared on the contract, the exit minute and
the exit reason (M1, review 2026-10-11). A backtest trade with no paper trade is a status difference: the paper
signal's own status and note when paper decided it but did not fill (unfilled, after the square-off), otherwise the
bar reason (a live gap, a different bar, or the same bar with a different state).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.contracts import Strategy
from app.backtest.costs import get_cost_model
from app.backtest.engine import LIVE_LAG_S, BacktestConfig, run_backtest
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


def _minute(t: int | float) -> int:
    return int(t) // 60 * 60


def _bar_of(row: dict[str, Any], step: int, basis: str) -> int:
    """The bar a trade is explained on: its decision bar for a market strategy, the bar holding the fill for stops."""
    if basis == "fill" or row.get("signal_time") is None:
        return entry_bar(int(row["fill_time"]), step, basis="fill")
    return int(row["signal_time"])


_SIDE = {"LONG": "BUY", "SHORT": "SELL"}
_COMPARED = ("contract", "exit_time", "exit_reason")


def _shown(field: str, value: Any) -> str:
    if field == "exit_time" and value is not None:
        return _hhmm(int(value))
    return str(value)


def compare_trades(
    live: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    backtest: list[dict[str, Any]],
    *,
    live_bars: dict[int, dict[str, Any]],
    bt_bars: dict[int, dict[str, Any]],
    incomplete: set[int],
    step: int,
    basis: str = "decision",
) -> list[dict[str, Any]]:
    """Trade-report rows (app/exits/report.py) of paper and of the backtest, matched one to one on the direction and
    the fill minute. Each difference: {time (fill, HH:MM IST), side, source (the side that has the trade, or both),
    fields (status, contract, exit_time, exit_reason), reason, live, backtest}. Empty when they agree."""
    open_live = sorted(live, key=lambda r: int(r["fill_time"]))
    diffs: list[dict[str, Any]] = []
    for b in sorted(backtest, key=lambda r: int(r["fill_time"])):
        side = _SIDE[b["direction"]]
        minute = _minute(b["fill_time"])
        match = next((r for r in open_live if r["direction"] == b["direction"] and _minute(r["fill_time"]) == minute),
                     None)
        if match is not None:
            open_live.remove(match)
            fields = []
            for field in _COMPARED:
                a, c = match.get(field), b.get(field)
                if field == "exit_time":
                    a, c = (None if a is None else _minute(a)), (None if c is None else _minute(c))
                if a != c:
                    fields.append(field)
            if fields:
                reason = "; ".join(
                    f"{f}: paper {_shown(f, match.get(f))}, backtest {_shown(f, b.get(f))}" for f in fields)
                diffs.append({"time": _hhmm(minute), "side": side, "source": "both", "fields": fields,
                              "reason": reason, "live": match, "backtest": b})
            continue
        bar = _bar_of(b, step, basis)
        decided = next((s for s in signals if s.get("side") == side and s.get("status") != "filled" and (
            _minute(int(s.get("decided_at_ms") or 0) // 1000) == minute or s.get("time") == bar)), None)
        if decided is not None:
            note = str(decided.get("note") or "")
            reason = f"paper {decided.get('status')}" + (f": {note}" if note else "")
        else:
            reason = _reason(bar, "backtest", live_bars=live_bars, bt_bars=bt_bars, incomplete=incomplete)
        diffs.append({"time": _hhmm(minute), "side": side, "source": "backtest", "fields": ["status"],
                      "reason": reason, "live": decided, "backtest": b})
    for r in open_live:
        bar = _bar_of(r, step, basis)
        diffs.append({"time": _hhmm(_minute(r["fill_time"])), "side": _SIDE[r["direction"]], "source": "live",
                      "fields": ["status"],
                      "reason": _reason(bar, "live", live_bars=live_bars, bt_bars=bt_bars, incomplete=incomplete),
                      "live": r, "backtest": None})
    return sorted(diffs, key=lambda d: (d["time"], d["side"]))


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


def entry_bar(entry_time: int, step: int, *, basis: str, lag: int = LIVE_LAG_S) -> int:
    """The bar a backtest entry is compared on. `decision`: a market order filled `lag` seconds after its bar ended
    (live timing; 0 on the old timing), so the bar that ended then. `fill`: a stop filled inside a bar, so the bar
    (anchored at 09:15 IST) that holds the fill minute."""
    if basis == "decision":
        return entry_time - lag - step
    anchor = entry_time - (entry_time + 19_800) % 86_400 + 9 * 3600 + 15 * 60
    return entry_time - (entry_time - anchor) % step


def backtest_day(store: CandleStore, symbol: str, day: date, strategy: Strategy,
                 timeframe: str = "5m", basis: str = "decision") -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """The normal backtest on one day: its entries (bar time, side) and its bars. Index only (signal level)."""
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
    lag = LIVE_LAG_S if config.live_timing else 0
    entries = [
        {"time": entry_bar(t.entry_time, step, basis=basis, lag=lag), "side": "BUY" if t.direction == "LONG" else "SELL"}
        for t in result.trades
    ]
    return entries, _day_bars(store, symbol, day, timeframe)


def _day_bars(store: CandleStore, symbol: str, day: date, timeframe: str) -> dict[int, dict[str, Any]]:
    start = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    minutes, _ = store.load(symbol, from_time=start, to_time=start + 86_400, session_types=DEFAULT_INCLUDED_SESSION_TYPES)
    return {int(c["time"]): c for c in resample(minutes, timeframe)}


#: the backtest settings the paper comparison uses (as in PROGRESS: delta-adjusted fills, measured slippage 0.2)
CHECK_SLIPPAGE = 0.2


def backtest_report(
    *, store: CandleStore, symbol: str, day: date, strategy: str, params: dict[str, Any], timeframe: str = "5m",
    tape: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The backtest's trade-report rows for one day, with the option overlay, and its warnings."""
    from app.backtest.catalog import build_strategy, parse_config
    from app.backtest.execute import _engine_config, _load_underlyings, _model, _report_rows
    from app.exits.rules import parse_exit_rule
    from app.options.model import overlay_options

    rest = dict(params)
    exit_rule = rest.pop("exit_rule", None)
    config = parse_config({
        "strategy": strategy, "params": rest, "symbol": symbol, "timeframe": timeframe,
        "start": day.isoformat(), "end": day.isoformat(), "sessions": list(DEFAULT_INCLUDED_SESSION_TYPES),
        "mode": "options", "slippage_points": CHECK_SLIPPAGE, "exit_rule": exit_rule,
    })
    result = run_backtest(build_strategy(config), StoreSource(store, symbol), _engine_config(config))
    nifty, vix = _load_underlyings(config, store=store)
    option = overlay_options(result, nifty, vix, config=_model(config), tape=tape)
    by_id = {row["index_trade_id"]: row for row in option.option.trades}
    rows, _counts = _report_rows(result.trades, by_id, rule=parse_exit_rule(config.get("exit_rule")),
                                 step=60 * int(timeframe[:-1]))
    warnings = []
    unchecked = sum(1 for row in option.option.trades if "premium_exit_unchecked" in row.get("flags", []))
    if unchecked:
        warnings.append(f"{unchecked} backtest trade(s): no stored option bars, so the premium exit was not checked")
    unpriced = len(option.option.unpriced)
    if unpriced:
        warnings.append(f"{unpriced} backtest trade(s) could not be priced as an option")
    return rows, warnings


def end_of_day_check(
    *,
    day: date,
    signals: list[dict[str, Any]],
    live_report: list[dict[str, Any]],
    live_bars: dict[int, dict[str, Any]],
    incomplete: set[int],
    store: CandleStore,
    symbol: str,
    strategy: str,
    params: dict[str, Any],
    timeframe: str = "5m",
    basis: str = "decision",
    tape: Any = None,
) -> dict[str, Any]:
    """Paper's trades (`live_report`, its trade-report rows) and decisions (`signals`) against the backtest's."""
    decisions = [s for s in signals if s.get("side") in ("BUY", "SELL") and s.get("time") is not None]
    try:
        backtest, warnings = backtest_report(store=store, symbol=symbol, day=day, strategy=strategy, params=params,
                                             timeframe=timeframe, tape=tape)
    except Exception as exc:  # noqa: BLE001 - the check is a report; it never breaks the reconcile
        return {"day": day.isoformat(), "error": f"{type(exc).__name__}: {exc}", "live_signals": len(decisions),
                "live_trades": len(live_report), "differences": []}
    diffs = compare_trades(live_report, decisions, backtest, live_bars=live_bars,
                           bt_bars=_day_bars(store, symbol, day, timeframe), incomplete=incomplete,
                           step=60 * int(timeframe[:-1]), basis=basis)
    return {
        "day": day.isoformat(),
        "live_signals": len(decisions),
        "live_trades": len(live_report),
        "backtest_signals": len(backtest),
        "backtest_trades": len(backtest),
        "differences": diffs,
        "warnings": warnings,
        "compared": list(("status",) + _COMPARED + ("fill_minute",)),
    }
