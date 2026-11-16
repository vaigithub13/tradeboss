"""Price Action Phase 1 — options backtests (patterns ± confluence, FHB box). No paper slot.

    cd backend && uv run python -m scripts.price_action_backtest

Writes data/validation/price_action_<date>.md (under data/ — not committed).
Holdout is run only for up to 2 flagged candidates at the end.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_holdout, execute_run
from app.backtest.holdout import load_holdout
from app.config import settings
from app.options.model import HISTORY_START
from app.strategies.price_action_patterns import PATTERN_KINDS

IST = timezone(timedelta(hours=5, minutes=30))
RESEARCH_START = date(2022, 1, 3)

SLOT5 = {
    "strategy": "dd_five_confirm",
    "params": {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
    "timeframe": "3m",
    "strike_premium": 250.0,
    "exit_rule": "premium_trail_10",
}


def _run(
    start: date,
    end: date,
    *,
    strategy: str,
    params: dict[str, Any],
    timeframe: str,
    strike_offset: int = 0,
    strike_premium: float | None = None,
    exit_rule: str | None = None,
    holdout: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "strategy": strategy,
        "params": params,
        "symbol": "NIFTY50",
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": ["normal"],
        "mode": "options",
        "strike_offset": strike_offset,
        "slippage_points": 0.2,
        "live_timing": True,
        "option_fill": "delta_adjusted",
    }
    if strike_premium is not None:
        body["strike_premium"] = strike_premium
    if exit_rule is not None:
        body["exit_rule"] = exit_rule
    if holdout:
        body["kind"] = "holdout"
        return execute_holdout(parse_config(body), lambda _e: None)
    return execute_run(parse_config(body), lambda _e: None, hash_data=False)


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


def _avg_wl(nets: list[float]) -> tuple[float | None, float | None]:
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v < 0]
    return (
        (sum(wins) / len(wins)) if wins else None,
        (sum(losses) / len(losses)) if losses else None,
    )


def _summarise(label: str, out: dict[str, Any]) -> dict[str, Any]:
    pairs = _opt_nets(out)
    nets = [v for _, v, _ in pairs]
    trades = [t for _, _, t in pairs]
    by_year: dict[int, float] = defaultdict(float)
    by_day: dict[str, int] = defaultdict(int)
    reasons: Counter[str] = Counter()
    for when, net, t in pairs:
        by_year[when.year] += net
        by_day[when.date().isoformat()] += 1
        reasons[str(t.get("exit_reason") or "?")] += 1
    wins = [v for v in nets if v > 0]
    opt = (out["result"]["summary"].get("option") or {})
    tpd = (sum(by_day.values()) / len(by_day)) if by_day else 0.0
    avg_w, avg_l = _avg_wl(nets)
    return {
        "label": label,
        "trades": len(nets),
        "win_pct": 100.0 * len(wins) / len(nets) if nets else 0.0,
        "net": sum(nets),
        "max_dd": opt.get("max_drawdown"),
        "without_top10": _leave_top(nets, 10),
        "pf": _pf(nets),
        "avg_win": avg_w,
        "avg_loss": avg_l,
        "trades_per_day": tpd,
        "years": dict(sorted(by_year.items())),
        "exit_reasons": dict(reasons),
        "real_fills": opt.get("real_fills"),
        "modelled_fills": opt.get("modelled_fills"),
    }


def _f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        if x == float("inf"):
            return "∞"
        return f"{x:,.{nd}f}"
    return str(x)


def _row_md(r: dict[str, Any]) -> str:
    return (
        f"| {r['label']} | {r['trades']} | {_f(r['win_pct'], 1)} | {_f(r['avg_win'])} | {_f(r['avg_loss'])} | "
        f"{_f(r['net'])} | {_f(r['pf'], 2)} | {_f(r['max_dd'])} | {_f(r['without_top10'])} | "
        f"{_f(r['trades_per_day'], 2)} |"
    )


def _job(spec: dict[str, Any]) -> dict[str, Any]:
    """Worker entry — must be top-level for ProcessPoolExecutor."""
    out = _run(
        date.fromisoformat(spec["start"]),
        date.fromisoformat(spec["end"]),
        strategy=spec["strategy"],
        params=spec["params"],
        timeframe=spec["timeframe"],
        strike_offset=spec.get("strike_offset", 0),
        strike_premium=spec.get("strike_premium"),
        exit_rule=spec.get("exit_rule"),
    )
    return {"spec": spec, "summary": _summarise(spec["label"], out), "raw": out}


def _specs(research_end: date) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    windows = [
        ("research", RESEARCH_START, research_end),
        ("real-prem", HISTORY_START, research_end),
    ]
    for wname, start, end in windows:
        for pattern in PATTERN_KINDS:
            for tf in ("5m", "15m"):
                for conf in (False, True):
                    label = f"{pattern}|{tf}|{'conf' if conf else 'raw'}|{wname}"
                    specs.append({
                        "label": label,
                        "window": wname,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "strategy": "price_action",
                        "timeframe": tf,
                        "params": {
                            "pattern": pattern,
                            "variant": "pattern",
                            "confluence": conf,
                            "rr": 2.0,
                            "mode": "long_short",
                        },
                    })
        for variant, tf in (("box_break", "5m"), ("box_fail", "5m")):
            label = f"{variant}|{tf}|raw|{wname}"
            specs.append({
                "label": label,
                "window": wname,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy": "price_action",
                "timeframe": tf,
                "params": {
                    "pattern": "tweezer_bottom",  # unused for box variants
                    "variant": variant,
                    "confluence": False,
                    "rr": 2.0,
                    "mode": "long_short",
                },
            })
        specs.append({
            "label": f"S5-QM-long|{wname}",
            "window": wname,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "strategy": SLOT5["strategy"],
            "timeframe": SLOT5["timeframe"],
            "params": SLOT5["params"],
            "strike_premium": SLOT5["strike_premium"],
            "exit_rule": SLOT5["exit_rule"],
        })
    return specs


def _passes(research: dict[str, Any], real: dict[str, Any]) -> bool:
    """Flag candidate: + both windows, PF≥1.25 real-prem, survives top-10, ≥100 trades, no cliff later."""
    if research["net"] <= 0 or real["net"] <= 0:
        return False
    pf = real["pf"]
    if pf is None or pf < 1.25:
        return False
    if real["without_top10"] <= 0:
        return False
    if real["trades"] < 100 and research["trades"] < 100:
        return False
    # prefer real-prem ≥100; allow research≥100 if real-prem a bit short but still ≥80
    if real["trades"] < 80:
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--smoke", action="store_true", help="one pattern + S5 only, research window")
    args = ap.parse_args(argv)

    holdout = load_holdout()
    research_end = holdout[0] - timedelta(days=1)
    lines: list[str] = [
        "# Price Action Phase 1 — options backtest",
        "",
        "ATM CE/PE, live timing, slippage 0.2, delta-adjusted fills, SL=invalidation, target 2R, "
        "square-off 15:15. Slot 5 unchanged (comparison only). Holdout 2026-07-01..2026-10-01 excluded "
        "from research.",
        "",
        f"Research {RESEARCH_START} .. {research_end}; real-premium {HISTORY_START} .. {research_end}.",
        "",
    ]

    if args.smoke:
        specs = [
            {
                "label": "tweezer_bottom|5m|raw|research",
                "window": "research",
                "start": RESEARCH_START.isoformat(),
                "end": research_end.isoformat(),
                "strategy": "price_action",
                "timeframe": "5m",
                "params": {"pattern": "tweezer_bottom", "variant": "pattern", "confluence": False, "rr": 2.0},
            },
            {
                "label": "S5-QM-long|research",
                "window": "research",
                "start": RESEARCH_START.isoformat(),
                "end": research_end.isoformat(),
                "strategy": SLOT5["strategy"],
                "timeframe": SLOT5["timeframe"],
                "params": SLOT5["params"],
                "strike_premium": SLOT5["strike_premium"],
                "exit_rule": SLOT5["exit_rule"],
            },
        ]
    else:
        specs = _specs(research_end)

    print(f"jobs={len(specs)} workers={args.workers}", flush=True)
    results: list[dict[str, Any]] = []
    if args.workers <= 1:
        for i, spec in enumerate(specs, 1):
            print(f"[{i}/{len(specs)}] {spec['label']} ...", flush=True)
            results.append(_job(spec))
            s = results[-1]["summary"]
            print(f"  n={s['trades']} net={s['net']:.0f} pf={s['pf']}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(_job, spec): spec for spec in specs}
            done = 0
            for fut in as_completed(futs):
                done += 1
                row = fut.result()
                results.append(row)
                s = row["summary"]
                print(f"[{done}/{len(specs)}] {s['label']} n={s['trades']} net={s['net']:.0f} pf={s['pf']}", flush=True)

    by_window: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_window[row["spec"]["window"]].append(row["summary"])

    head = (
        "| run | trades | win % | avg ₹ win | avg ₹ loss | net ₹ | PF | max DD ₹ | −top10 ₹ | trades/day |"
    )
    sep = "|" + "---|" * 10

    for wname in ("research", "real-prem"):
        rows = by_window.get(wname, [])
        if not rows:
            continue
        lines += [f"## {wname}", "", head, sep]
        for r in sorted(rows, key=lambda x: x["label"]):
            lines.append(_row_md(r))
        lines.append("")
        # year-by-year for each non-S5 with trades
        for r in sorted(rows, key=lambda x: x["label"]):
            if r["label"].startswith("S5") or r["trades"] == 0:
                continue
            lines += [f"### Year-by-year — {r['label']}", "", "| year | net ₹ |", "|---|---|"]
            for y, v in r["years"].items():
                lines.append(f"| {y} | {_f(v)} |")
            lines.append("")

    # Pair research + real-prem for candidate flagging
    research_map = {r["label"].rsplit("|", 1)[0]: r for r in by_window.get("research", []) if "|research" in r["label"] or True}
    # labels are pattern|tf|conf|window — strip window
    def base_key(label: str) -> str:
        parts = label.split("|")
        return "|".join(parts[:-1]) if len(parts) > 1 else label

    res_by = {base_key(r["label"]): r for r in by_window.get("research", [])}
    real_by = {base_key(r["label"]): r for r in by_window.get("real-prem", [])}

    candidates: list[str] = []
    lines += ["## Candidate flags", ""]
    for key in sorted(set(res_by) & set(real_by)):
        if key.startswith("S5"):
            continue
        ok = _passes(res_by[key], real_by[key])
        mark = "PASS" if ok else "no"
        lines.append(
            f"- **{key}**: {mark} — research net {_f(res_by[key]['net'])} (n={res_by[key]['trades']}, "
            f"PF {_f(res_by[key]['pf'], 2)}); real-prem net {_f(real_by[key]['net'])} "
            f"(n={real_by[key]['trades']}, PF {_f(real_by[key]['pf'], 2)}, −top10 {_f(real_by[key]['without_top10'])})"
        )
        if ok:
            candidates.append(key)
    lines.append("")

    # Sensitivity for anything with real-prem PF > 1.2
    sens_targets = [
        k for k, r in real_by.items()
        if not k.startswith("S5") and r["pf"] is not None and r["pf"] > 1.2
    ]
    lines += ["## Sensitivity ±20% (real-prem PF > 1.2)", ""]
    cliff_fail: set[str] = set()
    for key in sens_targets[:8]:  # cap work
        parts = key.split("|")
        if len(parts) < 3:
            continue
        pattern, tf, conf_s = parts[0], parts[1], parts[2]
        if pattern in ("box_break", "box_fail"):
            variant = pattern
            pattern_param = "tweezer_bottom"
            base_params = {"pattern": pattern_param, "variant": variant, "confluence": False, "rr": 2.0}
            axes = [
                ("rr", [1.6, 2.0, 2.4]),
                ("max_width", [120.0, 150.0, 180.0]),
                ("level_pts", [8.0, 10.0, 12.0]),
            ]
        else:
            base_params = {
                "pattern": pattern,
                "variant": "pattern",
                "confluence": conf_s == "conf",
                "rr": 2.0,
            }
            axes = [
                ("rr", [1.6, 2.0, 2.4]),
                ("confirm_bars", [2, 3, 4]),
                ("level_pts", [8.0, 10.0, 12.0]),
            ]
        lines += [f"### {key}", "", "| param | value | trades | net ₹ | PF |", "|---|---|---|---|---|"]
        base_net = real_by[key]["net"]
        axis_moves: list[float] = []
        for pname, values in axes:
            for val in values:
                params = {**base_params, pname: val}
                print(f"sens {key} {pname}={val} ...", flush=True)
                out = _run(HISTORY_START, research_end, strategy="price_action", params=params, timeframe=tf)
                s = _summarise(f"{pname}={val}", out)
                lines.append(f"| {pname} | {val} | {s['trades']} | {_f(s['net'])} | {_f(s['pf'], 2)} |")
                if val != values[1] and base_net and abs(base_net) > 0:
                    axis_moves.append(abs((s["net"] - base_net) / abs(base_net) * 100))
        if axis_moves and max(axis_moves) > 40:
            lines.append("")
            lines.append("**Cliff:** ±20% moved net >40% on an axis neighbour.")
            cliff_fail.add(key)
        else:
            lines.append("")
            lines.append("**Plateau:** no cliff on ±20% axis neighbours.")
        lines.append("")

    candidates = [c for c in candidates if c not in cliff_fail]
    lines += ["## Candidates after sensitivity", ""]
    if not candidates:
        lines.append("None.")
    else:
        for c in candidates:
            lines.append(f"- {c}")
    lines.append("")

    # Holdout for top 2 by real-prem net among candidates
    top = sorted(candidates, key=lambda k: real_by[k]["net"], reverse=True)[:2]
    lines += ["## Holdout (2026-07-01 .. 2026-10-01) — top candidates only", ""]
    if not top:
        lines.append("Skipped — no candidates passed the gates.")
    for key in top:
        parts = key.split("|")
        pattern, tf, conf_s = parts[0], parts[1], parts[2]
        if pattern in ("box_break", "box_fail"):
            params = {"pattern": "tweezer_bottom", "variant": pattern, "confluence": False, "rr": 2.0}
        else:
            params = {
                "pattern": pattern,
                "variant": "pattern",
                "confluence": conf_s == "conf",
                "rr": 2.0,
            }
        print(f"HOLDOUT {key} ...", flush=True)
        out = _run(
            holdout[0], holdout[1],
            strategy="price_action", params=params, timeframe=tf, holdout=True,
        )
        s = _summarise(f"{key}|holdout", out)
        lines.append(
            f"- **{key}**: trades {s['trades']}, win {_f(s['win_pct'], 1)}%, net {_f(s['net'])}, "
            f"PF {_f(s['pf'], 2)}, max DD {_f(s['max_dd'])}, −top10 {_f(s['without_top10'])}"
        )
    lines.append("")

    # vs S5
    lines += ["## vs slot 5 (S5-QM-long)", ""]
    for wname in ("research", "real-prem"):
        s5 = next((r for r in by_window.get(wname, []) if r["label"].startswith("S5")), None)
        if s5:
            lines.append(
                f"- **{wname}:** S5-QM-long n={s5['trades']} net {_f(s5['net'])} "
                f"PF {_f(s5['pf'], 2)} DD {_f(s5['max_dd'])}"
            )
    lines.append("")

    text = "\n".join(lines)
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"price_action_{date.today().isoformat()}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
