"""Phase C sanity checks (no holdout, no new research gates).

    cd backend && uv run python -m scripts.phase_c_sanity

1. Random null on GROSS: sign-shuffle |gross|, keep the same charges/slip per trade.
2. Direction: dd_five_long NIFTY index vs index_future (1 unit, zero costs) trade parity.
3. Cost share: avg gross R and avg cost R per strategy × risk from Phase C JSON (or live runs).
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.backtest.catalog import NIFTY_SYMBOL, build_strategy, parse_config
from app.backtest.costs import CostModel, Slippage
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.execute import execute_run
from app.backtest.sources import StoreSource
from app.config import settings
from app.data.store import CandleStore

IST = timezone(timedelta(hours=5, minutes=30))
DEV_START = date(2022, 1, 3)
DEV_END = date(2024, 12, 31)
RANDOM_SEEDS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89]
DRY_SYMBOLS = ("HDFCBANK", "RELIANCE", "BANKNIFTY")
STRATEGIES = (
    "dd_five_long",
    "dd_five_short",
    "price_channel",
    "ema_pivot_pullback",
    "morning_star_15m",
    "star_5m_confirmed",
    "gold_trend_9",
    "gold_trend_14",
)


def _preset(key: str) -> dict[str, Any]:
    from scripts.phase_c_research_suite import STRATEGIES as PRESETS

    return PRESETS[key]


def _universe_row(symbol: str) -> dict[str, Any]:
    from scripts.phase_c_research_suite import _universe_symbols

    for row in _universe_symbols():
        if row["symbol"] == symbol:
            return row
    raise KeyError(symbol)


def _points(t: dict[str, Any]) -> float:
    sign = 1.0 if t["direction"] == "LONG" else -1.0
    return (float(t["exit_price"]) - float(t["entry_price"])) * sign


def _percentile(strat: float, seeds: list[float]) -> float:
    if not seeds:
        return float("nan")
    return 100.0 * sum(1 for s in seeds if strat > s) / len(seeds)


def check_random_gross() -> dict[str, Any]:
    """Sign-shuffle GROSS; subtract the SAME charges. Also show broken net-shuffle for contrast."""
    out: dict[str, Any] = {"symbols": {}, "note": (
        "Correct null: random_net = ±gross − same charges (slip already in fill/gross). "
        "Broken Phase-C-v1 null flipped net (charges change sign)."
    )}
    preset = _preset("dd_five_long")
    for symbol in DRY_SYMBOLS:
        sym = _universe_row(symbol)
        body = {
            "strategy": preset["strategy"],
            "params": dict(preset["params"]),
            "symbol": symbol,
            "timeframe": preset["timeframe"],
            "start": DEV_START.isoformat(),
            "end": DEV_END.isoformat(),
            "sessions": ["normal"],
            "mode": sym["mode"],
            "live_timing": True,
            "risk_rupees": 300,
            "notional_cap": 75_000,
            "lot_size": sym["lot_size"],
            "slippage_ticks": 1,
            "tick_size": 0.05,
        }
        cfg = parse_config(body)
        run = execute_run(cfg, lambda _e: None, hash_data=False)
        trades = list(run.get("result", {}).get("trades") or [])
        grosses = [float(t["gross_pnl"]) for t in trades]
        charges = [float(t["charges_total"]) for t in trades]
        slips = [float(t.get("slippage_cost") or 0) for t in trades]
        nets = [float(t["net_pnl"]) for t in trades]
        # Identity: net == gross - charges (within 1 paisa)
        max_err = max((abs(n - (g - c)) for n, g, c in zip(nets, grosses, charges)), default=0.0)

        strat_gross = sum(grosses)
        strat_net = sum(nets)
        same_charges_total = sum(charges)

        seed_gross: list[float] = []
        seed_net_correct: list[float] = []
        seed_net_broken: list[float] = []
        for seed in RANDOM_SEEDS:
            rng = random.Random(seed)
            flipped = [g if rng.random() < 0.5 else -g for g in grosses]
            seed_gross.append(sum(flipped))
            seed_net_correct.append(sum(flipped) - same_charges_total)
            # broken: flip net (costs change sign)
            rng2 = random.Random(seed)
            seed_net_broken.append(sum(n if rng2.random() < 0.5 else -n for n in nets))

        out["symbols"][symbol] = {
            "trades": len(trades),
            "strategy_gross": round(strat_gross, 2),
            "strategy_net": round(strat_net, 2),
            "total_charges": round(same_charges_total, 2),
            "total_slippage_in_fills": round(sum(slips), 2),
            "net_equals_gross_minus_charges_max_err": round(max_err, 4),
            "gross_percentile_vs_sign_shuffle": round(_percentile(strat_gross, seed_gross), 1),
            "net_percentile_correct_same_costs": round(_percentile(strat_net, seed_net_correct), 1),
            "net_percentile_broken_flip_net": round(_percentile(strat_net, seed_net_broken), 1),
            "random_gross_seeds": [round(x, 2) for x in seed_gross],
            "random_net_correct_seeds": [round(x, 2) for x in seed_net_correct],
        }
        print(
            f"RANDOM {symbol}: trades={len(trades)} gross={strat_gross:.0f} net={strat_net:.0f} "
            f"gross_pct={out['symbols'][symbol]['gross_percentile_vs_sign_shuffle']} "
            f"net_pct_correct={out['symbols'][symbol]['net_percentile_correct_same_costs']} "
            f"net_pct_broken={out['symbols'][symbol]['net_percentile_broken_flip_net']}",
            flush=True,
        )
    return out


def _run_index_points() -> list[dict[str, Any]]:
    """Existing index-points path: mode=index, zero costs, dated Nifty lot."""
    body = {
        "strategy": "dd_five_confirm",
        "params": {"mode": "long_only"},
        "symbol": NIFTY_SYMBOL,
        "timeframe": "5m",
        "start": DEV_START.isoformat(),
        "end": DEV_END.isoformat(),
        "sessions": ["normal"],
        "mode": "index",
        "live_timing": True,
    }
    cfg = parse_config(body)
    out = execute_run(cfg, lambda _e: None, hash_data=False)
    return list(out.get("result", {}).get("trades") or [])


def _run_index_future_one_unit_zero_cost() -> list[dict[str, Any]]:
    """index_future, 1 unit, zero costs, zero slip, no risk sizing (fixed 1 lot)."""
    strategy = build_strategy(
        {
            "strategy": "dd_five_confirm",
            "params": {"mode": "long_only"},
            "mode": "index",  # skip risk wrap
            "exit_rule": None,
        }
    )
    store = CandleStore(settings.candles_dir)
    source = StoreSource(store, NIFTY_SYMBOL)
    bt = BacktestConfig(
        timeframe="5m",
        start=DEV_START.isoformat(),
        end=DEV_END.isoformat(),
        session_types=("normal",),
        lot_size=1,
        cost_model=CostModel.zero(),
        slippage=Slippage.none(),
        live_timing=True,
        square_off="15:15",
    )
    result = run_backtest(strategy, source, bt)
    # Normalize to dicts like execute_run trade rows
    rows = []
    for t in result.trades:
        rows.append(
            {
                "direction": t.direction,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "lots": t.lots,
                "units": t.units,
                "gross_pnl": t.gross_pnl,
                "net_pnl": t.net_pnl,
                "charges_total": t.charges_total,
            }
        )
    return rows


def check_direction() -> dict[str, Any]:
    print("DIRECTION: running index vs index_future(1u, zero cost)…", flush=True)
    a = _run_index_points()
    b = _run_index_future_one_unit_zero_cost()
    n = min(len(a), len(b))
    mismatches = []
    for i in range(n):
        ta, tb = a[i], b[i]
        pa, pb = _points(ta), _points(tb)
        if (
            ta["entry_time"] != tb["entry_time"]
            or ta["exit_time"] != tb["exit_time"]
            or ta["direction"] != tb["direction"]
            or abs(pa - pb) > 1e-6
            or abs(float(ta["entry_price"]) - float(tb["entry_price"])) > 1e-6
            or abs(float(ta["exit_price"]) - float(tb["exit_price"])) > 1e-6
        ):
            mismatches.append(
                {
                    "i": i,
                    "index": {
                        "entry": ta["entry_time"],
                        "exit": ta["exit_time"],
                        "dir": ta["direction"],
                        "pts": pa,
                        "ep": ta["entry_price"],
                        "xp": ta["exit_price"],
                    },
                    "future1": {
                        "entry": tb["entry_time"],
                        "exit": tb["exit_time"],
                        "dir": tb["direction"],
                        "pts": pb,
                        "ep": tb["entry_price"],
                        "xp": tb["exit_price"],
                    },
                }
            )
            if len(mismatches) >= 10:
                break
    # Short falling-price unit check (engine math)
    short_ok = _short_falling_pnl_ok()
    out = {
        "index_trades": len(a),
        "index_future_1u_trades": len(b),
        "compared": n,
        "mismatches": len(mismatches),
        "mismatch_sample": mismatches[:5],
        "match": len(a) == len(b) and len(mismatches) == 0,
        "short_falling_price_positive_pnl": short_ok,
        "index_total_points": round(sum(_points(t) for t in a), 2),
        "future_total_points": round(sum(_points(t) for t in b), 2),
    }
    print(
        f"DIRECTION: index={len(a)} future1={len(b)} mismatches={len(mismatches)} "
        f"match={out['match']} short_pnl_ok={short_ok}",
        flush=True,
    )
    return out


def _short_falling_pnl_ok() -> bool:
    """A short whose price falls must show positive gross/net (zero costs)."""
    from tests.bt_helpers import Scripted, day_bars, run, sell, buy, MON

    # Enter short @ 100, cover @ 90 → +10
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (90, 91, 89, 90), (90, 90, 90, 90)]
    res = run(Scripted({0: [sell()], 2: [buy()]}), day_bars(MON, rows))
    if not res.trades:
        return False
    t = res.trades[0]
    return (
        t.direction == "SHORT"
        and t.entry_price == 100.0
        and t.exit_price == 90.0
        and t.gross_pnl > 0
        and t.net_pnl > 0
    )


def check_cost_share() -> dict[str, Any]:
    """Avg gross R and cost R per trade from Phase C JSON when present; else sample recompute."""
    path = settings.data_dir / "validation" / "phase_c_research_2026-11-21.json"
    # JSON slim may lack per-trade gross; recompute DEV window aggregates live for risk 300/600
    from scripts.phase_c_research_suite import STRATEGIES as PRESETS, _universe_symbols, _body, _summarise_run

    symbols = _universe_symbols()
    # Use a 3-symbol sample for speed + note; plus load JSON costs_as_r/avg_r if available
    sample = [s for s in symbols if s["symbol"] in DRY_SYMBOLS]
    report: dict[str, Any] = {"sample_symbols": [s["symbol"] for s in sample], "by_strategy": {}}

    json_rows = []
    if path.is_file():
        json_rows = json.loads(path.read_text(encoding="utf-8")).get("per_symbol") or []

    for risk in (300.0, 600.0):
        report["by_strategy"][str(int(risk))] = {}
        for skey, preset in PRESETS.items():
            gross_rs: list[float] = []
            cost_rs: list[float] = []
            # Prefer recompute on sample for gross (JSON may not store it)
            for sym in sample:
                body = _body(
                    label=f"cost|{skey}|{sym['symbol']}|{risk}",
                    symbol=sym["symbol"],
                    mode=sym["mode"],
                    lot_size=sym["lot_size"],
                    risk=risk,
                    start=DEV_START,
                    end=DEV_END,
                    preset=preset,
                )
                cfg = parse_config({k: v for k, v in body.items() if not k.startswith("_")})
                out = execute_run(cfg, lambda _e: None, hash_data=False)
                sizing = out.get("sizing_stats") or {}
                avg_risk = sizing.get("avg_actual_risk") or risk
                trades = list(out.get("result", {}).get("trades") or [])
                for t in trades:
                    if avg_risk and avg_risk > 0:
                        gross_rs.append(float(t["gross_pnl"]) / avg_risk)
                        cost_rs.append(float(t["charges_total"]) / avg_risk)
            # Universe avg from JSON costs_as_r / avg_r (net R, not gross)
            j_avg_r = [
                float(r["avg_r"])
                for r in json_rows
                if r.get("strategy") == skey
                and float(r.get("risk") or 0) == risk
                and r.get("window") == "DEV"
                and r.get("avg_r") is not None
            ]
            j_cost_r = [
                float(r["costs_as_r"])
                for r in json_rows
                if r.get("strategy") == skey
                and float(r.get("risk") or 0) == risk
                and r.get("window") == "DEV"
                and r.get("costs_as_r") is not None
            ]
            report["by_strategy"][str(int(risk))][skey] = {
                "sample_trades": len(gross_rs),
                "avg_gross_R": round(statistics.mean(gross_rs), 4) if gross_rs else None,
                "avg_cost_R": round(statistics.mean(cost_rs), 4) if cost_rs else None,
                "json_universe_avg_net_R_dev": round(statistics.mean(j_avg_r), 4) if j_avg_r else None,
                "json_universe_avg_cost_R_dev": round(statistics.mean(j_cost_r), 4) if j_cost_r else None,
            }
            row = report["by_strategy"][str(int(risk))][skey]
            print(
                f"COST {skey}@{int(risk)}: gross_R={row['avg_gross_R']} cost_R={row['avg_cost_R']} "
                f"(sample n={row['sample_trades']})",
                flush=True,
            )
    return report


def _render(report: dict[str, Any]) -> str:
    lines = [
        f"# Phase C sanity ({report['as_of']})",
        "",
        "## 1. Random null (gross sign-shuffle, same costs)",
        "",
        report["random"]["note"],
        "",
        "| symbol | trades | strat gross | strat net | charges | gross pctile | net pctile (correct) | net pctile (broken flip-net) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sym, row in report["random"]["symbols"].items():
        lines.append(
            f"| {sym} | {row['trades']} | {row['strategy_gross']:.0f} | {row['strategy_net']:.0f} | "
            f"{row['total_charges']:.0f} | {row['gross_percentile_vs_sign_shuffle']:.0f} | "
            f"{row['net_percentile_correct_same_costs']:.0f} | {row['net_percentile_broken_flip_net']:.0f} |"
        )
    d = report["direction"]
    lines += [
        "",
        "## 2. Direction (NIFTY dd_five_long index vs index_future 1u zero-cost)",
        "",
        f"- index trades: {d['index_trades']}",
        f"- index_future 1u trades: {d['index_future_1u_trades']}",
        f"- mismatches: {d['mismatches']}",
        f"- **match: {d['match']}**",
        f"- short falling-price → positive PnL: {d['short_falling_price_positive_pnl']}",
        f"- total points index / future: {d['index_total_points']} / {d['future_total_points']}",
        "",
        "## 3. Cost share (sample HDFCBANK/RELIANCE/BANKNIFTY, DEV)",
        "",
    ]
    for risk, block in report["cost_share"]["by_strategy"].items():
        lines += [f"### Risk ₹{risk}", "", "| strategy | avg gross R | avg cost R | JSON univ net R | JSON univ cost R |", "|---|---:|---:|---:|---:|"]
        for skey in STRATEGIES:
            row = block.get(skey) or {}
            lines.append(
                f"| {skey} | {row.get('avg_gross_R')} | {row.get('avg_cost_R')} | "
                f"{row.get('json_universe_avg_net_R_dev')} | {row.get('json_universe_avg_cost_R_dev')} |"
            )
        lines.append("")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    report = {
        "as_of": date.today().isoformat(),
        "random": check_random_gross(),
        "direction": check_direction(),
        "cost_share": check_cost_share(),
    }
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"phase_c_sanity_{report['as_of']}.md"
    js = out_dir / f"phase_c_sanity_{report['as_of']}.json"
    md.write_text(_render(report), encoding="utf-8")
    js.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(_render(report), flush=True)
    print(f"wrote {md}", flush=True)
    return 0 if report["direction"]["match"] and report["direction"]["short_falling_price_positive_pnl"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
