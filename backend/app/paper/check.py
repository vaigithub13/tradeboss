"""End-of-day check: the paper trades against the normal backtest on the same day.

The backtest is the real engine (`run_backtest`) on the stored candles, with the same strategy, parameters and exit
rule, on live timing (a market entry fills one minute after its decision bar ends, as in paper), then the option
overlay (the contract at the fill, delta-adjusted fills, slippage 0.2, the premium walk on the stored option bars).

Trades are matched one to one on the direction and the fill minute, and compared on the contract, the exit minute and
the exit reason (M1, review 2026-10-11). A backtest trade with no paper trade is a status difference: the paper
signal's own status and note when paper decided it but did not fill (unfilled, after the square-off), otherwise the
bar reason (a live gap, a different bar, or the same bar with a different state).

A stop or target set from each side's own entry fill is two levels, paper's and the backtest's. When only the exit
minute differs and the bar at the earlier exit crossed only the earlier side's level, the difference is a
"fill-driven level difference" (12 Oct, slot 3): kept apart from the real differences, with both levels and that bar.

Paper's premium levels trigger on the traded prices the feed sends; the official 1m bar also holds the trades between
them. When only the exit minute differs, the backtest exited in a minute whose bar crossed paper's level and paper
recorded a shadow exit there (no tick of that minute reached the level; app/paper/shadow.py), the difference is an
"exchange-only crossing" (4 Nov, slot 4): kept apart too, with paper's shadow and that bar.
"""

from __future__ import annotations

import re
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
FILL_DRIVEN = "fill-driven level difference"
EXCHANGE_ONLY = "exchange-only crossing"
_LEVELS = frozenset({"premium_target", "premium_stop", "index_target", "index_stop"})

#: (contract name, minute) -> the contract's 1m (open, high, low, close), or None
OptionBar = Callable[[str, int], "tuple[float, float, float, float] | None"]
#: minute -> the index's 1m (open, high, low, close), or None
IndexBar = Callable[[int], "tuple[float, float, float, float] | None"]


def _above(name: str, direction: str) -> bool:
    """Is the level above the entry? A premium level is on a bought option; an index level on the position."""
    return name.endswith("target") if (name.startswith("premium") or direction == "LONG") else name.endswith("stop")


def _crossed(level: float, ohlc: tuple[float, ...], *, above: bool) -> bool:
    """Did this 1m (open, high, low, close) reach the level?"""
    return ohlc[1] >= level if above else ohlc[2] <= level


def _fill_driven(live: dict[str, Any], bt: dict[str, Any], *, option_bar: OptionBar | None,
                 index_bar: IndexBar | None) -> dict[str, Any] | None:
    """The level and crossing bar when the exit minutes differ only because the two fills set two levels."""
    name = str(live.get("exit_reason_raw"))
    if name not in _LEVELS or bt.get("exit_reason_raw") != name:
        return None
    paper_level, bt_level = live.get(name), bt.get(name)
    if paper_level is None or bt_level is None or abs(float(paper_level) - float(bt_level)) <= PRICE_TOLERANCE:
        return None
    paper_minute, bt_minute = _minute(live["exit_time"]), _minute(bt["exit_time"])
    first = "paper" if paper_minute < bt_minute else "backtest"
    minute = min(paper_minute, bt_minute)
    if name.startswith("premium"):
        of, ohlc = str(live["contract"]), None if option_bar is None else option_bar(str(live["contract"]), minute)
    else:
        of, ohlc = "index", None if index_bar is None else index_bar(minute)
    if ohlc is None:
        return None
    first_level, other_level = (paper_level, bt_level) if first == "paper" else (bt_level, paper_level)
    above = _above(name, str(live["direction"]))
    if not _crossed(float(first_level), ohlc, above=above) or _crossed(float(other_level), ohlc, above=above):
        return None
    o, h, lo, c = (float(x) for x in ohlc)
    extreme = ("high", h) if above else ("low", lo)
    return {
        "level": {"name": name, "paper": float(paper_level), "backtest": float(bt_level)},
        "crossing_bar": {"time": _hhmm(minute), "of": of, "open": o, "high": h, "low": lo, "close": c,
                         "crossed": first},
        "reason": (f"{FILL_DRIVEN}: {name} paper {float(paper_level):g}, backtest {float(bt_level):g}; "
                   f"{of} {_hhmm(minute)} {extreme[0]} {extreme[1]:g} reached only the {first}'s level "
                   f"(exit paper {_hhmm(paper_minute)}, backtest {_hhmm(bt_minute)})"),
    }


