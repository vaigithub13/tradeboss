"""SENSEX options research (no paper slot; Nifty slots untouched).

    cd backend && uv run python -m scripts.sensex_research_suite
    cd backend && uv run python -m scripts.sensex_research_suite --workers 4

Premiums: modelled only (no SENSEX_*.parquet). India VIX as IV proxy. Lot 20. Friday weeklies.
strike_premium: Nifty ₹250 × (Sensex/Nifty spot ≈ 3.28) → ₹820, rounded to **800**.
Slippage: 0.2 × (800/250) → **0.65** pts.

Windows: development 2022-01-03..2024-12-31; OOS 2025-01-01..2026-06-30 (holdout excluded);
forward 2026-10-02..2026-11-20 (informational). Writes data/validation/sensex_research_<date>.md.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_run
from app.backtest.holdout import load_holdout
from app.config import settings

IST = timezone(timedelta(hours=5, minutes=30))
DEV_START = date(2022, 1, 3)
DEV_END = date(2024, 12, 31)
OOS_START = date(2025, 1, 1)
# Holdout is 2026-07-01..2026-10-01 — research OOS stops the day before.
HOLDOUT_START, HOLDOUT_END = load_holdout()
OOS_END = HOLDOUT_START - timedelta(days=1)  # 2026-06-30
FORWARD_START = HOLDOUT_END + timedelta(days=1)  # 2026-10-02 (after holdout end)
FORWARD_END = date(2026, 11, 20)

SENSEX_STRIKE_PREMIUM = 800.0  # Nifty 250 × ~3.28 spot ratio
SENSEX_SLIPPAGE = 0.65  # 0.2 × (800/250)

# S5-QM-long Nifty baseline (PROGRESS / s5_edge_diagnosis)
S5_NIFTY = {
    "research_net": 159_724,
    "research_pf": 1.47,
    "real_prem_net": 86_438,
    "real_prem_pf": 1.57,
}


def _run_body(
    *,
    strategy: str,
    params: dict[str, Any],
    timeframe: str,
    start: date,
    end: date,
    strike_offset: int = 0,
    strike_premium: float | None = None,
    exit_rule: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "strategy": strategy,
        "params": params,
        "symbol": "SENSEX",
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": ["normal"],
        "mode": "options",
        "strike_offset": strike_offset,
        "slippage_points": SENSEX_SLIPPAGE,
        "live_timing": True,
        "option_fill": "delta_adjusted",
    }
    if strike_premium is not None:
        body["strike_premium"] = strike_premium
    if exit_rule is not None:
        body["exit_rule"] = exit_rule
    return execute_run(parse_config(body), lambda _e: None, hash_data=False)


def _worker(spec: dict[str, Any]) -> dict[str, Any]:
    out = _run_body(
        strategy=spec["strategy"],
        params=spec["params"],
        timeframe=spec["timeframe"],
        start=date.fromisoformat(spec["start"]),
        end=date.fromisoformat(spec["end"]),
        strike_offset=int(spec.get("strike_offset", 0)),
        strike_premium=spec.get("strike_premium"),
        exit_rule=spec.get("exit_rule"),
    )
    # Drop the full trade payload — 80+ runs would otherwise keep GBs in the parent.
    return {"spec": spec, "summary": _summarise(spec["label"], out)}


def _opt_nets(out: dict[str, Any]) -> list[tuple[datetime, float, dict[str, Any]]]:
    rows = []
    for t in out["result"]["trades"]:
        opt = t.get("option") or {}
        if opt.get("net_pnl") is None:
            continue
        when = datetime.fromtimestamp(int(t["entry_time"]), IST)
        rows.append((when, float(opt["net_pnl"]), t))
    return rows


def _leave_top(nets: list[float], n: int) -> float:
    if len(nets) <= n:
        return 0.0
    return sum(sorted(nets, reverse=True)[n:])


def _pf(nets: list[float]) -> float | None:
    wins = sum(v for v in nets if v > 0)
    losses = abs(sum(v for v in nets if v < 0))
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return wins / losses


def _hold_minutes(t: dict[str, Any]) -> float:
    return max(0.0, (int(t["exit_time"]) - int(t["entry_time"])) / 60.0)


def _summarise(label: str, out: dict[str, Any]) -> dict[str, Any]:
    pairs = _opt_nets(out)
    nets = [v for _, v, _ in pairs]
    trades = [t for _, _, t in pairs]
    by_year: dict[int, float] = defaultdict(float)
    holds = [_hold_minutes(t) for t in trades]
    for when, net, _t in pairs:
        by_year[when.year] += net
    opt = ((out.get("result") or {}).get("summary") or {}).get("option") or {}
    real_fills = int(opt.get("real_fills") or 0)
    modelled_fills = int(opt.get("modelled_fills") or 0)
    warnings = out.get("warnings") or []
    return {
        "label": label,
        "trades": len(nets),
        "wins": sum(1 for v in nets if v > 0),
        "win_pct": (100.0 * sum(1 for v in nets if v > 0) / len(nets)) if nets else 0.0,
        "net": sum(nets),
        "pf": _pf(nets),
        "max_dd": opt.get("max_drawdown"),
        "avg_hold_m": (sum(holds) / len(holds)) if holds else 0.0,
        "by_year": dict(sorted(by_year.items())),
        "without_top10": _leave_top(nets, 10),
        "real_fills": real_fills,
        "modelled_fills": modelled_fills,
        "warnings": [w for w in warnings if "SENSEX" in w or "premium" in w.lower()][:4],
        "modelled_note": "modelled" if real_fills == 0 else ("mixed" if modelled_fills else "real"),
    }


def _gates(dev: dict[str, Any], oos: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    if (dev.get("net") or 0) <= 0 or (oos.get("net") or 0) <= 0:
        fails.append("not positive in both windows")
    oos_pf = oos.get("pf")
    if oos_pf is None or oos_pf < 1.25:
        fails.append(f"OOS PF {oos_pf} < 1.25")
    if (oos.get("without_top10") or 0) <= 0:
        fails.append("fails top-10 removal on OOS")
    if (oos.get("trades") or 0) < 100:
        fails.append(f"OOS trades {oos.get('trades')} < 100")
    return fails


def _specs() -> list[dict[str, Any]]:
    windows = [
        ("dev", DEV_START, DEV_END),
        ("oos", OOS_START, OOS_END),
        ("fwd", FORWARD_START, FORWARD_END),
    ]
    specs: list[dict[str, Any]] = []

    def add(label: str, window: str, start: date, end: date, **kw: Any) -> None:
        specs.append({
            "label": f"{label}|{window}",
            "window": window,
            "start": start.isoformat(),
            "end": end.isoformat(),
            **kw,
        })

    slot_runs = [
        ("PC20", "price_channel", {"length": 20}, "5m", None),
        ("PC20_1to2", "price_channel", {"length": 20}, "5m", "premium_1to2"),
        (
            "S5_long",
            "dd_five_confirm",
            {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
            "3m",
            "premium_trail_10",
        ),
        (
            "S6_short",
            "dd_five_confirm",
            {"mode": "short_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
            "3m",
            "premium_trail_10",
        ),
    ]
    research_runs = [
        ("EMP", "ema_pivot_pullback", {}, "5m"),
        ("MS15_raw", "price_action", {"pattern": "morning_star", "variant": "pattern", "confluence": False, "rr": 2.0}, "15m"),
        ("ES15_raw", "price_action", {"pattern": "evening_star", "variant": "pattern", "confluence": False, "rr": 2.0}, "15m"),
        ("MS5_conf", "price_action", {"pattern": "morning_star", "variant": "pattern", "confluence": True, "rr": 2.0}, "5m"),
        ("ES5_conf", "price_action", {"pattern": "evening_star", "variant": "pattern", "confluence": True, "rr": 2.0}, "5m"),
        ("GT9", "gold_trend_9", {}, "5m"),
    ]

    for wname, start, end in windows:
        for lab, strat, params, tf, exit_rule in slot_runs:
            add(
                lab,
                wname,
                start,
                end,
                strategy=strat,
                params=params,
                timeframe=tf,
                strike_premium=SENSEX_STRIKE_PREMIUM,
                exit_rule=exit_rule,
            )
        for lab, strat, params, tf in research_runs:
            for strike_lab, offset, prem in (("ATM", 0, None), ("P800", 0, SENSEX_STRIKE_PREMIUM)):
                for exit_lab, exit_rule in (("orig", None), ("trail10", "premium_trail_10")):
                    add(
                        f"{lab}_{strike_lab}_{exit_lab}",
                        wname,
                        start,
                        end,
                        strategy=strat,
                        params=params,
                        timeframe=tf,
                        strike_offset=offset,
                        strike_premium=prem,
                        exit_rule=exit_rule,
                    )
    return specs


def _fmt_pf(pf: float | None) -> str:
    if pf is None:
        return "—"
    if pf == float("inf"):
        return "∞"
    return f"{pf:.2f}"


def _sensitivity(base_spec: dict[str, Any], workers: int) -> list[dict[str, Any]]:
    """±20% strike_premium and trail when present."""
    out: list[dict[str, Any]] = []
    base_prem = base_spec.get("strike_premium") or SENSEX_STRIKE_PREMIUM
    variants = [
        ("prem_-20%", {**base_spec, "strike_premium": round(base_prem * 0.8, 1), "label": base_spec["label"] + "|sens_p-20"}),
        ("prem_+20%", {**base_spec, "strike_premium": round(base_prem * 1.2, 1), "label": base_spec["label"] + "|sens_p+20"}),
    ]
    if base_spec.get("exit_rule") == "premium_trail_10":
        # trail kind is fixed in catalog aliases; approximate via exit_rule string only — skip trail % sweep
        pass
    for name, spec in variants:
        try:
            r = _worker({**spec, "window": "oos"})
            out.append({"name": name, "summary": r["summary"]})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": name, "error": str(exc)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0, help="run only the first N specs (debug)")
    args = ap.parse_args()
    specs = _specs()
    if args.limit > 0:
        specs = specs[: args.limit]

    print(f"SENSEX research: {len(specs)} runs, strike_premium={SENSEX_STRIKE_PREMIUM}, slip={SENSEX_SLIPPAGE}")
    print(f"dev {DEV_START}..{DEV_END}; oos {OOS_START}..{OOS_END}; fwd {FORWARD_START}..{FORWARD_END}")
    print(f"holdout excluded: {HOLDOUT_START}..{HOLDOUT_END}")

    results: list[dict[str, Any]] = []
    if args.workers <= 1:
        for i, spec in enumerate(specs, 1):
            print(f"[{i}/{len(specs)}] {spec['label']}", flush=True)
            results.append(_worker(spec))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(_worker, s): s for s in specs}
            done = 0
            for fut in as_completed(futs):
                done += 1
                spec = futs[fut]
                try:
                    results.append(fut.result())
                    print(f"[{done}/{len(specs)}] {spec['label']} ok", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[{done}/{len(specs)}] {spec['label']} FAIL {exc}", flush=True)
                    results.append({"spec": spec, "summary": {"label": spec["label"], "error": str(exc)}})

    by_base: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in results:
        lab = r["spec"]["label"]
        base, _, window = lab.rpartition("|")
        by_base[base][window] = r["summary"]

    passers: list[str] = []
    sens_notes: list[str] = []
    lines: list[str] = []
    lines.append("# SENSEX options research")
    lines.append("")
    lines.append(f"Generated {datetime.now(IST).isoformat()}")
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append(f"- Symbol **SENSEX** 1m→TF; BSE weekly **Friday** expiry; lot **20**.")
    lines.append(f"- `strike_premium` **{SENSEX_STRIKE_PREMIUM:.0f}** (Nifty ₹250 × ~3.28 spot ratio).")
    lines.append(f"- Slippage **{SENSEX_SLIPPAGE}** pts (0.2 × 800/250); live timing; delta-adjusted fills.")
    lines.append("- **Premiums: modelled only** (no `SENSEX_*.parquet`); India VIX as IV proxy; Nifty BS calibration.")
    lines.append("- Costs: Upstox table (exchange % NSE-derived — unverified for BSE).")
    lines.append(f"- Dev `{DEV_START}`..`{DEV_END}`; OOS `{OOS_START}`..`{OOS_END}` (holdout `{HOLDOUT_START}`..`{HOLDOUT_END}` excluded);")
    lines.append(f"  forward `{FORWARD_START}`..`{FORWARD_END}`.")
    lines.append(
        "- Note: on modelled Sensex PC20, `premium_1to2` never fires before channel reversals "
        "(identical to bare PC20); `premium_trail_10` does fire."
    )
    lines.append("")
    lines.append("## S5-QM-long Nifty baseline (for comparison)")
    lines.append("")
    lines.append(
        f"| research net | research PF | real-prem net | real-prem PF |\n|---:|---:|---:|---:|\n"
        f"| {S5_NIFTY['research_net']:,} | {S5_NIFTY['research_pf']} | {S5_NIFTY['real_prem_net']:,} | {S5_NIFTY['real_prem_pf']} |"
    )
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append(
        "| run | win | trades | win% | net ₹ | PF | max DD | avg hold m | −top10 | pass? |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")

    for base in sorted(by_base):
        windows = by_base[base]
        for w in ("dev", "oos", "fwd"):
            s = windows.get(w)
            if not s or s.get("error"):
                err = (s or {}).get("error", "missing")
                lines.append(f"| {base} | {w} | — | — | — | — | — | — | — | {err} |")
                continue
            lines.append(
                f"| {base} | {w} | {s['trades']} | {s['win_pct']:.1f} | {s['net']:,.0f} | {_fmt_pf(s.get('pf'))} | "
                f"{s.get('max_dd') if s.get('max_dd') is not None else '—'} | {s['avg_hold_m']:.0f} | "
                f"{s['without_top10']:,.0f} |  |"
            )
            if s.get("by_year"):
                yr = ", ".join(f"{y}:{v:,.0f}" for y, v in s["by_year"].items())
                lines.append(f"|  |  | years | {yr} |  |  |  |  |  |  |")

        if "dev" in windows and "oos" in windows and not windows["dev"].get("error") and not windows["oos"].get("error"):
            fails = _gates(windows["dev"], windows["oos"])
            # ±20% strike_premium for every run with OOS PF > 1.2 (cliff only gates when otherwise passing)
            oos_pf = windows["oos"].get("pf") or 0
            cliff = False
            if oos_pf > 1.2:
                oos_spec = next(r["spec"] for r in results if r["spec"]["label"] == f"{base}|oos")
                oos_net = windows["oos"].get("net") or 0
                for sn in _sensitivity(oos_spec, args.workers):
                    summ = sn.get("summary") or {}
                    net = summ.get("net")
                    if oos_net > 0 and net is not None and net < 0.5 * oos_net:
                        cliff = True
                    sens_notes.append(
                        f"- {base} {sn.get('name')}: net={summ.get('net')} PF={_fmt_pf(summ.get('pf'))} "
                        f"trades={summ.get('trades')}"
                        + (f" ERR {sn['error']}" if "error" in sn else "")
                    )
            if cliff:
                fails.append("sensitivity cliff (±20% strike_premium)")
            ok = not fails
            if ok:
                passers.append(base)
            gate_txt = "PASS" if ok else "; ".join(
                f"OOS PF {_fmt_pf(oos_pf)} < 1.25" if f.startswith("OOS PF") else f for f in fails
            )
            lines.append(f"| {base} | gates |  |  |  |  |  |  |  | {gate_txt} |")

    lines.append("")
    lines.append("## Passers")
    lines.append("")
    if passers:
        for p in passers:
            lines.append(f"- **{p}**")
    else:
        lines.append("- None passed all gates.")
    lines.append("")
    if sens_notes:
        lines.append("## Sensitivity (±20% strike_premium on OOS when PF>1.2)")
        lines.append("")
        lines.extend(sens_notes)
        lines.append("")

    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"sensex_research_{datetime.now(IST).date().isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    print("Passers:", passers or "(none)")


if __name__ == "__main__":
    main()
