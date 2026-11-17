"""GOLD TREND GT9 / GT2 / GT14 — Nifty options research (no paper slot).

    cd backend && uv run python -m scripts.gold_trend_backtest
    cd backend && uv run python -m scripts.gold_trend_backtest --workers 4

Per strategy: (a) ATM original exits, (b) strike_premium 250 original exits,
(c) strike_premium 250 + premium_trail_10; long/short splits for (b).
Compare S5-QM-long. Sensitivity ±20% when real-prem PF > 1.2.
Holdout only for ≤2 passers. Writes data/validation/gold_trend_<date>.md (not committed).
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

IST = timezone(timedelta(hours=5, minutes=30))
RESEARCH_START = date(2022, 1, 3)
TF = "5m"

SLOT5 = {
    "strategy": "dd_five_confirm",
    "params": {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
    "timeframe": "3m",
    "strike_premium": 250.0,
    "exit_rule": "premium_trail_10",
}

STRATS = (
    ("GT9", "gold_trend_9", {}),
    ("GT2", "gold_trend_2", {}),
    ("GT14", "gold_trend_14", {}),
)


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


def _hold_minutes(t: dict[str, Any]) -> float:
    return max(0.0, (int(t["exit_time"]) - int(t["entry_time"])) / 60.0)


def _summarise(label: str, out: dict[str, Any]) -> dict[str, Any]:
    pairs = _opt_nets(out)
    nets = [v for _, v, _ in pairs]
    trades = [t for _, _, t in pairs]
    by_year: dict[int, float] = defaultdict(float)
    by_side: dict[str, list[float]] = defaultdict(list)
    by_day: dict[str, int] = defaultdict(int)
    reasons: Counter[str] = Counter()
    holds = [_hold_minutes(t) for t in trades]
    for when, net, t in pairs:
        by_year[when.year] += net
        by_side[t["direction"]].append(net)
        by_day[when.date().isoformat()] += 1
        reasons[str(t.get("exit_reason") or "?")] += 1
    wins = [v for v in nets if v > 0]
    opt = (out["result"]["summary"].get("option") or {})
    tpd = (sum(by_day.values()) / len(by_day)) if by_day else 0.0
    return {
        "label": label,
        "trades": len(nets),
        "win_pct": 100.0 * len(wins) / len(nets) if nets else 0.0,
        "net": sum(nets),
        "max_dd": opt.get("max_drawdown"),
        "without_top10": _leave_top(nets, 10),
        "pf": _pf(nets),
        "avg_hold_min": sum(holds) / len(holds) if holds else 0.0,
        "trades_per_day": tpd,
        "long_n": len(by_side["LONG"]),
        "long_net": sum(by_side["LONG"]),
        "short_n": len(by_side["SHORT"]),
        "short_net": sum(by_side["SHORT"]),
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
        f"| {r['label']} | {r['trades']} | {_f(r['win_pct'], 1)} | {_f(r['net'])} | {_f(r['pf'], 2)} | "
        f"{_f(r['max_dd'])} | {_f(r['without_top10'])} | {_f(r['avg_hold_min'], 1)} | "
        f"{r['long_n']} / {_f(r['long_net'])} | {r['short_n']} / {_f(r['short_net'])} |"
    )


def _job(spec: dict[str, Any]) -> dict[str, Any]:
    out = _run(
        date.fromisoformat(spec["start"]),
        date.fromisoformat(spec["end"]),
        strategy=spec["strategy"],
        params=spec["params"],
        timeframe=spec["timeframe"],
        strike_offset=spec.get("strike_offset", 0),
        strike_premium=spec.get("strike_premium"),
        exit_rule=spec.get("exit_rule"),
        holdout=bool(spec.get("holdout")),
    )
    return {"spec": spec, "summary": _summarise(spec["label"], out)}


def _main_specs(research_end: date) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    windows = [
        ("research", RESEARCH_START, research_end),
        ("real-prem", HISTORY_START, research_end),
    ]
    for wname, start, end in windows:
        for code, strategy, base in STRATS:
            variants = [
                (f"{code}-ATM-orig|{wname}", {**base}, 0, None, None),
                (f"{code}-QM250-orig|{wname}", {**base}, 0, 250.0, None),
                (f"{code}-QM250-trail10|{wname}", {**base}, 0, 250.0, "premium_trail_10"),
                (f"{code}-QM250-orig-long|{wname}", {**base, "mode": "long_only"}, 0, 250.0, None),
                (f"{code}-QM250-orig-short|{wname}", {**base, "mode": "short_only"}, 0, 250.0, None),
            ]
            for label, params, offset, prem, exit_rule in variants:
                specs.append({
                    "label": label,
                    "window": wname,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "strategy": strategy,
                    "timeframe": TF,
                    "params": params,
                    "strike_offset": offset,
                    "strike_premium": prem,
                    "exit_rule": exit_rule,
                    "family": code,
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
            "family": "S5",
        })
    return specs


def _passes(research: dict[str, Any], real: dict[str, Any]) -> bool:
    if research["net"] <= 0 or real["net"] <= 0:
        return False
    pf = real["pf"]
    if pf is None or pf < 1.25:
        return False
    if real["without_top10"] <= 0:
        return False
    if real["trades"] < 100:
        return False
    return True


def _sensitivity_specs(code: str, strategy: str, research_end: date) -> list[dict[str, Any]]:
    """±20% stop multiple and main lengths; strike_premium 250, original exits."""
    start, end = HISTORY_START, research_end
    out: list[dict[str, Any]] = []

    def add(label: str, params: dict[str, Any]) -> None:
        out.append({
            "label": f"{code}-sens-{label}|real-prem",
            "window": "real-prem",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "strategy": strategy,
            "timeframe": TF,
            "params": params,
            "strike_premium": 250.0,
            "family": code,
            "kind": "sens",
        })

    if code == "GT9":
        for stop in (1.2, 1.5, 1.8):
            for ten, kij in ((7, 21), (9, 26), (11, 31)):
                add(f"stop{stop}-t{ten}/k{kij}", {"stop_mult": stop, "tenkan": ten, "kijun": kij, "senkou": max(52, kij * 2)})
    elif code == "GT2":
        for stop in (1.6, 2.0, 2.4):
            for atr in (8, 10, 12):
                add(f"stop{stop}-st{atr}", {"stop_mult": stop, "st_atr": atr, "st_mult": 2.0})
    elif code == "GT14":
        for stop in (2.4, 3.0, 3.6):
            for fast, slow in ((10, 21), (12, 26), (14, 31)):
                add(f"stop{stop}-m{fast}/{slow}", {"stop_mult": stop, "macd_fast": fast, "macd_slow": slow})
    return out


def _cliff(rows: list[dict[str, Any]], baseline_label_substr: str) -> tuple[bool, str]:
    base = next((r for r in rows if baseline_label_substr in r["label"]), None)
    if base is None or not base["net"]:
        return False, "no baseline"
    axis = []
    for r in rows:
        if r is base:
            continue
        pct = (r["net"] - base["net"]) / abs(base["net"]) * 100
        # keep only single-axis neighbours (stop± or length±, not both)
        axis.append((r["label"], pct))
    if not axis:
        return False, "empty"
    worst = max(abs(p) for _, p in axis)
    if worst > 40:
        return True, f"cliff (largest move {worst:.1f}%)"
    if worst > 20:
        return False, f"soft edge (largest move {worst:.1f}%)"
    return False, f"plateau (largest move {worst:.1f}%)"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)

    holdout = load_holdout()
    research_end = holdout[0] - timedelta(days=1)
    lines: list[str] = [
        "# GOLD TREND GT9 / GT2 / GT14 — Nifty options research",
        "",
        "Port from AurumBoss goldtrend_book (EA maths). 5m feed, 09:15-anchored 15/20/30m signals, "
        "live timing, slippage 0.2, delta-adjusted fills, square-off 15:15, no paper slot.",
        "",
        f"Research {RESEARCH_START} .. {research_end}; real-premium {HISTORY_START} .. {research_end}. "
        f"Holdout {holdout[0]} .. {holdout[1]} excluded.",
        "",
        "Stops: GT9 1.5×ATR(20m), GT2 2.0×ATR(30m), GT14 3.0×ATR(20m). No target. last_entry 14:30.",
        "",
    ]

    if args.smoke:
        specs = [
            {
                "label": "GT2-QM250-orig|research",
                "window": "research",
                "start": RESEARCH_START.isoformat(),
                "end": min(RESEARCH_START + timedelta(days=40), research_end).isoformat(),
                "strategy": "gold_trend_2",
                "timeframe": TF,
                "params": {"warmup_bars": 20, "warmup_days": 5},
                "strike_premium": 250.0,
                "family": "GT2",
            },
        ]
    else:
        specs = _main_specs(research_end)

    print(f"jobs={len(specs)} workers={args.workers}", flush=True)
    results: list[dict[str, Any]] = []

    def run_pool(job_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if args.workers <= 1:
            for i, spec in enumerate(job_specs, 1):
                print(f"[{i}/{len(job_specs)}] {spec['label']} ...", flush=True)
                rows.append(_job(spec))
                s = rows[-1]["summary"]
                print(f"  n={s['trades']} net={s['net']:.0f} pf={s['pf']}", flush=True)
            return rows
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(_job, spec): spec for spec in job_specs}
            done = 0
            for fut in as_completed(futs):
                done += 1
                row = fut.result()
                rows.append(row)
                s = row["summary"]
                print(
                    f"[{done}/{len(job_specs)}] {s['label']} n={s['trades']} net={s['net']:.0f} pf={s['pf']}",
                    flush=True,
                )
        return rows

    results = run_pool(specs)

    by_window: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_window[row["spec"]["window"]].append(row["summary"])

    head = (
        "| run | trades | win % | net ₹ | PF | max DD ₹ | −top10 ₹ | avg hold min | "
        "long n / ₹ | short n / ₹ |"
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
        for r in sorted(rows, key=lambda x: x["label"]):
            if r["label"].startswith("S5") or r["trades"] == 0:
                continue
            lines += [f"### Year-by-year — {r['label']}", "", "| year | net ₹ |", "|---|---|"]
            for y, v in r["years"].items():
                lines.append(f"| {y} | {_f(v)} |")
            lines.append("")

    def base_key(label: str) -> str:
        return label.rsplit("|", 1)[0]

    res_by = {base_key(r["label"]): r for r in by_window.get("research", [])}
    real_by = {base_key(r["label"]): r for r in by_window.get("real-prem", [])}

    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    lines += ["## Candidate flags", ""]
    for key in sorted(set(res_by) & set(real_by)):
        if key.startswith("S5"):
            continue
        ok = _passes(res_by[key], real_by[key])
        mark = "PASS" if ok else "fail"
        lines.append(
            f"- **{mark}** `{key}`: research net {_f(res_by[key]['net'])} n={res_by[key]['trades']}; "
            f"real-prem net {_f(real_by[key]['net'])} PF {_f(real_by[key]['pf'], 2)} "
            f"−top10 {_f(real_by[key]['without_top10'])} n={real_by[key]['trades']}"
        )
        if ok:
            candidates.append((key, res_by[key], real_by[key]))
    lines.append("")

    # Sensitivity for anything with real-prem PF > 1.2 (primary QM250-orig if present)
    sens_targets: list[tuple[str, str]] = []
    for code, strategy, _ in STRATS:
        for key, r in real_by.items():
            if not key.startswith(code):
                continue
            if r.get("pf") is not None and r["pf"] > 1.2 and (code, strategy) not in sens_targets:
                sens_targets.append((code, strategy))

    cliff_fail: set[str] = set()
    if sens_targets and not args.smoke:
        lines += ["## Sensitivity ±20% (real-prem, QM250 original exits)", ""]
        for code, strategy in sens_targets:
            sens_specs = _sensitivity_specs(code, strategy, research_end)
            print(f"=== sensitivity {code} ({len(sens_specs)} jobs) ===", flush=True)
            sens_rows = [x["summary"] for x in run_pool(sens_specs)]
            lines += [f"### {code}", "", head, sep]
            for r in sorted(sens_rows, key=lambda x: x["label"]):
                lines.append(_row_md(r))
            is_cliff, note = _cliff(sens_rows, "stop" + ("1.5" if code == "GT9" else "2.0" if code == "GT2" else "3.0"))
            # better cliff detection: compare to default params row
            base = next(
                (r for r in sens_rows if ("stop1.5-t9/k26" in r["label"] or "stop2.0-st10" in r["label"]
                                          or "stop3.0-m12/26" in r["label"])),
                None,
            )
            if base and abs(base["net"] or 0) > 0:
                neigh = []
                for r in sens_rows:
                    if r is base:
                        continue
                    # axis: same lengths different stop, or same stop different lengths
                    pct = abs((r["net"] - base["net"]) / abs(base["net"]) * 100)
                    if code == "GT9":
                        axis = ("stop1.2-t9/k26" in r["label"] or "stop1.8-t9/k26" in r["label"]
                                or "stop1.5-t7/k21" in r["label"] or "stop1.5-t11/k31" in r["label"])
                    elif code == "GT2":
                        axis = ("stop1.6-st10" in r["label"] or "stop2.4-st10" in r["label"]
                                or "stop2.0-st8" in r["label"] or "stop2.0-st12" in r["label"])
                    else:
                        axis = ("stop2.4-m12/26" in r["label"] or "stop3.6-m12/26" in r["label"]
                                or "stop3.0-m10/21" in r["label"] or "stop3.0-m14/31" in r["label"])
                    if axis:
                        neigh.append(pct)
                worst = max(neigh) if neigh else 0.0
                is_cliff = worst > 40
                note = f"{'cliff' if is_cliff else 'plateau' if worst <= 20 else 'soft edge'} (axis max {worst:.1f}%)"
            lines.append(f"**Verdict {code}:** {note}.")
            lines.append("")
            if is_cliff:
                cliff_fail.add(code)

    # Drop cliff families from candidates
    candidates = [c for c in candidates if c[0].split("-")[0] not in cliff_fail]

    # Rank passers by real-prem net; holdout ≤2
    candidates.sort(key=lambda x: x[2]["net"], reverse=True)
    holdout_runs = candidates[:2]
    lines += ["## Holdout (≤2 passers)", ""]
    if not holdout_runs:
        lines.append("No candidates passed the gates (or sensitivity cliff). Holdout not run.")
        lines.append("")
    elif args.smoke:
        lines.append("Smoke mode — holdout skipped.")
        lines.append("")
    else:
        for key, _, real in holdout_runs:
            code = key.split("-")[0]
            strategy = next(s for c, s, _ in STRATS if c == code)
            # rebuild params from key
            params: dict[str, Any] = {}
            prem = 250.0 if "QM250" in key else None
            exit_rule = "premium_trail_10" if "trail10" in key else None
            if "long" in key:
                params["mode"] = "long_only"
            elif "short" in key:
                params["mode"] = "short_only"
            print(f"=== holdout {key} ===", flush=True)
            out = _run(
                holdout[0], holdout[1], strategy=strategy, params=params, timeframe=TF,
                strike_premium=prem, exit_rule=exit_rule, holdout=True,
            )
            h = _summarise(f"{key}|holdout", out)
            lines.append(
                f"- **{key}** holdout: n={h['trades']} net {_f(h['net'])} PF {_f(h['pf'], 2)} "
                f"DD {_f(h['max_dd'])} −top10 {_f(h['without_top10'])} avg hold {_f(h['avg_hold_min'], 1)}m"
            )
            print(f"  holdout n={h['trades']} net={h['net']:.0f}", flush=True)
        lines.append("")

    # vs S5
    lines += ["## vs S5-QM-long", ""]
    for wname in ("research", "real-prem"):
        s5 = next((r for r in by_window.get(wname, []) if r["label"].startswith("S5")), None)
        if not s5:
            continue
        lines.append(f"- **{wname} S5-QM-long:** n={s5['trades']} net {_f(s5['net'])} PF {_f(s5['pf'], 2)} DD {_f(s5['max_dd'])}")
        for code, _, _ in STRATS:
            for suffix in ("QM250-orig", "QM250-trail10", "ATM-orig"):
                r = next((x for x in by_window.get(wname, []) if x["label"] == f"{code}-{suffix}|{wname}"), None)
                if r:
                    lines.append(
                        f"  - {code}-{suffix}: n={r['trades']} net {_f(r['net'])} PF {_f(r['pf'], 2)} "
                        f"(vs S5 Δ {_f(r['net'] - s5['net'])})"
                    )
    lines.append("")

    text = "\n".join(lines)
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gold_trend_{date.today().isoformat()}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
