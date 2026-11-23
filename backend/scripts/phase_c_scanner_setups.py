"""Phase C test of the scanner setups. Research only: no paper slot, no auto-take.

    cd backend && uv run python -m scripts.phase_c_scanner_setups
    cd backend && uv run python -m scripts.phase_c_scanner_setups --workers 4 --symbols HDFCBANK

Uses the Phase C gate unchanged (equity_intraday costs, ₹300 and ₹600, ₹75k cap,
live timing, square-off 15:15). DEV 2022-01-03..2024-12-31, OOS 2025-01-01..2026-06-30.
The holdout is not read. Setups are the scanner's vwap_cross, gap_fill, and orb15.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.execute import _candle_symbol, _engine_config
from app.backtest.holdout import refuse_holdout_overlap
from app.backtest.risk_wrap import WithRiskSizing
from app.backtest.sizing import RiskSizing
from app.backtest.sources import StoreSource
from app.config import settings
from app.data.resampler import DAY_S, IST_OFFSET_S, resample
from app.data.store import CandleStore, SymbolNotFound, _q
from app.research.scanner_setups import (
    SETUPS,
    ScannerSetupStrategy,
    collect_history,
    history_counts,
    pooled_counts,
)
from app.scanner.config import ScannerSettings
from app.scanner.judge import judge
from app.scanner.timemachine import minutes_for, ratio_buckets, simulate
from app.scanner.bars import day_of, split_sessions
from scripts.phase_c_research_suite import (
    DEV_END,
    DEV_START,
    NOTIONAL_CAP,
    OOS_END,
    OOS_START,
    _breadth,
    _gate,
    _random_benchmark,
    _summarise_run,
    _top10_removal,
    _universe_aggregate,
    _universe_symbols,
    _walk_forward_portfolio,
)

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[2]
HIST_START = date(2021, 12, 1)
AXES: dict[str, tuple[str, float]] = {
    "orb15": ("volume_mult", 1.5),
    "vwap_cross": ("volume_mult", 1.2),
    "gap_fill": ("gap_min", 0.01),
}
JUDGE_SETTINGS = ScannerSettings()


def _midnight(d: date) -> int:
    return (d.toordinal() - date(1970, 1, 1).toordinal()) * DAY_S - IST_OFFSET_S


def _inside_session_clock() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return 9 * 60 <= minute < 16 * 60 + 10


def _overlaps(store: CandleStore, folder: str, start: date, end: date) -> bool:
    try:
        _minutes, path = store._base_file(folder)
    except SymbolNotFound:
        return False
    lo, hi = _midnight(start), _midnight(end + timedelta(days=1)) - 1
    con = duckdb.connect()
    try:
        row = con.execute(
            f"SELECT 1 FROM read_parquet('{_q(path)}') WHERE time >= {int(lo)} AND time <= {int(hi)} LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    return row is not None


def research_universe(only: set[str] | None = None) -> dict[str, Any]:
    """29 research equities plus Nifty 50 names that have bars in both windows."""
    from app.research.universe import load_universe_spec, resolve_universe as resolve_research
    from app.scanner.universe import resolve_universe as resolve_nifty
    from app.upstox.instruments import current_index

    idx = current_index(settings.instruments_dir)
    if idx is None:
        raise RuntimeError("no instrument snapshot")
    research_res = resolve_research(idx, spec=load_universe_spec())
    research = [s for s in _universe_symbols() if s["mode"] == "equity_intraday"]
    nifty, nifty_missing = resolve_nifty(idx, "nifty50")
    by_symbol = {s["symbol"]: {**s, "lists": ["research_universe_v1"]} for s in research}
    for stock in nifty:
        if stock.symbol in by_symbol:
            by_symbol[stock.symbol]["lists"].append("nifty50")
            continue
        by_symbol[stock.symbol] = {
            "symbol": stock.symbol,
            "folder": stock.folder,
            "mode": "equity_intraday",
            "lot_size": 1,
            "lists": ["nifty50"],
        }
    store = CandleStore(settings.candles_dir)
    kept: list[dict[str, Any]] = []
    one_window: list[dict[str, str]] = []
    no_file: list[str] = []
    for sym in by_symbol.values():
        if only and sym["symbol"] not in only:
            continue
        dev = _overlaps(store, sym["folder"], DEV_START, DEV_END)
        oos = _overlaps(store, sym["folder"], OOS_START, OOS_END)
        if dev and oos:
            kept.append(sym)
        elif dev or oos:
            one_window.append({"symbol": sym["symbol"], "window": "DEV" if dev else "OOS"})
        else:
            no_file.append(sym["symbol"])
    kept.sort(key=lambda s: s["symbol"])
    return {
        "symbols": kept,
        "research_unresolved": list(research_res.unresolved),
        "nifty_unresolved": list(nifty_missing),
        "one_window": one_window,
        "no_history_in_windows": sorted(no_file),
        "research_equities": len(research),
        "nifty_resolved": len(nifty),
    }


def _load_bars(folder: str, start: date, end: date) -> list[dict[str, Any]]:
    refuse_holdout_overlap(start, end)
    store = CandleStore(settings.candles_dir)
    candles, base = store.load(
        folder,
        from_time=_midnight(start),
        to_time=_midnight(end + timedelta(days=1)) - 1,
        session_types=["normal"],
    )
    return list(resample(candles, "5m", base))


def _run_symbol(spec: dict[str, Any]) -> dict[str, Any]:
    label = spec["label"]
    start, end = date.fromisoformat(spec["start"]), date.fromisoformat(spec["end"])
    try:
        refuse_holdout_overlap(start, end)
        body = {
            "symbol": spec["symbol"],
            "mode": "equity_intraday",
            "timeframe": "5m",
            "start": spec["start"],
            "end": spec["end"],
            "sessions": ["normal"],
            "live_timing": True,
            "tick_size": 0.05,
            "slippage_ticks": 1,
            "lot_size": 1,
        }
        cfg: BacktestConfig = _engine_config(body)
        folder = spec.get("folder") or _candle_symbol(body)
        inner = ScannerSetupStrategy(
            spec["setup"],
            volume_mult=spec.get("volume_mult"),
            gap_min=spec.get("gap_min"),
            min_width=spec.get("min_width"),
            max_width=spec.get("max_width"),
        )
        wrapped = WithRiskSizing(
            inner,
            RiskSizing(risk_rupees=float(spec["risk"]), notional_cap=NOTIONAL_CAP, lot_size=1),
        )
        result = run_backtest(wrapped, StoreSource(CandleStore(settings.candles_dir), folder), cfg)
        trades = []
        marks = []
        for t in result.trades:
            row = {
                "entry_time": int(t.entry_time),
                "exit_time": int(t.exit_time),
                "net_pnl": float(t.net_pnl),
                "gross_pnl": float(t.gross_pnl),
                "charges_total": float(t.charges_total),
                "entry_tag": t.entry_tag,
            }
            trades.append(row)
            dist = abs(float(t.entry_price) - float(t.stop_level)) if t.stop_level else 0.0
            rupee = dist * int(t.units) if dist else 0.0
            marks.append(
                {
                    "tag": t.entry_tag,
                    "net": float(t.net_pnl),
                    "gross": float(t.gross_pnl),
                    "charges": float(t.charges_total),
                    "r": (float(t.net_pnl) / rupee) if rupee else None,
                    "win": float(t.net_pnl) > 0,
                }
            )
        out = {"result": {"trades": trades}, "sizing_stats": wrapped.sizing_stats(), "_risk": float(spec["risk"])}
        summary = _summarise_run(out)
        if not spec.get("cliff"):
            summary["marks"] = marks
        return {
            "ok": True,
            "label": label,
            "spec": {
                "strategy_key": spec["setup"],
                "symbol": spec["symbol"],
                "folder": folder,
                "mode": "equity_intraday",
                "risk": float(spec["risk"]),
                "window": spec["window"],
                "start": spec["start"],
                "end": spec["end"],
            },
            "summary": summary,
            "warnings": list(result.warnings),
        }
    except Exception as exc:
        return {
            "ok": False,
            "label": label,
            "spec": {
                "strategy_key": spec.get("setup"),
                "symbol": spec.get("symbol"),
                "risk": spec.get("risk"),
                "window": spec.get("window"),
            },
            "error": f"{type(exc).__name__}: {exc}",
        }


def _jobs(symbols: list[dict[str, Any]], setups: list[str], risks: list[float]) -> list[dict[str, Any]]:
    windows = [("DEV", DEV_START, DEV_END), ("OOS", OOS_START, OOS_END)]
    jobs = []
    for setup in setups:
        for sym in symbols:
            for risk in risks:
                for name, start, end in windows:
                    jobs.append(
                        {
                            "label": f"{setup}|{sym['symbol']}|r{int(risk)}|{name}",
                            "setup": setup,
                            "symbol": sym["symbol"],
                            "folder": sym["folder"],
                            "risk": risk,
                            "window": name,
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        }
                    )
    return jobs


def _run_jobs(jobs: list[dict[str, Any]], workers: int) -> list[dict[str, Any]]:
    if workers <= 1 or len(jobs) <= 1:
        return [_run_symbol(job) for job in jobs]
    out: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_run_symbol, job): job for job in jobs}
        done = 0
        for fut in as_completed(futs):
            out.append(fut.result())
            done += 1
            if done == 1 or done % 10 == 0 or done == len(jobs):
                print(f"backtest {done}/{len(jobs)}", flush=True)
    return out


def _cost_share(rows: list[dict[str, Any]], setup: str, risk: float, window: str) -> dict[str, Any]:
    gross = cost = 0.0
    n = 0
    for row in rows:
        if not row.get("ok"):
            continue
        sp, sm = row["spec"], row["summary"]
        if sp["strategy_key"] != setup or float(sp["risk"]) != risk or sp["window"] != window:
            continue
        trades = int(sm.get("trades") or 0)
        if not trades or sm.get("avg_gross_r") is None:
            continue
        gross += float(sm["avg_gross_r"]) * trades
        cost += float(sm.get("costs_as_r") or 0) * trades
        n += trades
    return {
        "trades": n,
        "avg_gross_r": (gross / n) if n else None,
        "costs_as_r": (cost / n) if n else None,
    }


def _cliff(rows: list[dict[str, Any]], symbols: list[dict[str, Any]], setup: str, risk: float, workers: int) -> dict[str, Any]:
    key, default = AXES[setup]
    sample: list[dict[str, Any]] = []
    for sym in symbols:
        traded = any(
            r.get("ok")
            and r["spec"]["strategy_key"] == setup
            and float(r["spec"]["risk"]) == risk
            and r["spec"]["window"] == "OOS"
            and (r["summary"].get("trades") or 0) > 0
            and r["spec"]["symbol"] == sym["symbol"]
            for r in rows
        )
        if traded:
            sample.append(sym)
        if len(sample) >= 5:
            break
    if not sample:
        sample = symbols[:5]
    jobs = []
    for sym in sample:
        for mult, tag in ((0.8, "m20"), (1.2, "p20")):
            job = {
                "label": f"cliff|{setup}|{sym['symbol']}|{key}|{tag}",
                "setup": setup,
                "symbol": sym["symbol"],
                "folder": sym["folder"],
                "risk": risk,
                "window": "OOS",
                "start": OOS_START.isoformat(),
                "end": OOS_END.isoformat(),
                "cliff": tag,
                key: default * mult,
            }
            jobs.append(job)
    varied = _run_jobs(jobs, workers)
    by_sym: dict[str, dict[str, float]] = defaultdict(dict)
    for r in varied:
        if not r.get("ok"):
            continue
        tag = (r["label"].split("|")[-1]) if r.get("label") else "base"
        by_sym[r["spec"]["symbol"]][tag] = float(r["summary"]["net_pnl"])
    bases = {}
    for r in rows:
        if not r.get("ok"):
            continue
        sp = r["spec"]
        if sp["strategy_key"] == setup and float(sp["risk"]) == risk and sp["window"] == "OOS":
            bases[sp["symbol"]] = float(r["summary"]["net_pnl"])
    cliff = False
    details = []
    for sym, nets in by_sym.items():
        base = bases.get(sym, 0.0)
        for tag, net in nets.items():
            if base == 0:
                continue
            drop = (base - net) / abs(base)
            sign_flip = (base > 0 and net < 0) or (base < 0 and net > 0 and abs(net) > abs(base) * 0.2)
            bad = sign_flip or drop > 0.5
            details.append({"symbol": sym, "tag": tag, "base": base, "net": net, "drop": drop, "cliff": bad})
            if bad:
                cliff = True
    return {"cliff": cliff, "axis": key, "default": default, "details": details[:20], "n_symbols": len(sample)}


def _verdict_symbol(spec: dict[str, Any]) -> dict[str, Any]:
    """Time Machine + this symbol's own history for each taken signal. Pooling happens in the parent."""
    try:
        refuse_holdout_overlap(HIST_START, OOS_END)
        bars = _load_bars(spec["folder"], HIST_START, OOS_END)
        sessions = split_sessions(bars)
        bucket_cache: dict[str, dict] = {}
        out: dict[str, Any] = {"ok": True, "symbol": spec["symbol"], "setups": {}}
        for setup, times in spec["times"].items():
            collected = collect_history(bars, setup)
            plans = {int(row["entry_time"]): row for row in collected["fires"]}
            labeled = []
            for ts in times:
                plan = plans.get(int(ts))
                if plan is None:
                    labeled.append({"time": int(ts), "reason": "no_plan"})
                    continue
                if int(plan["qty"]) < 1:
                    labeled.append({"time": int(ts), "reason": "scanner_qty"})
                    continue
                day = date.fromisoformat(plan["day"])
                key = day.isoformat()
                if key not in bucket_cache:
                    prior = [s for s in sessions if day_of(int(s[0]["time"])) < day]
                    bucket_cache[key] = ratio_buckets(prior[-59:])
                tm = simulate(
                    entry=float(plan["entry"]),
                    stop=float(plan["stop"]),
                    target=float(plan["target"]),
                    side=str(plan["side"]),
                    buckets=bucket_cache[key],
                    minutes=minutes_for(int(plan["entry_time"]), include_fill_bar=False),
                    paths=JUDGE_SETTINGS.tm_paths,
                    seed=JUDGE_SETTINGS.tm_seed,
                    qty=int(plan["qty"]),
                    on=day,
                )
                fires, rate = history_counts(collected, day)
                labeled.append(
                    {
                        "time": int(ts),
                        "target_pct": tm.target_pct,
                        "stop_pct": tm.stop_pct,
                        "own_fires": fires,
                        "own_win_rate": rate,
                        "day": key,
                    }
                )
            out["setups"][setup] = {"history": collected, "signals": labeled}
        return out
    except Exception as exc:
        return {"ok": False, "symbol": spec["symbol"], "error": f"{type(exc).__name__}: {exc}"}


