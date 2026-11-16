"""Backtest ema_pivot_pullback (5m, options) vs slot-5 Quantman. No network. No paper slot.

    cd backend && uv run python -m scripts.ema_pivot_pullback_backtest

Windows: research (holdout excluded), real-premium (2024-10-03..research_end), forward
(2026-10-02..2026-11-16). ATM and 1-strike ITM; long vs short; year-by-year; leave top-5/10;
avg hold; exit reasons; trades/day; ±20% on EMA lengths and RR. Slot 5 comparison row included.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_run
from app.backtest.holdout import load_holdout
from app.config import settings
from app.options.model import HISTORY_START

IST = timezone(timedelta(hours=5, minutes=30))
RESEARCH_START = date(2022, 1, 3)
FORWARD_START = date(2026, 10, 2)
FORWARD_END = date(2026, 11, 16)

SLOT5 = {
    "strategy": "dd_five_confirm",
    "params": {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
    "timeframe": "3m",
    "strike_premium": 250.0,
    "exit_rule": "premium_trail_10",
}

def _run(start: date, end: date, *, strategy: str, params: dict[str, Any], timeframe: str,
         strike_offset: int = 0, strike_premium: float | None = None,
         exit_rule: str | None = None) -> dict[str, Any]:
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
    }
    if strike_premium is not None:
        body["strike_premium"] = strike_premium
    if exit_rule is not None:
        body["exit_rule"] = exit_rule
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
        "without_top5": _leave_top(nets, 5),
        "without_top10": _leave_top(nets, 10),
        "avg_hold_min": sum(holds) / len(holds) if holds else 0.0,
        "exit_reasons": dict(reasons),
        "trades_per_day": tpd,
        "long_n": len(by_side["LONG"]),
        "long_net": sum(by_side["LONG"]),
        "short_n": len(by_side["SHORT"]),
        "short_net": sum(by_side["SHORT"]),
        "years": dict(sorted(by_year.items())),
        "real_fills": opt.get("real_fills"),
        "modelled_fills": opt.get("modelled_fills"),
    }


def _f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return str(x)


def _row_md(r: dict[str, Any]) -> str:
    return (
        f"| {r['label']} | {r['trades']} | {_f(r['win_pct'], 1)} | {_f(r['net'])} | {_f(r['max_dd'])} | "
        f"{_f(r['without_top5'])} | {_f(r['without_top10'])} | {_f(r['avg_hold_min'], 1)} | "
        f"{_f(r['trades_per_day'], 2)} | {r['long_n']} / {_f(r['long_net'])} | "
        f"{r['short_n']} / {_f(r['short_net'])} |"
    )


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    holdout = load_holdout()
    research_end = holdout[0] - timedelta(days=1)
    lines: list[str] = [
        "# EMA pivot pullback (SpringPad 20/50 + R1/S1) — 5m options backtest",
        "",
        "Defaults: fast20/slow50, rr=3, entries 09:20-14:30, max 4/day, square-off 15:15, "
        "live timing, slippage 0.2. Slot 5 unchanged (comparison only).",
        "",
        f"Research {RESEARCH_START} .. {research_end}; real-premium {HISTORY_START} .. {research_end}; "
        f"forward {FORWARD_START} .. {FORWARD_END}.",
        "",
    ]

    windows = [
        ("research", RESEARCH_START, research_end),
        ("real-prem", HISTORY_START, research_end),
        ("forward", FORWARD_START, FORWARD_END),
    ]
    emp_variants = [
        ("EMP-ATM-both", {}, 0),
        ("EMP-ITM1-both", {}, -1),
        ("EMP-ATM-long", {"mode": "long_only"}, 0),
        ("EMP-ATM-short", {"mode": "short_only"}, 0),
        ("EMP-ITM1-long", {"mode": "long_only"}, -1),
        ("EMP-ITM1-short", {"mode": "short_only"}, -1),
    ]

    head = (
        "| run | trades | win % | net ₹ | max DD ₹ | −top5 ₹ | −top10 ₹ | avg hold min | "
        "trades/day | long n / ₹ | short n / ₹ |"
    )
    sep = "|" + "---|" * 11

    all_rows: dict[str, list[dict[str, Any]]] = {}
    for wname, start, end in windows:
        print(f"=== {wname} {start}..{end} ===", flush=True)
        lines += [f"## {wname} ({start} .. {end})", "", head, sep]
        rows = []
        for label, params, offset in emp_variants:
            print(f"  {label} ...", flush=True)
            out = _run(start, end, strategy="ema_pivot_pullback", params=params, timeframe="5m",
                       strike_offset=offset)
            r = _summarise(label, out)
            rows.append(r)
            lines.append(_row_md(r))
            print(f"     n={r['trades']} net={r['net']:.0f} dd={r['max_dd']}", flush=True)
        # slot 5 comparison (long only, 3m, premium trail)
        print("  S5-QM-long ...", flush=True)
        s5 = _run(start, end, strategy=SLOT5["strategy"], params=SLOT5["params"],
                  timeframe=SLOT5["timeframe"], strike_offset=0,
                  strike_premium=SLOT5["strike_premium"], exit_rule=SLOT5["exit_rule"])
        r5 = _summarise("S5-QM-long", s5)
        rows.append(r5)
        lines.append(_row_md(r5))
        print(f"     n={r5['trades']} net={r5['net']:.0f} dd={r5['max_dd']}", flush=True)
        lines.append("")
        # year breakdown for ATM both
        atm = next(r for r in rows if r["label"] == "EMP-ATM-both")
        lines += ["### Year-by-year net (EMP-ATM-both)", "", "| year | net ₹ |", "|---|---|"]
        for y, v in atm["years"].items():
            lines.append(f"| {y} | {_f(v)} |")
        lines += ["", "### Exit reasons (EMP-ATM-both)", "", "| reason | n |", "|---|---|"]
        for reason, n in sorted(atm["exit_reasons"].items(), key=lambda x: -x[1]):
            lines.append(f"| {reason} | {n} |")
        lines.append("")
        all_rows[wname] = rows

    # Sensitivity on research window, ATM both
    print("=== sensitivity (research, ATM both) ===", flush=True)
    lines += [
        "## Sensitivity ±20% (research, EMP-ATM-both; report only)",
        "",
        "Vary fast/slow EMA lengths (±20% from 20/50 → 16/40, 20/50, 24/60) and rr (±20% → 2.4, 3.0, 3.6).",
        "",
        "| fast | slow | rr | trades | win % | net ₹ | max DD ₹ |",
        "|---|---|---|---|---|---|---|",
    ]
    base_net = None
    sens = []
    for fast, slow in ((16, 40), (20, 50), (24, 60)):
        for rr in (2.4, 3.0, 3.6):
            label = f"f{fast}/s{slow}/rr{rr}"
            print(f"  {label} ...", flush=True)
            out = _run(RESEARCH_START, research_end, strategy="ema_pivot_pullback",
                       params={"fast": fast, "slow": slow, "rr": rr}, timeframe="5m", strike_offset=0)
            r = _summarise(label, out)
            sens.append(r)
            if fast == 20 and slow == 50 and rr == 3.0:
                base_net = r["net"]
            lines.append(
                f"| {fast} | {slow} | {rr} | {r['trades']} | {_f(r['win_pct'], 1)} | "
                f"{_f(r['net'])} | {_f(r['max_dd'])} |"
            )
            print(f"     n={r['trades']} net={r['net']:.0f}", flush=True)
    lines.append("")
    if base_net and abs(base_net) > 0:
        lines.append(f"Baseline (20/50, rr=3): net {_f(base_net)}.")
        lines.append("Relative net vs baseline:")
        lines.append("")
        axis_pcts = []
        for r in sens:
            if r["label"] == "f20/s50/rr3.0":
                continue
            pct = (r["net"] - base_net) / abs(base_net) * 100
            lines.append(f"- {r['label']}: {_f(pct, 1)}%")
            # axis neighbours only
            if r["label"] in ("f16/s40/rr3.0", "f24/s60/rr3.0", "f20/s50/rr2.4", "f20/s50/rr3.6"):
                axis_pcts.append(abs(pct))
        lines.append("")
        if axis_pcts and max(axis_pcts) > 40:
            verdict = "**Verdict:** +-20% looks like a **cliff** on an axis neighbour."
        elif axis_pcts and max(axis_pcts) > 20:
            verdict = (
                f"**Verdict:** +-20% is mostly a **plateau** with a soft edge "
                f"(largest axis move {_f(max(axis_pcts), 1)}%)."
            )
        else:
            verdict = "**Verdict:** +-20% looks like a **plateau** (axis neighbours within ~20%)."
        lines.append(verdict)
        lines.append("")

    # Short comparison blurb
    lines += ["## vs slot 5 (same windows)", ""]
    for wname, _, _ in windows:
        emp = next(r for r in all_rows[wname] if r["label"] == "EMP-ATM-long")
        s5 = next(r for r in all_rows[wname] if r["label"] == "S5-QM-long")
        lines.append(
            f"- **{wname}:** EMP-ATM-long net {_f(emp['net'])} (n={emp['trades']}, DD {_f(emp['max_dd'])}) vs "
            f"S5-QM-long net {_f(s5['net'])} (n={s5['trades']}, DD {_f(s5['max_dd'])})."
        )
    lines.append("")

    text = "\n".join(lines)
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ema_pivot_pullback_{date.today().isoformat()}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
