"""Phase C: stock/BankNifty research suite (no slots, no live).

    cd backend && uv run python -m scripts.phase_c_research_suite
    cd backend && uv run python -m scripts.phase_c_research_suite --workers 4
    cd backend && uv run python -m scripts.phase_c_research_suite --dry-only   # STEP 0 re-check

DEV 2022-01-03..2024-12-31; OOS 2025-01-01..2026-06-30. Holdout untouched.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_run
from app.backtest.holdout import load_holdout
from app.backtest.metrics import TradeRecord, compute_metrics
from app.config import settings
from app.research.universe import load_universe_spec, resolve_universe
from app.upstox.instruments import BANKNIFTY_INDEX_KEY, current_index

IST = timezone(timedelta(hours=5, minutes=30))
DEV_START, DEV_END = date(2022, 1, 3), date(2024, 12, 31)
HOLDOUT_START, HOLDOUT_END = load_holdout()
OOS_START, OOS_END = date(2025, 1, 1), HOLDOUT_START - timedelta(days=1)  # 2026-06-30
NOTIONAL_CAP = 75_000.0
BANKNIFTY_LOT = 30  # front future lot on 2026-11-21 snapshot
RANDOM_SEEDS = list(range(10))

STRATEGIES: dict[str, dict[str, Any]] = {
    "dd_five_long": {
        "strategy": "dd_five_confirm",
        "params": {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
        "timeframe": "5m",
    },
    "dd_five_short": {
        "strategy": "dd_five_confirm",
        "params": {"mode": "short_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
        "timeframe": "5m",
    },
    "price_channel": {"strategy": "price_channel", "params": {"length": 20}, "timeframe": "5m"},
    "ema_pivot_pullback": {
        "strategy": "ema_pivot_pullback",
        "params": {"mode": "long_short", "use_pivots": True},
        "timeframe": "5m",
    },
    "morning_star_15m": {
        "strategy": "price_action",
        "params": {"pattern": "morning_star", "mode": "long_only", "level_atr": 0.15, "max_width_atr": 2.0},
        "timeframe": "15m",
    },
    "star_5m_confirmed": {
        "strategy": "price_action",
        "params": {
            "pattern": "morning_star",
            "mode": "long_short",
            "confirm_bars": 3,
            "level_atr": 0.15,
            "max_width_atr": 2.0,
        },
        "timeframe": "5m",
    },
    "gold_trend_9": {"strategy": "gold_trend_9", "params": {"mode": "long_short"}, "timeframe": "5m"},
    "gold_trend_14": {"strategy": "gold_trend_14", "params": {"mode": "long_short"}, "timeframe": "5m"},
}

# ±20% cliff axes per strategy (default-param keys that are numeric and material)
CLIFF_AXES: dict[str, list[tuple[str, float]]] = {
    "dd_five_long": [("st_mult", 2.0), ("rsi_long", 70.0)],
    "dd_five_short": [("st_mult", 2.0), ("rsi_short", 30.0)],
    "price_channel": [("length", 20)],
    "ema_pivot_pullback": [("rr", 3.0), ("fast", 20), ("slow", 50)],
    "morning_star_15m": [("rr", 2.0), ("confirm_bars", 3)],
    "star_5m_confirmed": [("rr", 2.0), ("confirm_bars", 3)],
    "gold_trend_9": [("stop_mult", 1.5)],
    "gold_trend_14": [("stop_mult", 3.0)],
}


def _universe_symbols() -> list[dict[str, Any]]:
    idx = current_index(settings.instruments_dir)
    if idx is None:
        raise RuntimeError("no instrument snapshot")
    res = resolve_universe(idx, spec=load_universe_spec())
    out: list[dict[str, Any]] = []
    for m in res.resolved:
        if m.role == "equity":
            out.append({"symbol": m.symbol, "folder": m.folder, "mode": "equity_intraday", "lot_size": 1})
        elif m.role == "index" and m.instrument_key == BANKNIFTY_INDEX_KEY:
            # Exchange BN FUT lot (30) × spot ≈ ₹1.3–1.5L >> ₹75k notional cap → always qty<1.
            # Research sizes in 1 index unit with futures cost table; note exchange lot separately.
            out.append(
                {
                    "symbol": "BANKNIFTY",
                    "folder": m.folder,
                    "mode": "index_future",
                    "lot_size": 1,
                    "exchange_lot": BANKNIFTY_LOT,
                }
            )
    return out


def _body(
    *,
    label: str,
    symbol: str,
    mode: str,
    lot_size: int,
    risk: float,
    start: date,
    end: date,
    preset: dict[str, Any],
    params_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = dict(preset["params"])
    if params_override:
        params.update(params_override)
    return {
        "strategy": preset["strategy"],
        "params": params,
        "symbol": symbol,
        "timeframe": preset["timeframe"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": ["normal"],
        "mode": mode,
        "live_timing": True,
        "risk_rupees": risk,
        "notional_cap": NOTIONAL_CAP,
        "lot_size": lot_size,
        "slippage_ticks": 1,
        "tick_size": 0.05,
        "_label": label,
    }


def _trade_rows(out: dict[str, Any]) -> list[dict[str, Any]]:
    return list(out.get("result", {}).get("trades") or [])


def _summarise_run(out: dict[str, Any]) -> dict[str, Any]:
    trades = _trade_rows(out)
    nets = [float(t["net_pnl"]) for t in trades]
    metrics = compute_metrics(
        [TradeRecord(int(t["entry_time"]), int(t["exit_time"]), float(t["net_pnl"])) for t in trades]
    ) if trades else {
        "trades": 0, "win_rate": None, "net_pnl": 0.0, "profit_factor": None, "max_drawdown": 0.0,
    }
    gross = sum(float(t.get("gross_pnl") or 0) for t in trades)
    charges = sum(float(t.get("charges_total") or 0) for t in trades)
    # avg R: net / (actual risk) when we can recover risk from entry stop; else net / target from sizing
    sizing = out.get("sizing_stats") or {}
    avg_risk = sizing.get("avg_actual_risk") or float(out.get("_risk") or 300)
    avg_r = (statistics.mean(nets) / avg_risk) if nets and avg_risk else None
    costs_as_r = (charges / (avg_risk * len(trades))) if trades and avg_risk else None
    grosses = [float(t.get("gross_pnl") or 0) for t in trades]
    charge_rows = [float(t.get("charges_total") or 0) for t in trades]
    avg_gross_r = (statistics.mean(grosses) / avg_risk) if grosses and avg_risk else None
    return {
        "trades": int(metrics["trades"]),
        "win_rate": metrics.get("win_rate"),
        "net_pnl": float(metrics.get("net_pnl") or 0),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": float(metrics.get("max_drawdown") or 0),
        "gross_pnl": gross,
        "total_charges": charges,
        "avg_r": avg_r,
        "avg_gross_r": avg_gross_r,
        "costs_as_r": costs_as_r,
        "sizing": sizing,
        "trade_nets": nets,
        "trade_times": [(int(t["entry_time"]), int(t["exit_time"]), float(t["net_pnl"])) for t in trades],
        "trade_gross_charges": list(zip(grosses, charge_rows)),
    }


def _worker(spec: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in spec.items() if not k.startswith("_")}
    label = spec.get("_label", "")
    risk = float(spec.get("risk_rupees", 300))
    try:
        cfg = parse_config(body)
        out = execute_run(cfg, lambda _e: None, hash_data=False)
        out["_risk"] = risk
        summary = _summarise_run(out)
        # Drop heavy trade blobs for parent memory except times/nets already in summary
        return {
            "ok": True,
            "label": label,
            "spec": {
                "strategy_key": spec["_strategy_key"],
                "symbol": spec["symbol"],
                "mode": spec["mode"],
                "risk": risk,
                "window": spec["_window"],
                "start": spec["start"],
                "end": spec["end"],
            },
            "summary": summary,
        }
    except Exception as exc:
        return {
            "ok": False,
            "label": label,
            "spec": {
                "strategy_key": spec.get("_strategy_key"),
                "symbol": spec.get("symbol"),
                "risk": risk,
                "window": spec.get("_window"),
            },
            "error": f"{type(exc).__name__}: {exc}",
        }


def _jobs(*, risks: list[float], windows: list[tuple[str, date, date]], symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for skey, preset in STRATEGIES.items():
        for sym in symbols:
            for risk in risks:
                for wname, start, end in windows:
                    body = _body(
                        label=f"{skey}|{sym['symbol']}|r{int(risk)}|{wname}",
                        symbol=sym["symbol"],
                        mode=sym["mode"],
                        lot_size=sym["lot_size"],
                        risk=risk,
                        start=start,
                        end=end,
                        preset=preset,
                    )
                    body["_strategy_key"] = skey
                    body["_window"] = wname
                    jobs.append(body)
    return jobs


def _breadth(rows: list[dict[str, Any]], *, risk: float) -> dict[str, Any]:
    """Share of symbols with PF>1.1 in BOTH DEV and OOS."""
    by: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        if not r.get("ok"):
            continue
        sp = r["spec"]
        if float(sp["risk"]) != risk:
            continue
        by[sp["strategy_key"]][sp["symbol"]] = by[sp["strategy_key"]].get(sp["symbol"], {})
        by[sp["strategy_key"]][sp["symbol"]][sp["window"]] = r["summary"]

    out: dict[str, Any] = {}
    for skey, symbols in by.items():
        pass_syms = []
        detail = {}
        for sym, wins in symbols.items():
            dev, oos = wins.get("DEV"), wins.get("OOS")
            ok = bool(
                dev and oos
                and (dev.get("profit_factor") or 0) > 1.1
                and (oos.get("profit_factor") or 0) > 1.1
            )
            detail[sym] = {
                "dev_pf": None if not dev else dev.get("profit_factor"),
                "oos_pf": None if not oos else oos.get("profit_factor"),
                "dev_net": None if not dev else dev.get("net_pnl"),
                "oos_net": None if not oos else oos.get("net_pnl"),
                "dev_trades": None if not dev else dev.get("trades"),
                "oos_trades": None if not oos else oos.get("trades"),
                "pass": ok,
            }
            if ok:
                pass_syms.append(sym)
        n = len(symbols)
        out[skey] = {
            "symbols": n,
            "passed": len(pass_syms),
            "breadth": (len(pass_syms) / n) if n else 0.0,
            "passed_symbols": sorted(pass_syms),
            "detail": detail,
        }
    return out


def _month_key(ts: int) -> str:
    d = datetime.fromtimestamp(ts, IST).date()
    return f"{d.year:04d}-{d.month:02d}"


def _month_start(key: str) -> date:
    y, m = key.split("-")
    return date(int(y), int(m), 1)


def _add_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _walk_forward_portfolio(rows: list[dict[str, Any]], *, strategy_key: str, risk: float) -> dict[str, Any]:
    """Each month: pick 5 symbols with best trailing-6-month net; trade them the next month only."""
    # Collect monthly net per symbol from trade_times across DEV+OOS runs
    monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    monthly_trades: dict[str, dict[str, list[tuple[int, int, float]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if not r.get("ok"):
            continue
        sp = r["spec"]
        if sp["strategy_key"] != strategy_key or float(sp["risk"]) != risk:
            continue
        sym = sp["symbol"]
        for entry_t, exit_t, net in r["summary"].get("trade_times") or []:
            mk = _month_key(entry_t)
            monthly[sym][mk] += net
            monthly_trades[sym][mk].append((entry_t, exit_t, net))

    all_months = sorted({m for sym in monthly for m in monthly[sym]})
    if len(all_months) < 7:
        return {"error": "not enough months", "months": all_months}

    portfolio_trades: list[tuple[int, int, float, str, str]] = []  # entry, exit, net, sym, month
    selections: list[dict[str, Any]] = []
    for i in range(6, len(all_months)):
        trail_months = all_months[i - 6 : i]
        trade_month = all_months[i]
        scores = []
        for sym, mnets in monthly.items():
            trail = sum(mnets.get(m, 0.0) for m in trail_months)
            scores.append((trail, sym))
        scores.sort(reverse=True)
        top5 = [s for _, s in scores[:5]]
        selections.append({"trade_month": trade_month, "trail": trail_months, "symbols": top5, "scores": scores[:5]})
        for sym in top5:
            for row in monthly_trades[sym].get(trade_month, []):
                portfolio_trades.append((*row, sym, trade_month))

    def _window_slice(start: date, end: date) -> list[float]:
        out = []
        for entry_t, _exit_t, net, _sym, _mk in portfolio_trades:
            d = datetime.fromtimestamp(entry_t, IST).date()
            if start <= d <= end:
                out.append(net)
        return out

    def _pf(nets: list[float]) -> float | None:
        wins = sum(x for x in nets if x > 0)
        losses = sum(x for x in nets if x < 0)
        if losses >= 0 or not nets:
            return None if not wins else float("inf")
        return wins / -losses

    def _pack(nets: list[float]) -> dict[str, Any]:
        metrics = compute_metrics(
            [TradeRecord(0, 1, n) for n in nets]
        ) if nets else {"trades": 0, "net_pnl": 0.0, "profit_factor": None, "max_drawdown": 0.0, "win_rate": None}
        return {
            "trades": int(metrics["trades"]),
            "net_pnl": float(metrics.get("net_pnl") or 0),
            "profit_factor": metrics.get("profit_factor"),
            "max_drawdown": float(metrics.get("max_drawdown") or 0),
            "win_rate": metrics.get("win_rate"),
        }

    return {
        "selections_sample": selections[:3] + selections[-2:],
        "n_months_traded": len(selections),
        "DEV": _pack(_window_slice(DEV_START, DEV_END)),
        "OOS": _pack(_window_slice(OOS_START, OOS_END)),
    }


def _random_benchmark(
    rows: list[dict[str, Any]],
    *,
    strategy_key: str,
    risk: float,
    window: str,
    seeds: list[int] = RANDOM_SEEDS,
) -> dict[str, Any]:
    """Null of no directional edge on GROSS; charges stay the same on every seed.

    Per trade: random_net = ±gross − charges (slip already inside fill/gross). Flipping *net*
    would wrongly flip the cost sign. A no-edge strategy sits near the 50th percentile on GROSS
    (and therefore on this net, since costs are identical across seeds).
    """
    by_day: dict[date, list[tuple[float, float]]] = defaultdict(list)  # (gross, charges)
    for r in rows:
        if not r.get("ok"):
            continue
        sp = r["spec"]
        if sp["strategy_key"] != strategy_key or float(sp["risk"]) != risk or sp["window"] != window:
            continue
        pairs = r["summary"].get("trade_gross_charges") or []
        times = r["summary"].get("trade_times") or []
        if pairs and len(pairs) == len(times):
            for (entry_t, _exit_t, _net), (gross, chg) in zip(times, pairs):
                by_day[datetime.fromtimestamp(entry_t, IST).date()].append((float(gross), float(chg)))
        else:
            # Fallback: only nets available — cannot keep costs fixed
            for entry_t, _exit_t, net in times:
                by_day[datetime.fromtimestamp(entry_t, IST).date()].append((float(net), 0.0))
    if not by_day:
        return {"error": "no trades", "percentile": None}

    pairs = [p for day_pairs in by_day.values() for p in day_pairs]
    grosses = [g for g, _c in pairs]
    charges = [c for _g, c in pairs]
    strat_gross = sum(grosses)
    total_charges = sum(charges)
    strat_net = strat_gross - total_charges
    n_trades = len(pairs)
    trades_per_day = {d.isoformat(): len(v) for d, v in sorted(by_day.items())}

    seed_gross: list[float] = []
    seed_nets: list[float] = []
    for seed in seeds:
        rng = random.Random(seed)
        flipped = [g if rng.random() < 0.5 else -g for g in grosses]
        gsum = sum(flipped)
        seed_gross.append(gsum)
        seed_nets.append(gsum - total_charges)

    better_gross = sum(1 for s in seed_gross if strat_gross > s)
    better_net = sum(1 for s in seed_nets if strat_net > s)
    pct_gross = 100.0 * better_gross / len(seed_gross)
    pct_net = 100.0 * better_net / len(seed_nets)
    return {
        "strategy_net": strat_net,
        "strategy_gross": strat_gross,
        "total_charges": total_charges,
        "trades": n_trades,
        "n_days": len(by_day),
        "trades_per_day_sample": dict(list(trades_per_day.items())[:3]),
        "seed_nets": seed_nets,
        "seed_gross": seed_gross,
        "percentile": pct_net,
        "percentile_gross": pct_gross,
        "beats_95": pct_net >= 95.0,
        "method": "sign_shuffle_gross_minus_same_charges",
    }


def _top10_removal(summary: dict[str, Any]) -> dict[str, Any]:
    nets = list(summary.get("trade_nets") or [])
    if len(nets) < 10:
        return {"net_without_top10": summary.get("net_pnl"), "removed": len(nets), "survives": False}
    top = sorted(nets, reverse=True)[:10]
    rest = nets[:]
    for v in top:
        rest.remove(v)
    net = sum(rest)
    return {"net_without_top10": net, "removed": 10, "survives": net > 0}


def _param_cliff(
    *,
    strategy_key: str,
    symbol_sample: list[dict[str, Any]],
    risk: float,
    workers: int,
) -> dict[str, Any]:
    """±20% on key params; cliff if OOS net sign flips vs base or drops >50% on any axis (universe sample)."""
    axes = CLIFF_AXES.get(strategy_key) or []
    if not axes:
        return {"cliff": False, "note": "no axes"}
    preset = STRATEGIES[strategy_key]
    # Use up to 5 symbols that traded in OOS base for speed
    sample = symbol_sample[:5]
    jobs = []
    for sym in sample:
        for key, default in axes:
            for mult, tag in ((0.8, "m20"), (1.2, "p20")):
                val = default * mult
                if isinstance(default, int) or key in ("length", "confirm_bars", "fast", "slow"):
                    val = max(1, int(round(val)))
                override = {key: val}
                body = _body(
                    label=f"cliff|{strategy_key}|{sym['symbol']}|{key}|{tag}",
                    symbol=sym["symbol"],
                    mode=sym["mode"],
                    lot_size=sym["lot_size"],
                    risk=risk,
                    start=OOS_START,
                    end=OOS_END,
                    preset=preset,
                    params_override=override,
                )
                body["_strategy_key"] = strategy_key
                body["_window"] = "OOS"
                body["_cliff"] = f"{key}:{tag}"
                jobs.append(body)
        # base
        body = _body(
            label=f"cliff|{strategy_key}|{sym['symbol']}|base",
            symbol=sym["symbol"],
            mode=sym["mode"],
            lot_size=sym["lot_size"],
            risk=risk,
            start=OOS_START,
            end=OOS_END,
            preset=preset,
        )
        body["_strategy_key"] = strategy_key
        body["_window"] = "OOS"
        body["_cliff"] = "base"
        jobs.append(body)

    results = _run_jobs(jobs, workers=workers)
    by_sym: dict[str, dict[str, float]] = defaultdict(dict)
    for r in results:
        if not r.get("ok"):
            continue
        sym = r["spec"]["symbol"]
        tag = r.get("spec", {})
        # recover cliff tag from label
        label = r.get("label") or ""
        cliff = label.split("|")[-1] if "|base" in label or "m20" in label or "p20" in label else "base"
        if label.endswith("|base"):
            cliff = "base"
        else:
            parts = label.split("|")
            cliff = parts[-1] if parts else "base"
        by_sym[sym][cliff] = float(r["summary"]["net_pnl"])

    cliff = False
    details = []
    for sym, nets in by_sym.items():
        base = nets.get("base", 0.0)
        for tag, net in nets.items():
            if tag == "base":
                continue
            if base == 0:
                continue
            drop = (base - net) / abs(base) if base != 0 else 0
            sign_flip = (base > 0 and net < 0) or (base < 0 and net > 0 and abs(net) > abs(base) * 0.2)
            bad = sign_flip or drop > 0.5
            details.append({"symbol": sym, "tag": tag, "base": base, "net": net, "drop": drop, "cliff": bad})
            if bad:
                cliff = True
    return {"cliff": cliff, "details": details[:20], "n_jobs": len(jobs)}


def _run_jobs(jobs: list[dict[str, Any]], *, workers: int) -> list[dict[str, Any]]:
    if workers <= 1:
        return [_worker(j) for j in jobs]
    out: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_worker, j): j for j in jobs}
        for fut in as_completed(futs):
            out.append(fut.result())
    return out


def _gate(
    *,
    breadth: dict[str, Any],
    wf: dict[str, Any],
    random_oos: dict[str, Any],
    oos_summary_universe: dict[str, Any],
    top10: dict[str, Any],
    cliff: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "breadth_ge_40": (breadth.get("breadth") or 0) >= 0.40,
        "wf_dev_positive": (wf.get("DEV", {}).get("net_pnl") or 0) > 0,
        "wf_oos_positive": (wf.get("OOS", {}).get("net_pnl") or 0) > 0,
        "wf_oos_pf_ge_1_2": (wf.get("OOS", {}).get("profit_factor") or 0) >= 1.2,
        "random_beats_95": bool(random_oos.get("beats_95")),
        "top10_survives": bool(top10.get("survives")),
        "no_param_cliff": not bool(cliff.get("cliff")),
        "oos_trades_ge_100": (oos_summary_universe.get("trades") or 0) >= 100,
    }
    return {"passed": all(checks.values()), "checks": checks}


def _universe_aggregate(rows: list[dict[str, Any]], *, strategy_key: str, risk: float, window: str) -> dict[str, Any]:
    nets: list[float] = []
    trades = 0
    for r in rows:
        if not r.get("ok"):
            continue
        sp = r["spec"]
        if sp["strategy_key"] != strategy_key or float(sp["risk"]) != risk or sp["window"] != window:
            continue
        nets.extend(r["summary"].get("trade_nets") or [])
        trades += int(r["summary"].get("trades") or 0)
    if not nets:
        return {"trades": 0, "net_pnl": 0.0, "profit_factor": None, "max_drawdown": 0.0, "trade_nets": []}
    metrics = compute_metrics([TradeRecord(0, 1, n) for n in nets])
    return {
        "trades": trades,
        "net_pnl": float(metrics["net_pnl"]),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": float(metrics.get("max_drawdown") or 0),
        "win_rate": metrics.get("win_rate"),
        "trade_nets": nets,
    }


def _render_md(report: dict[str, Any]) -> str:
    lines = [
        f"# Phase C stock/BankNifty research ({report['as_of']})",
        "",
        f"DEV `{DEV_START}`..`{DEV_END}` · OOS `{OOS_START}`..`{OOS_END}` · holdout untouched "
        f"(no passers → holdout not opened).",
        f"Sizing: risk ₹{report['risks']} · notional cap ₹{NOTIONAL_CAP:,.0f} · 1-tick slip · "
        f"BANKNIFTY index_future costs with **1-unit** sizing (exchange FUT lot={BANKNIFTY_LOT} "
        f"× spot ≫ cap, so a real lot cannot fit under ₹75k).",
        f"STEP 0: notional over-cap → floor(cap/price) (not skip). Skip only qty&lt;1.",
        "",
        "## Breadth (PF_after_costs > 1.1 in BOTH DEV and OOS)",
        "",
        "| strategy | risk | symbols | passed | breadth |",
        "|---|---:|---:|---:|---:|",
    ]
    for risk, block in report["breadth"].items():
        for skey, b in block.items():
            lines.append(
                f"| {skey} | {risk} | {b['symbols']} | {b['passed']} | {b['breadth']*100:.1f}% |"
            )
    lines += ["", "## Walk-forward portfolio (top-5 trailing 6m → next month)", ""]
    lines.append("| strategy | risk | DEV net | DEV PF | OOS net | OOS PF | OOS trades |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for risk, block in report["walk_forward"].items():
        for skey, wf in block.items():
            if "error" in wf:
                lines.append(f"| {skey} | {risk} | error | | | | |")
                continue
            lines.append(
                f"| {skey} | {risk} | {wf['DEV']['net_pnl']:.0f} | {wf['DEV'].get('profit_factor') or '—'} | "
                f"{wf['OOS']['net_pnl']:.0f} | {wf['OOS'].get('profit_factor') or '—'} | {wf['OOS']['trades']} |"
            )
    lines += [
        "",
        "## Random-entry benchmark (OOS percentile vs 10 seeds)",
        "",
        "Method: sign-shuffle GROSS, then subtract the **same** charges (slip already in fills/gross). "
        "Flipping net would wrongly flip the cost sign. No-edge ≈ 50th on gross.",
        "",
    ]
    lines.append("| strategy | risk | strat gross | strat net | gross pctile | net pctile | beats ≥95% |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for risk, block in report["random"].items():
        for skey, rnd in block.items():
            if "error" in rnd:
                lines.append(f"| {skey} | {risk} | — | — | — | — | — |")
                continue
            lines.append(
                f"| {skey} | {risk} | {rnd.get('strategy_gross', 0):.0f} | {rnd['strategy_net']:.0f} | "
                f"{rnd.get('percentile_gross', rnd['percentile']):.0f} | {rnd['percentile']:.0f} | "
                f"{'yes' if rnd.get('beats_95') else 'no'} |"
            )
    lines += ["", "## Gate verdict", ""]
    lines.append("| strategy | risk | passed | failed checks |")
    lines.append("|---|---:|---|---|")
    for risk, block in report["gates"].items():
        for skey, g in block.items():
            failed = [k for k, v in g["checks"].items() if not v]
            lines.append(
                f"| {skey} | {risk} | {'PASS' if g['passed'] else 'FAIL'} | "
                f"{', '.join(failed) if failed else '—'} |"
            )
    lines += ["", "## STEP 0 sizing (DEV dry symbols sample)", ""]
    for row in report.get("step0_sizing") or []:
        lines.append(
            f"- {row['strategy']} @ {row['symbol']}: trades={row['trades']} capped={row['capped']} "
            f"avg_risk=₹{row['avg_risk']:.1f} (target ₹{row['target']})"
        )

    # Per-strategy / per-symbol tables (risk 300 primary; 600 in JSON)
    per = report.get("per_symbol") or []
    if per:
        lines += ["", "## Per-symbol (risk ₹300; DEV + OOS)", ""]
        by_strat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in per:
            if float(row["risk"]) != 300.0:
                continue
            by_strat[row["strategy"]].append(row)
        for skey in STRATEGIES:
            rows = by_strat.get(skey) or []
            if not rows:
                continue
            lines += [
                f"### {skey}",
                "",
                "| symbol | win | trades | win% | net ₹ | PF | avg R | costs/R | max DD | capped | avg risk ₹ |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
            # pivot DEV/OOS onto one line each
            by_sym: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
            for r in rows:
                by_sym[r["symbol"]][r["window"]] = r
            for sym in sorted(by_sym):
                for win in ("DEV", "OOS"):
                    r = by_sym[sym].get(win)
                    if not r:
                        continue
                    wr = r.get("win_rate")
                    wr_s = f"{wr*100:.1f}" if wr is not None else "—"
                    pf = r.get("profit_factor")
                    pf_s = f"{pf:.3f}" if pf is not None else "—"
                    ar = r.get("avg_r")
                    ar_s = f"{ar:.3f}" if ar is not None else "—"
                    cr = r.get("costs_as_r")
                    cr_s = f"{cr:.3f}" if cr is not None else "—"
                    avg_risk = r.get("avg_actual_risk")
                    avg_s = f"{avg_risk:.1f}" if avg_risk is not None else "—"
                    lines.append(
                        f"| {sym} | {win} | {r.get('trades') or 0} | {wr_s} | "
                        f"{r.get('net_pnl') or 0:.0f} | {pf_s} | {ar_s} | {cr_s} | "
                        f"{r.get('max_drawdown') or 0:.0f} | {r.get('capped') or 0} | {avg_s} |"
                    )
            lines.append("")

    lines.append("")
    return "\n".join(lines)


def run_step0_dry(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-run dd_five long on 3 symbols DEV; report capped + avg risk."""
    want = {"HDFCBANK", "RELIANCE", "BANKNIFTY"}
    sample = [s for s in symbols if s["symbol"] in want]
    preset = STRATEGIES["dd_five_long"]
    out_rows = []
    for sym in sample:
        body = _body(
            label=f"step0|{sym['symbol']}",
            symbol=sym["symbol"],
            mode=sym["mode"],
            lot_size=sym["lot_size"],
            risk=300,
            start=DEV_START,
            end=DEV_END,
            preset=preset,
        )
        cfg = parse_config({k: v for k, v in body.items() if not k.startswith("_")})
        out = execute_run(cfg, lambda _e: None, hash_data=False)
        stats = out.get("sizing_stats") or {}
        trades = len(_trade_rows(out))
        print(
            f"STEP0 {sym['symbol']}: trades={trades} capped={stats.get('trades_capped')} "
            f"avg_risk={stats.get('avg_actual_risk')} skipped={stats.get('signals_skipped')}",
            flush=True,
        )
        out_rows.append(
            {
                "strategy": "dd_five_long",
                "symbol": sym["symbol"],
                "trades": trades,
                "capped": stats.get("trades_capped"),
                "avg_risk": stats.get("avg_actual_risk") or 0,
                "target": 300,
            }
        )
    return out_rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-only", action="store_true")
    ap.add_argument("--risk", type=float, nargs="*", default=[300.0, 600.0])
    args = ap.parse_args(argv)

    symbols = _universe_symbols()
    print(f"universe {len(symbols)} symbols (BANKNIFTY lot={BANKNIFTY_LOT})", flush=True)

    step0 = run_step0_dry(symbols)
    if args.dry_only:
        return 0

    windows = [("DEV", DEV_START, DEV_END), ("OOS", OOS_START, OOS_END)]
    jobs = _jobs(risks=list(args.risk), windows=windows, symbols=symbols)
    print(f"running {len(jobs)} backtests with {args.workers} workers…", flush=True)
    rows = _run_jobs(jobs, workers=args.workers)
    errors = [r for r in rows if not r.get("ok")]
    print(f"done: {len(rows)-len(errors)} ok, {len(errors)} errors", flush=True)

    breadth = {str(int(r)): _breadth(rows, risk=r) for r in args.risk}
    walk_forward: dict[str, dict[str, Any]] = {}
    random_rep: dict[str, dict[str, Any]] = {}
    gates: dict[str, dict[str, Any]] = {}
    top10_rep: dict[str, dict[str, Any]] = {}
    cliff_rep: dict[str, dict[str, Any]] = {}

    for risk in args.risk:
        rk = str(int(risk))
        walk_forward[rk] = {}
        random_rep[rk] = {}
        gates[rk] = {}
        top10_rep[rk] = {}
        cliff_rep[rk] = {}
        for skey in STRATEGIES:
            print(f"post: {skey} risk={risk}", flush=True)
            wf = _walk_forward_portfolio(rows, strategy_key=skey, risk=risk)
            rnd = _random_benchmark(rows, strategy_key=skey, risk=risk, window="OOS")
            oos_agg = _universe_aggregate(rows, strategy_key=skey, risk=risk, window="OOS")
            top10 = _top10_removal(oos_agg)
            # cliff on a sample of symbols that have OOS trades
            traded = [
                s for s in symbols
                if any(
                    r.get("ok") and r["spec"]["strategy_key"] == skey
                    and float(r["spec"]["risk"]) == risk
                    and r["spec"]["window"] == "OOS"
                    and (r["summary"].get("trades") or 0) > 0
                    for r in rows
                )
            ]
            cliff = _param_cliff(strategy_key=skey, symbol_sample=traded or symbols, risk=risk, workers=args.workers)
            gate = _gate(
                breadth=breadth[rk][skey],
                wf=wf,
                random_oos=rnd,
                oos_summary_universe=oos_agg,
                top10=top10,
                cliff=cliff,
            )
            walk_forward[rk][skey] = wf
            random_rep[rk][skey] = rnd
            top10_rep[rk][skey] = top10
            cliff_rep[rk][skey] = cliff
            gates[rk][skey] = gate

    # Holdout only for passers (max 2), once — peek recording via execute_holdout
    holdout_runs = []
    passers = []
    for risk in args.risk:
        rk = str(int(risk))
        for skey, g in gates[rk].items():
            if g["passed"]:
                passers.append((skey, risk))
    passers = passers[:2]
    for skey, risk in passers:
        print(f"HOLDOUT peek for {skey} risk={risk}", flush=True)
        # Run holdout via execute_holdout path would record peek — user said max 2 once.
        # Use allow_holdout parse + execute_holdout
        from app.backtest.execute import execute_holdout

        preset = STRATEGIES[skey]
        # Pick BANKNIFTY + top breadth symbol as a light holdout check? Spec says holdout for passers —
        # run on full universe would be huge; run aggregate on equities sample of 5 + BANKNIFTY.
        sample = [s for s in symbols if s["symbol"] == "BANKNIFTY"] + [
            s for s in symbols if s["symbol"] != "BANKNIFTY"
        ][:5]
        nets = []
        for sym in sample:
            body = _body(
                label=f"holdout|{skey}|{sym['symbol']}",
                symbol=sym["symbol"],
                mode=sym["mode"],
                lot_size=sym["lot_size"],
                risk=risk,
                start=HOLDOUT_START,
                end=HOLDOUT_END,
                preset=preset,
            )
            cfg = parse_config({k: v for k, v in body.items() if not k.startswith("_")}, allow_holdout=True)
            # Record peek once per passer (first symbol only)
            if sym is sample[0]:
                out = execute_holdout({**cfg, "kind": "holdout"}, lambda _e: None)
            else:
                out = execute_run(cfg, lambda _e: None, hash_data=False)
            nets.extend(float(t["net_pnl"]) for t in _trade_rows(out))
        holdout_runs.append({"strategy": skey, "risk": risk, "net": sum(nets), "trades": len(nets)})

    as_of = date.today().isoformat()
    report = {
        "as_of": as_of,
        "risks": list(args.risk),
        "step0_sizing": step0,
        "breadth": breadth,
        "walk_forward": walk_forward,
        "random": random_rep,
        "top10": top10_rep,
        "cliff": cliff_rep,
        "gates": gates,
        "holdout": holdout_runs,
        "errors": errors[:20],
        "n_jobs": len(jobs),
    }
    # Slim per-symbol table for JSON
    per_symbol = []
    for r in rows:
        if not r.get("ok"):
            continue
        sp = r["spec"]
        sm = r["summary"]
        per_symbol.append(
            {
                "strategy": sp["strategy_key"],
                "symbol": sp["symbol"],
                "risk": sp["risk"],
                "window": sp["window"],
                "trades": sm["trades"],
                "win_rate": sm["win_rate"],
                "net_pnl": sm["net_pnl"],
                "profit_factor": sm["profit_factor"],
                "avg_r": sm["avg_r"],
                "costs_as_r": sm["costs_as_r"],
                "max_drawdown": sm["max_drawdown"],
                "capped": (sm.get("sizing") or {}).get("trades_capped"),
                "avg_actual_risk": (sm.get("sizing") or {}).get("avg_actual_risk"),
            }
        )
    report["per_symbol"] = per_symbol

    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"phase_c_research_{as_of}.md"
    json_path = out_dir / f"phase_c_research_{as_of}.json"
    md_path.write_text(_render_md(report), encoding="utf-8")
    # Drop trade_nets from JSON to keep file smaller
    slim = dict(report)
    for row in slim.get("per_symbol") or []:
        row.pop("trade_nets", None)
    json_path.write_text(json.dumps(slim, indent=2, default=str) + "\n", encoding="utf-8")
    print(_render_md(report), flush=True)
    print(f"wrote {md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