def _exchange_only(live: dict[str, Any], bt: dict[str, Any], *,
                   option_bar: OptionBar | None) -> dict[str, Any] | None:
    """Paper's shadow and the bar, when the backtest's exit is the resting order paper's ticks missed."""
    sh = live.get("shadow")
    if not sh or bt.get("exit_time") is None or bt.get("exit_reason") != sh.get("exit_reason"):
        return None
    minute = _minute(bt["exit_time"])
    if _minute(sh["exit_time"]) != minute:
        return None
    name = f"premium_{sh['exit_reason']}"
    contract = str(live["contract"])
    ohlc = None if option_bar is None else option_bar(contract, minute)
    bar = None
    if ohlc is not None:
        o, h, lo, c = (float(x) for x in ohlc)
        bar = {"time": _hhmm(minute), "of": contract, "open": o, "high": h, "low": lo, "close": c}
    extreme = ""
    if bar is not None:
        side = "low" if name.endswith("stop") else "high"
        extreme = f" {side} {bar[side]:g}"
    return {
        "shadow": sh,
        "crossing_bar": bar,
        "reason": (f"{EXCHANGE_ONLY}: {contract} {_hhmm(minute)}{extreme} crossed paper's {name} "
                   f"{float(live.get(name) or 0):g}, no feed tick reached it (exit paper {_hhmm(_minute(live['exit_time']))}, "
                   f"backtest {_hhmm(minute)}); a resting order: {float(sh['exit_premium']):g}, net {float(sh['net']):g}"),
    }