def _signal_times(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[int]]]:
    found: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for row in rows:
        if not row.get("ok") or row["spec"].get("window") not in ("DEV", "OOS"):
            continue
        setup = row["spec"]["strategy_key"]
        symbol = row["spec"]["symbol"]
        for mark in row["summary"].get("marks") or []:
            tag = str(mark.get("tag") or "")
            if ":" not in tag:
                continue
            found[symbol][setup].add(int(tag.split(":", 1)[1]))
    return {sym: {setup: sorted(times) for setup, times in setups.items()} for sym, setups in found.items()}


def _judge_trades(rows: list[dict[str, Any]], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    hist: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    signals: dict[tuple[str, str, int], dict[str, Any]] = {}
    for block in verdicts:
        if not block.get("ok"):
            continue
        symbol = block["symbol"]
        for setup, body in block["setups"].items():
            hist[setup][symbol] = body["history"]
            for sig in body["signals"]:
                signals[(symbol, setup, int(sig["time"]))] = sig
    grouped: dict[tuple[str, float, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("ok"):
            continue
        sp = row["spec"]
        setup, symbol, risk = sp["strategy_key"], sp["symbol"], float(sp["risk"])
        for mark in row["summary"].get("marks") or []:
            tag = str(mark.get("tag") or "")
            if ":" not in tag:
                continue
            ts = int(tag.split(":", 1)[1])
            sig = signals.get((symbol, setup, ts))
            if sig is None or "target_pct" not in sig:
                bucket = "unmarked"
                extra = {"reason": None if sig is None else sig.get("reason")}
            else:
                day = date.fromisoformat(sig["day"])
                fires, rate, source = int(sig["own_fires"]), sig["own_win_rate"], "stock"
                if fires < JUDGE_SETTINGS.tm_min_fires:
                    fires, rate = pooled_counts(hist[setup], day)
                    source = "pooled"
                bucket = judge(
                    target_pct=float(sig["target_pct"]),
                    stop_pct=float(sig["stop_pct"]),
                    win_rate=rate,
                    fires=fires,
                    settings=JUDGE_SETTINGS,
                )
                extra = {
                    "target_pct": sig["target_pct"],
                    "stop_pct": sig["stop_pct"],
                    "history_source": source,
                    "fires": fires,
                }
            grouped[(setup, risk, bucket)].append({**mark, **extra})
    report: dict[str, Any] = {}
    for setup in SETUPS:
        report[setup] = {}
        for risk in (300.0, 600.0):
            cells = {}
            for bucket in ("GOOD", "COIN_FLIP", "AVOID", "unmarked"):
                marks = grouped.get((setup, risk, bucket), [])
                nets = [float(m["net"]) for m in marks]
                rs = [float(m["r"]) for m in marks if m.get("r") is not None]
                cells[bucket] = {
                    "trades": len(marks),
                    "win_rate": (sum(1 for m in marks if m.get("win")) / len(marks)) if marks else None,
                    "avg_net": (sum(nets) / len(nets)) if nets else None,
                    "total_net": sum(nets) if nets else 0.0,
                    "avg_r": (sum(rs) / len(rs)) if rs else None,
                    "avg_target_pct": _mean([m.get("target_pct") for m in marks]),
                    "avg_stop_pct": _mean([m.get("stop_pct") for m in marks]),
                    "pooled_share": (
                        sum(1 for m in marks if m.get("history_source") == "pooled") / len(marks)
                    ) if marks else None,
                }
            good, avoid = cells["GOOD"], cells["AVOID"]
            better = None
            if good["trades"] and avoid["trades"] and good["avg_net"] is not None and avoid["avg_net"] is not None:
                better = good["avg_net"] > avoid["avg_net"]
            report[setup][str(int(risk))] = {"cells": cells, "good_beats_avoid_on_avg_net": better}
    return report


def _mean(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "inf" if value > 0 else "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{100.0 * float(value):.1f}%"


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Phase C — scanner setups ({payload['as_of']})",
        "",
        "Research only. Slot 8 was not changed and auto-take stays off. `LIVE_TRADING` is false.",
        "The holdout was not opened.",
        "",
        "Setups are the scanner code `2026-11-23.2`: stop on the correct side, minimum stop 0.5% of price,",
        "duplicate plans on the same day merged. Costs are `equity_intraday` from the dated table.",
        "Risk ₹300 and ₹600, notional cap ₹75,000 (quantity capped, not skipped), live timing, square-off 15:15,",
        "entries only before 14:30 IST. DEV 2022-01-03..2024-12-31. OOS 2025-01-01..2026-06-30.",
        "",
        "The scanner's positive mark is **GOOD**. It does not use a BUY label. COIN FLIP is not a positive mark.",
        "",
        f"Universe in the gate: **{payload['universe']['n']}** symbols with bars in both windows "
        f"({payload['universe']['research_in_gate']} from research_universe_v1, "
        f"{payload['universe']['nifty_only_in_gate']} Nifty 50 names that are not in that list).",
        f"Research names not resolved (not substituted): {', '.join(payload['universe']['research_unresolved']) or 'none'}.",
        f"Nifty names not resolved: {', '.join(payload['universe']['nifty_unresolved']) or 'none'}.",
        f"History in only one window, left out of the gate: {len(payload['universe']['one_window'])}.",
        f"No bars inside DEV or OOS (files that only cover the holdout are not read): "
        f"{len(payload['universe']['no_history_in_windows'])}.",
        "",
    ]
    if payload["universe"]["no_history_in_windows"]:
        lines.append("No in-window history: " + ", ".join(payload["universe"]["no_history_in_windows"]) + ".")
        lines.append("")
    lines.append(f"Passers: **{payload['passers'] or 'none'}**. Holdout not opened.")
    lines.append("")
    for setup in payload["setups"]:
        lines.append(f"## {setup}")
        lines.append("")
        lines.append("| risk | breadth | WF DEV net | WF OOS net | WF OOS PF | random pct | top10 left | gross R | cost R | OOS trades | OOS PF | cliff | gate |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
        for risk, block in payload["by_risk"][setup].items():
            g = block["gate"]
            lines.append(
                "| {risk} | {breadth} | {wf_dev} | {wf_oos} | {wf_pf} | {rnd} | {top} | {gr} | {cr} | {trades} | {pf} | {cliff} | {gate} |".format(
                    risk=risk,
                    breadth=_pct(block["breadth"]["breadth"]),
                    wf_dev=_fmt(block["walk_forward"].get("DEV", {}).get("net_pnl")),
                    wf_oos=_fmt(block["walk_forward"].get("OOS", {}).get("net_pnl")),
                    wf_pf=_fmt(block["walk_forward"].get("OOS", {}).get("profit_factor"), 3),
                    rnd=_fmt(block["random"].get("percentile"), 1),
                    top=_fmt(block["top10"].get("net_without_top10")),
                    gr=_fmt(block["costs"]["avg_gross_r"], 3),
                    cr=_fmt(block["costs"]["costs_as_r"], 3),
                    trades=block["oos"].get("trades"),
                    pf=_fmt(block["oos"].get("profit_factor"), 3),
                    cliff="yes" if block["cliff"].get("cliff") else "no",
                    gate="PASS" if g.get("passed") else "FAIL",
                )
            )
        lines.append("")
        failed = []
        for risk, block in payload["by_risk"][setup].items():
            checks = block["gate"]["checks"]
            missed = [name for name, ok in checks.items() if not ok]
            failed.append(f"₹{risk}: " + (", ".join(missed) if missed else "all checks passed"))
        lines.append("Checks: " + "; ".join(failed) + ".")
        lines.append("")
        lines.append("Walk-forward picks the 5 symbols with the best trailing 6-month net and trades only those next month.")
        lines.append("Random null is ±gross minus the same charges, 10 seeds. It beats the null at the 95th percentile of net.")
        cliff_block = next(iter(payload["by_risk"][setup].values()))["cliff"]
        lines.append(
            "±20% moves one knob on up to 5 OOS symbols: "
            + f"{cliff_block.get('axis', '')} (default {cliff_block.get('default', '')})."
        )
        lines.append("")
        lines.append("### Verdict accuracy")
        lines.append("")
        lines.append("A taken trade is marked the way the scanner would have marked that signal: 10,000 paths, seed 20261123,")
        lines.append("edge 10 points, win rate 0.40, at least 8 fires, stock history unless the stock has fewer than 8 fires.")
        lines.append("Quantity for that mark uses the scanner's ₹500 / ₹50,000 plan. A plan that book skips is unmarked.")
        lines.append("")
        lines.append("| risk | mark | trades | win rate | avg net | avg R | total net |")
        lines.append("|---:|---|---:|---:|---:|---:|---:|")
        for risk, block in payload["verdicts"].get(setup, {}).items():
            for bucket in ("GOOD", "COIN_FLIP", "AVOID", "unmarked"):
                cell = block["cells"][bucket]
                lines.append(
                    f"| {risk} | {bucket} | {cell['trades']} | {_pct(cell['win_rate'])} | {_fmt(cell['avg_net'])} | "
                    f"{_fmt(cell['avg_r'], 3)} | {_fmt(cell['total_net'])} |"
                )
            flag = block["good_beats_avoid_on_avg_net"]
            answer = "yes" if flag else "no" if flag is False else "not enough of both marks"
            lines.append(f"| {risk} | GOOD avg net better than AVOID | {answer} | | | | |")
        lines.append("")
    if payload["errors"]:
        lines.append("## Errors")
        lines.append("")
        for err in payload["errors"][:15]:
            lines.append(f"- {err.get('label')}: {err.get('error')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _slim(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if not row.get("ok"):
            continue
        sm = dict(row["summary"])
        for key in ("trade_nets", "trade_times", "trade_gross_charges", "marks"):
            sm.pop(key, None)
        out.append({"spec": row["spec"], "summary": sm})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--setups", nargs="*", default=list(SETUPS))
    parser.add_argument("--risk", type=float, nargs="*", default=[300.0, 600.0])
    parser.add_argument("--skip-verdict", action="store_true")
    args = parser.parse_args(argv)
    if settings.live_trading:
        print("LIVE_TRADING is on; refusing.", flush=True)
        return 2
    if _inside_session_clock():
        print("inside 09:00–16:10 IST; not running.", flush=True)
        return 2
    for setup in args.setups:
        if setup not in SETUPS:
            raise SystemExit(f"unknown setup {setup}")
    universe = research_universe(set(args.symbols) if args.symbols else None)
    symbols = universe["symbols"]
    print(
        f"universe {len(symbols)} symbols with both windows; "
        f"one-window {len(universe['one_window'])}; no-window {len(universe['no_history_in_windows'])}",
        flush=True,
    )
    if not symbols:
        print("no symbols", flush=True)
        return 1
    jobs = _jobs(symbols, list(args.setups), list(args.risk))
    print(f"running {len(jobs)} backtests with {args.workers} workers", flush=True)
    rows = _run_jobs(jobs, args.workers)
    errors = [r for r in rows if not r.get("ok")]
    print(f"done {len(rows) - len(errors)} ok, {len(errors)} errors", flush=True)

    breadth = {str(int(r)): _breadth(rows, risk=r) for r in args.risk}
    by_risk: dict[str, dict[str, Any]] = {setup: {} for setup in args.setups}
    gates: dict[str, dict[str, Any]] = {}
    for risk in args.risk:
        rk = str(int(risk))
        for setup in args.setups:
            print(f"post {setup} risk={rk}", flush=True)
            wf = _walk_forward_portfolio(rows, strategy_key=setup, risk=risk)
            rnd = _random_benchmark(rows, strategy_key=setup, risk=risk, window="OOS")
            oos = _universe_aggregate(rows, strategy_key=setup, risk=risk, window="OOS")
            top10 = _top10_removal(oos)
            cliff = _cliff(rows, symbols, setup, risk, args.workers)
            gate = _gate(
                breadth=breadth[rk].get(setup, {"breadth": 0}),
                wf=wf,
                random_oos=rnd,
                oos_summary_universe=oos,
                top10=top10,
                cliff=cliff,
            )
            by_risk[setup][rk] = {
                "breadth": breadth[rk].get(setup, {}),
                "walk_forward": wf,
                "random": rnd,
                "oos": {k: v for k, v in oos.items() if k != "trade_nets"},
                "top10": top10,
                "cliff": cliff,
                "costs": _cost_share(rows, setup, risk, "OOS"),
                "costs_dev": _cost_share(rows, setup, risk, "DEV"),
                "gate": gate,
            }
            gates.setdefault(rk, {})[setup] = gate

    verdicts: dict[str, Any] = {}
    verdict_errors: list[dict[str, Any]] = []
    if not args.skip_verdict:
        times = _signal_times(rows)
        vjobs = [
            {"symbol": sym["symbol"], "folder": sym["folder"], "times": times.get(sym["symbol"], {})}
            for sym in symbols
            if times.get(sym["symbol"])
        ]
        print(f"verdicts for {len(vjobs)} symbols", flush=True)
        if args.workers <= 1:
            vrows = [_verdict_symbol(job) for job in vjobs]
        else:
            vrows = []
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futs = [pool.submit(_verdict_symbol, job) for job in vjobs]
                done = 0
                for fut in as_completed(futs):
                    vrows.append(fut.result())
                    done += 1
                    if done == 1 or done % 5 == 0 or done == len(vjobs):
                        print(f"verdict {done}/{len(vjobs)}", flush=True)
        verdict_errors = [r for r in vrows if not r.get("ok")]
        verdicts = _judge_trades(rows, vrows)

    passers = [
        f"{setup} ₹{risk}"
        for risk, block in gates.items()
        for setup, gate in block.items()
        if gate.get("passed")
    ]
    payload = {
        "as_of": date.today().isoformat(),
        "code_version": "2026-11-23.2",
        "holdout_opened": False,
        "passers": passers,
        "setups": list(args.setups),
        "universe": {
            "n": len(symbols),
            "research_in_gate": sum(1 for s in symbols if "research_universe_v1" in s["lists"]),
            "nifty_only_in_gate": sum(1 for s in symbols if s["lists"] == ["nifty50"]),
            "research_unresolved": universe["research_unresolved"],
            "nifty_unresolved": universe["nifty_unresolved"],
            "one_window": universe["one_window"],
            "no_history_in_windows": universe["no_history_in_windows"],
            "symbols": [s["symbol"] for s in symbols],
        },
        "by_risk": by_risk,
        "verdicts": verdicts,
        "errors": errors[:20],
        "verdict_errors": verdict_errors[:10],
        "n_jobs": len(jobs),
        "per_symbol": _slim(rows),
    }
    out_dir = ROOT / "data" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"phase_c_scanner_setups_{payload['as_of']}"
    text = _markdown(payload)
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    stem.with_suffix(".json").write_text(json.dumps(_jsonable(payload), indent=2), encoding="utf-8")
    print(text, flush=True)
    print(f"wrote {stem}.md", flush=True)
    return 0


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


if __name__ == "__main__":
    raise SystemExit(main())
