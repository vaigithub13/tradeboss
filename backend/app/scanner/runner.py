"""Scan one moment, or walk a whole session. Shared by the live service, replay, and the tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from app.scanner.alerts import cutoff_message, flat_message, signal_message, trade_message
from app.scanner.bars import (
    ENTRY_CUTOFF_MIN,
    IST,
    LAST_BAR_MIN,
    Bar,
    day_of,
    minute_of,
    session_for,
    split_sessions,
)
from app.scanner.book import flatten, on_bar, take_signals
from app.scanner.config import ScannerSettings
from app.scanner.context import context_row, nifty_change_pct
from app.scanner.history import occurrences
from app.scanner.judge import judge
from app.scanner.plan import build_plan
from app.scanner.risk import assess
from app.scanner.setups import run_setups
from app.scanner.timemachine import minutes_for, ratio_buckets, run_time_machine

TmFn = Callable[[dict], dict]


def fresh_state(day: date) -> dict[str, Any]:
    return {
        "day": day.isoformat(),
        "source": "live",
        "open": [],
        "trades": [],
        "net": 0.0,
        "signals": [],
        "alerts": [],
        "flattened": False,
        "reported": False,
        "cutoff_sent": False,
        "last_scan": None,
        "applied": {},
    }


def proven_pairs(rankings: dict | None) -> set[tuple[str, str]] | None:
    """None when no ranking exists yet, so the proven-only filter stays off."""
    if not rankings or not rankings.get("pairs"):
        return None
    good: set[tuple[str, str]] = set()
    for row in rankings["pairs"]:
        trades = int(row.get("trades") or 0)
        pf = row.get("profit_factor")
        wins = int(row.get("wins") or 0)
        if trades >= 8 and ((pf is None and wins > 0) or (pf is not None and float(pf) > 1)):
            good.add((str(row["symbol"]), str(row["setup"])))
    return good


def _prev_close(bars: list[Bar], day: date) -> float | None:
    prior = [b for b in bars if day_of(int(b["time"])) < day]
    return float(prior[-1]["close"]) if prior else None


def _next_bar(bars: list[Bar], ts: int) -> Bar | None:
    for bar in bars:
        if int(bar["time"]) > ts:
            return bar
    return None


def _buckets_for(bars: list[Bar], day: date) -> dict[int, list[tuple[float, float, float, float]]]:
    prior = [session for session in split_sessions(bars) if day_of(int(session[0]["time"])) < day]
    return ratio_buckets(prior[-59:])


def evaluate_symbol(
    *,
    symbol: str,
    sector: str,
    bars: list[Bar],
    day: date,
    as_of: int | None,
    settings: ScannerSettings,
    nifty_pct: float | None,
    proven: set[tuple[str, str]] | None,
    open_symbols: set[str],
    daily_loss: float,
    trades_today: int,
    folder: str | None = None,
    tm: TmFn = run_time_machine,
) -> list[dict[str, Any]]:
    prev = _prev_close(bars, day)
    hits = run_setups(bars, enabled=settings.setups, prev_close=prev, as_of=as_of)
    today_hits = [h for h in hits if day_of(h.time) == day]
    if settings.only_proven and proven is not None:
        today_hits = [h for h in today_hits if h.forming or (symbol, h.setup) in proven]
    sessions = split_sessions(bars)
    prior_sessions = [s for s in sessions if day_of(int(s[0]["time"])) < day]
    today = session_for(bars, day)
    if as_of is not None:
        today = [b for b in today if int(b["time"]) + 300 <= as_of]
    ctx = context_row(
        symbol=symbol,
        sector=sector,
        today=today,
        prior_sessions=prior_sessions,
        prev_close=prev,
        nifty_pct=nifty_pct,
    )
    buckets = _buckets_for(bars, day)
    hist_settings = replace(settings, target_mode="2r")
    rows: list[dict[str, Any]] = []
    for hit in today_hits:
        next_bar = None
        if settings.entry_mode == "next_open":
            nxt = _next_bar(bars, hit.time)
            if nxt is not None and (as_of is None or int(nxt["time"]) <= as_of):
                next_bar = nxt
        plan = build_plan(hit, settings, symbol=symbol, next_bar=next_bar)
        if hit.forming and as_of is not None and minute_of(as_of) >= ENTRY_CUTOFF_MIN:
            plan.status = "expired"
        row_id = f"orb15:{symbol}:{day.isoformat()}:waiting" if hit.forming else f"{hit.setup}:{symbol}:{hit.time}:{hit.side}"
        row: dict[str, Any] = {
            "id": row_id,
            **plan.to_dict(),
            **{k: ctx[k] for k in ("sector", "change_pct", "vwap_dist_pct", "rvol", "vs_nifty")},
            "range_high": hit.range_high,
            "range_low": hit.range_low,
            "trigger_time": hit.time,
            "forming": hit.forming,
            "folder": folder or symbol,
            "proven": proven is not None and (symbol, hit.setup) in proven,
            "target_pct": None,
            "stop_pct": None,
            "open_pct": None,
            "avg_rupees": None,
            "fires": 0,
            "wins": 0,
            "losses": 0,
            "neither": 0,
            "win_rate": None,
            "avg_r": None,
            "occurrences": [],
            "verdict": None,
        }
        if (
            not hit.forming
            and plan.entry is not None
            and plan.stop is not None
            and plan.target is not None
            and plan.side in ("long", "short")
            and plan.qty >= 1
        ):
            include_fill = settings.entry_mode == "next_open"
            spec = {
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "side": plan.side,
                "buckets": {str(k): [list(item) for item in v] for k, v in buckets.items()},
                "minutes": minutes_for(plan.entry_time, include_fill_bar=include_fill),
                "paths": settings.tm_paths,
                "seed": settings.tm_seed,
                "qty": plan.qty,
                "on": day.isoformat(),
            }
            tm_row = tm(spec)
            hist = occurrences(bars, setup=hit.setup, up_to=day, settings=hist_settings)
            verdict = judge(
                target_pct=float(tm_row["target_pct"]),
                stop_pct=float(tm_row["stop_pct"]),
                win_rate=hist.win_rate,
                fires=hist.fires,
                settings=settings,
            )
            row.update(
                {
                    "target_pct": tm_row["target_pct"],
                    "stop_pct": tm_row["stop_pct"],
                    "open_pct": tm_row["open_pct"],
                    "avg_rupees": tm_row["avg_rupees"],
                    "paths": tm_row["paths"],
                    "seed": tm_row["seed"],
                    "histogram": tm_row["histogram"],
                    "fires": hist.fires,
                    "wins": hist.wins,
                    "losses": hist.losses,
                    "neither": hist.neither,
                    "win_rate": hist.win_rate,
                    "avg_r": hist.avg_r,
                    "occurrences": hist.occurrences,
                    "verdict": verdict,
                }
            )
        elif hit.forming:
            row["verdict"] = None
        else:
            row["verdict"] = "AVOID"
        decision = assess(
            plan,
            verdict=row["verdict"],
            settings=settings,
            daily_loss=daily_loss,
            trades_today=trades_today,
            open_symbols=open_symbols,
        )
        row["decision"] = decision["decision"]
        row["reasons"] = decision["reasons"]
        row["news"] = decision["news"]
        rows.append(row)
    return rows


def scan_feed(
    symbols: dict[str, list[Bar]],
    sectors: dict[str, str],
    nifty: list[Bar],
    *,
    day: date,
    as_of: int | None,
    settings: ScannerSettings,
    state: dict[str, Any],
    proven: set[tuple[str, str]] | None,
    folders: dict[str, str] | None = None,
    tm: TmFn = run_time_machine,
) -> list[dict[str, Any]]:
    nifty_prev = _prev_close(nifty, day) if nifty else None
    nifty_pct = nifty_change_pct(nifty, day, nifty_prev) if nifty else None
    open_symbols = {p["symbol"] for p in state["open"]}
    trades_today = len(state["trades"]) + len(state["open"])
    found: list[dict[str, Any]] = []
    for symbol, bars in symbols.items():
        found.extend(
            evaluate_symbol(
                symbol=symbol,
                sector=sectors.get(symbol, ""),
                bars=bars,
                day=day,
                as_of=as_of,
                settings=settings,
                nifty_pct=nifty_pct,
                proven=proven,
                open_symbols=open_symbols,
                daily_loss=float(state["net"]),
                trades_today=trades_today,
                folder=(folders or {}).get(symbol),
                tm=tm,
            )
        )
    return found


def merge_signals(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    by_id = {row["id"]: row for row in state["signals"]}
    for row in rows:
        prev = by_id.get(row["id"])
        if prev is not None and prev.get("status") in ("in_trade", "target", "stop", "time_exit"):
            # keep the trade status; refresh context numbers
            for key in ("change_pct", "vwap_dist_pct", "rvol", "vs_nifty"):
                prev[key] = row.get(key)
            continue
        by_id[row["id"]] = row
    state["signals"] = list(by_id.values())


def apply_until(state: dict[str, Any], symbols: dict[str, list[Bar]], day: date, as_of: int, settings: ScannerSettings) -> list[dict]:
    closed: list[dict] = []
    applied: dict[str, int] = state["applied"]
    for symbol, bars in symbols.items():
        for bar in bars:
            if day_of(int(bar["time"])) != day:
                continue
            if int(bar["time"]) + 300 > as_of:
                continue
            if int(bar["time"]) <= applied.get(symbol, 0):
                continue
            closed.extend(on_bar(state, symbol, bar, settings))
            applied[symbol] = int(bar["time"])
    _mark_closed(state, closed)
    return closed


def _mark_closed(state: dict[str, Any], trades: list[dict]) -> None:
    if not trades:
        return
    by_symbol = {t["symbol"]: t for t in trades}
    for row in state["signals"]:
        trade = by_symbol.get(row["symbol"])
        if trade is None or row.get("status") != "in_trade":
            continue
        if int(row.get("entry_time") or 0) != int(trade["entry_time"]):
            continue
        row["status"] = {"target": "target", "stop": "stop", "time": "time_exit"}[trade["exit_reason"]]
        row["exit"] = trade["exit"]
        row["net"] = trade["net"]
        row["r"] = trade["r"]


def note_alerts(state: dict[str, Any], rows: list[dict[str, Any]], trades: list[dict]) -> None:
    seen = {a["id"] for a in state["alerts"]}
    for row in rows:
        if row.get("verdict") == "GOOD" and row.get("status") == "triggered":
            alert_id = f"good:{row['id']}"
            if alert_id not in seen:
                state["alerts"].append({"id": alert_id, "kind": "good_signal", "message": signal_message(row)})
                seen.add(alert_id)
    for trade in trades:
        kind = "exit"
        alert_id = f"exit:{trade['symbol']}:{trade['exit_time']}"
        if alert_id not in seen:
            state["alerts"].append({"id": alert_id, "kind": kind, "message": trade_message(trade, kind="exit")})
            seen.add(alert_id)


def note_entries(state: dict[str, Any], taken: list[dict]) -> None:
    seen = {a["id"] for a in state["alerts"]}
    for row in taken:
        alert_id = f"entry:{row['id']}"
        if alert_id in seen:
            continue
        state["alerts"].append({"id": alert_id, "kind": "entry", "message": trade_message(row, kind="entry")})


def step(
    symbols: dict[str, list[Bar]],
    sectors: dict[str, str],
    nifty: list[Bar],
    *,
    day: date,
    as_of: int,
    settings: ScannerSettings,
    state: dict[str, Any],
    proven: set[tuple[str, str]] | None,
    tm: TmFn = run_time_machine,
) -> None:
    apply_until(state, symbols, day, as_of, settings)
    rows = scan_feed(
        symbols, sectors, nifty, day=day, as_of=as_of, settings=settings, state=state, proven=proven, tm=tm
    )
    merge_signals(state, rows)
    note_alerts(state, rows, [])
    taken = take_signals(state["signals"], settings, state)
    note_entries(state, taken)
    state["last_scan"] = datetime.fromtimestamp(as_of, IST).isoformat()


def walk_day(
    symbols: dict[str, list[Bar]],
    sectors: dict[str, str],
    nifty: list[Bar],
    *,
    day: date,
    settings: ScannerSettings,
    proven: set[tuple[str, str]] | None = None,
    tm: TmFn = run_time_machine,
    source: str = "replay",
) -> dict[str, Any]:
    """Walk 09:30–14:30 every five minutes, then flat at 15:15. `source` marks a replay so it is not live P&L."""
    state = fresh_state(day)
    state["source"] = source
    cursor = datetime.combine(day, time(9, 30), tzinfo=IST)
    end = datetime.combine(day, time(14, 30), tzinfo=IST)
    while cursor <= end:
        step(
            symbols,
            sectors,
            nifty,
            day=day,
            as_of=int(cursor.timestamp()),
            settings=settings,
            state=state,
            proven=proven,
            tm=tm,
        )
        cursor += timedelta(minutes=5)
    flat_ts = int(datetime.combine(day, time(15, 15), tzinfo=IST).timestamp())
    apply_until(state, symbols, day, flat_ts, settings)
    last_prices: dict[str, tuple[float, int]] = {}
    for symbol, bars in symbols.items():
        today = [b for b in session_for(bars, day) if minute_of(int(b["time"])) <= LAST_BAR_MIN]
        if today:
            last_prices[symbol] = (float(today[-1]["close"]), int(today[-1]["time"]))
    closed = flatten(state, last_prices, settings)
    _mark_closed(state, closed)
    note_alerts(state, [], closed)
    if not state["cutoff_sent"]:
        state["alerts"].append({"id": f"cutoff:{day.isoformat()}", "kind": "cutoff", "message": cutoff_message()})
        state["cutoff_sent"] = True
    state["alerts"].append({"id": f"flat:{day.isoformat()}", "kind": "flat", "message": flat_message(float(state["net"]))})
    state["reported"] = True
    state["report"] = build_report(state)
    return state


def build_report(state: dict[str, Any]) -> dict[str, Any]:
    signals = state["signals"]
    outcomes: dict[str, list[str]] = {"GOOD": [], "COIN_FLIP": [], "AVOID": []}
    for row in signals:
        verdict = row.get("verdict")
        status = row.get("status")
        if verdict in outcomes and status in ("target", "stop", "time_exit"):
            outcomes[verdict].append(status)
    return {
        "day": state["day"],
        "source": state.get("source", "live"),
        "net": state["net"],
        "trades": state["trades"],
        "signals": [
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row["side"],
                "status": row["status"],
                "decision": row.get("decision"),
                "reasons": row.get("reasons"),
                "verdict": row.get("verdict"),
            }
            for row in signals
        ],
        "blocked": [
            {"id": row["id"], "symbol": row["symbol"], "setup": row["setup"], "reasons": row.get("reasons")}
            for row in signals
            if row.get("decision") == "BLOCK"
        ],
        "verdict_outcomes": {name: _tally(statuses) for name, statuses in outcomes.items()},
    }


def _tally(statuses: list[str]) -> dict[str, int]:
    return {
        "n": len(statuses),
        "target": statuses.count("target"),
        "stop": statuses.count("stop"),
        "time_exit": statuses.count("time_exit"),
    }