def split_kinds(diffs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The check's three lists: real differences, fill-driven level differences, exchange-only crossings."""
    return {"differences": [d for d in diffs if d["kind"] not in (FILL_DRIVEN, EXCHANGE_ONLY)],
            "fill_driven": [d for d in diffs if d["kind"] == FILL_DRIVEN],
            "exchange_only": [d for d in diffs if d["kind"] == EXCHANGE_ONLY]}


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
    option_bar: OptionBar | None = None,
    index_bar: IndexBar | None = None,
) -> list[dict[str, Any]]:
    """Trade-report rows (app/exits/report.py) of paper and of the backtest, matched one to one on the direction and
    the fill minute. Each difference: {time (fill, HH:MM IST), side, source (the side that has the trade, or both),
    kind ("difference", FILL_DRIVEN with level and crossing_bar, or EXCHANGE_ONLY with paper's shadow and
    crossing_bar), fields (status, contract, exit_time,
    exit_reason), reason, live, backtest}. Empty when they agree. `option_bar` and `index_bar` give the 1m bars a
    fill-driven level difference is read on; without them every difference is a real one."""
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
                only_exit = fields == ["exit_time"]
                driven = _fill_driven(match, b, option_bar=option_bar, index_bar=index_bar) if only_exit else None
                missed = _exchange_only(match, b, option_bar=option_bar) if only_exit and driven is None else None
                if driven is not None:
                    diffs.append({"time": _hhmm(minute), "side": side, "source": "both", "kind": FILL_DRIVEN,
                                  "fields": fields, **driven, "live": match, "backtest": b})
                elif missed is not None:
                    diffs.append({"time": _hhmm(minute), "side": side, "source": "both", "kind": EXCHANGE_ONLY,
                                  "fields": fields, **missed, "live": match, "backtest": b})
                else:
                    diffs.append({"time": _hhmm(minute), "side": side, "source": "both", "kind": "difference",
                                  "fields": fields, "reason": reason, "live": match, "backtest": b})
            continue
        bar = _bar_of(b, step, basis)
        decided = next((s for s in signals if s.get("side") == side and s.get("status") != "filled" and (
            _minute(int(s.get("decided_at_ms") or 0) // 1000) == minute or s.get("time") == bar)), None)
        if decided is not None:
            note = str(decided.get("note") or "")
            reason = f"paper {decided.get('status')}" + (f": {note}" if note else "")
        else:
            reason = _reason(bar, "backtest", live_bars=live_bars, bt_bars=bt_bars, incomplete=incomplete)
        diffs.append({"time": _hhmm(minute), "side": side, "source": "backtest", "kind": "difference",
                      "fields": ["status"],
                      "reason": reason, "live": decided, "backtest": b})
    for r in open_live:
        bar = _bar_of(r, step, basis)
        diffs.append({"time": _hhmm(_minute(r["fill_time"])), "side": _SIDE[r["direction"]], "source": "live",
                      "kind": "difference", "fields": ["status"],
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


_CONTRACT = re.compile(r"^\S+ (\d+(?:\.\d+)?) (CE|PE) (\d{2}) ([A-Z]{3}) (\d{2})$")


def _option_bars(tape: Any) -> OptionBar:
    """1m bars of a contract by its name (`NIFTY 22500 PE 13 OCT 26`), from the tape the backtest priced with."""
    from app.options.contract import MONTHS
    from app.options.history import default_history_store

    source = tape if tape is not None else default_history_store()
    bar_at = getattr(source, "bar_at", None)

    def get(contract: str, minute: int) -> tuple[float, float, float, float] | None:
        m = _CONTRACT.match(contract)
        if bar_at is None or m is None or m.group(4) not in MONTHS:
            return None
        expiry = date(2000 + int(m.group(5)), MONTHS.index(m.group(4)) + 1, int(m.group(3)))
        return bar_at(expiry, float(m.group(1)), m.group(2), minute)

    return get


def _index_bars(store: CandleStore, symbol: str, day: date) -> IndexBar:
    """The index's 1m bars of the day, loaded when first asked for."""
    bars: dict[int, tuple[float, float, float, float]] | None = None

    def get(minute: int) -> tuple[float, float, float, float] | None:
        nonlocal bars
        if bars is None:
            start = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
            rows, _ = store.load(symbol, from_time=start, to_time=start + 86_400,
                                 session_types=DEFAULT_INCLUDED_SESSION_TYPES)
            bars = {int(r["time"]): (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                    for r in rows}
        return bars.get(minute)

    return get


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
    """Paper's trades (`live_report`, its trade-report rows) and decisions (`signals`) against the backtest's. A
    fill-driven level difference goes to `fill_driven`, an exchange-only crossing to `exchange_only`, not
    `differences`."""
    decisions = [s for s in signals if s.get("side") in ("BUY", "SELL") and s.get("time") is not None]
    try:
        backtest, warnings = backtest_report(store=store, symbol=symbol, day=day, strategy=strategy, params=params,
                                             timeframe=timeframe, tape=tape)
    except Exception as exc:  # noqa: BLE001 - the check is a report; it never breaks the reconcile
        return {"day": day.isoformat(), "error": f"{type(exc).__name__}: {exc}", "live_signals": len(decisions),
                "live_trades": len(live_report), "differences": [], "fill_driven": []}
    diffs = compare_trades(live_report, decisions, backtest, live_bars=live_bars,
                           bt_bars=_day_bars(store, symbol, day, timeframe), incomplete=incomplete,
                           step=60 * int(timeframe[:-1]), basis=basis, option_bar=_option_bars(tape),
                           index_bar=_index_bars(store, symbol, day))
    return {
        "day": day.isoformat(),
        "live_signals": len(decisions),
        "live_trades": len(live_report),
        "backtest_signals": len(backtest),
        "backtest_trades": len(backtest),
        **split_kinds(diffs),
        "warnings": warnings,
        "compared": list(("status",) + _COMPARED + ("fill_minute",)),
    }
