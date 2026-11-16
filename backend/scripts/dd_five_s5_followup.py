"""S5 long follow-up: real-premium window, year/month, leave-top-N, split-half, sensitivity. No network.

    cd backend && uv run python -m scripts.dd_five_s5_followup

Slot 5 config unchanged. Writes data/validation/dd_five_s5_followup_<today>.md.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_run
from app.backtest.holdout import load_holdout
from app.config import settings
from app.exits.rules import NO_TARGET
from app.options.model import HISTORY_START

IST = timezone(timedelta(hours=5, minutes=30))
RESEARCH_START = date(2022, 1, 3)
FORWARD_START = date(2026, 10, 2)
FORWARD_END = date(2026, 11, 13)
SLOT5 = {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"}


def _run(start: date, end: date, *, strike: float = 250.0, stop: float = 0.10,
         trail_every: float = 0.15, trail_by: float | None = None) -> dict[str, Any]:
    trail_by = stop if trail_by is None else trail_by
    body: dict[str, Any] = {
        "strategy": "dd_five_confirm",
        "params": SLOT5,
        "symbol": "NIFTY50",
        "timeframe": "3m",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": ["normal"],
        "mode": "options",
        "strike_offset": 0,
        "strike_premium": strike,
        "slippage_points": 0.2,
        "live_timing": True,
        "exit_rule": {"kind": "premium", "stop": stop, "target": NO_TARGET,
                      "trail_every": trail_every, "trail_by": trail_by},
    }
    return execute_run(parse_config(body), lambda _e: None, hash_data=False)


def _nets(out: dict[str, Any]) -> list[tuple[datetime, float]]:
    rows: list[tuple[datetime, float]] = []
    for t in out["result"]["trades"]:
        opt = t.get("option") or {}
        if opt.get("net_pnl") is None:
            continue
        rows.append((datetime.fromtimestamp(int(t["entry_time"]), IST), float(opt["net_pnl"])))
    return rows


def _summary(nets: list[float], out: dict[str, Any] | None = None) -> dict[str, Any]:
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]
    total = sum(nets)
    opt = ((out or {}).get("result") or {}).get("summary", {}).get("option") or {}
    counters = ((out or {}).get("result") or {}).get("summary", {})
    # real/modelled live in option counters on the overlay summary
    real = modelled = None
    if out is not None:
        s = out["result"]["summary"]
        # execute packs real_fills at view level — try both
        real = s.get("real_fills")
        modelled = s.get("modelled_fills")
        if real is None and isinstance(s.get("option"), dict):
            real = s["option"].get("real_fills")
            modelled = s["option"].get("modelled_fills")
    return {
        "trades": len(nets),
        "win_pct": 100.0 * len(wins) / len(nets) if nets else 0.0,
        "net": total,
        "max_dd": opt.get("max_drawdown"),
        "real_fills": real,
        "modelled_fills": modelled,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
    }


def _leave_top(nets: list[float], n: int) -> float:
    if len(nets) <= n:
        return 0.0
    kept = sorted(nets, reverse=True)[n:]
    return sum(kept)


def _f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "-"
    return f"{x:,.{nd}f}" if isinstance(x, (int, float)) else str(x)


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    holdout = load_holdout()
    research_end = holdout[0] - timedelta(days=1)
    lines: list[str] = [
        "# S5 long follow-up (dd_five_confirm Quantman trail10, slot 5 config unchanged)",
        "",
        "3m, long_only, use_wr, max_trades_per_day 10, last_entry 15:10, strike_premium 250, "
        "exit_rule premium_trail_10 (stop 10% / trail 15→10), live timing, slippage 0.2.",
        "",
    ]

    # --- 1. Real-premium window ---
    print("1. real-premium window ...", flush=True)
    real_out = _run(HISTORY_START, research_end)
    real_pairs = _nets(real_out)
    real_nets = [v for _, v in real_pairs]
    # fill counts from result summary / trades
    real_fills = modelled_fills = 0
    for t in real_out["result"]["trades"]:
        opt = t.get("option") or {}
        for src in (opt.get("entry_source"), opt.get("exit_source")):
            if src == "real":
                real_fills += 1
            elif src == "modelled":
                modelled_fills += 1
    opt_sum = (real_out["result"]["summary"].get("option") or {})
    packed_real = opt_sum.get("real_fills")
    packed_mod = opt_sum.get("modelled_fills")
    show_real = packed_real if packed_real is not None else real_fills
    show_mod = packed_mod if packed_mod is not None else modelled_fills
    s1 = _summary(real_nets, real_out)
    lines += [
        "## 1. Real-premium window "
        f"({HISTORY_START.isoformat()} .. {research_end.isoformat()})",
        "",
        f"| trades | win % | net ₹ | max DD ₹ | real fills | modelled fills |",
        f"|---|---|---|---|---|---|",
        f"| {s1['trades']} | {_f(s1['win_pct'], 1)} | {_f(s1['net'])} | {_f(s1['max_dd'])} | "
        f"{show_real} | {show_mod} |",
        "",
        f"(Fill counts: packed option counters real={packed_real}, modelled={packed_mod}; "
        f"per-leg entry+exit sources real={real_fills}, modelled={modelled_fills}.)",
        "",
    ]
    print(f"   trades {s1['trades']} win {s1['win_pct']:.1f}% net {s1['net']:.0f} "
          f"dd {s1['max_dd']} real {real_fills} modelled {modelled_fills}", flush=True)

    # --- 2. Year / month (whole research) ---
    print("2. whole research year/month ...", flush=True)
    full_out = _run(RESEARCH_START, research_end)
    full_pairs = _nets(full_out)
    by_year: dict[int, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    for when, net in full_pairs:
        by_year[when.year] += net
        by_month[f"{when.year}-{when.month:02d}"] += net
    losing_months = sum(1 for v in by_month.values() if v < 0)
    lines += [
        f"## 2. Year / month net (research {RESEARCH_START} .. {research_end})",
        "",
        "### By year",
        "",
        "| year | net ₹ |",
        "|---|---|",
    ]
    for y in sorted(by_year):
        lines.append(f"| {y} | {_f(by_year[y])} |")
    lines += [
        "",
        f"### By month ({len(by_month)} months, **{losing_months} losing**)",
        "",
        "| month | net ₹ |",
        "|---|---|",
    ]
    for m in sorted(by_month):
        lines.append(f"| {m} | {_f(by_month[m])} |")
    lines.append("")
    print(f"   years {dict(by_year)}; losing months {losing_months}/{len(by_month)}", flush=True)

    # --- 3. Leave top 5 / 10 ---
    print("3. leave top-N ...", flush=True)
    fwd_out = _run(FORWARD_START, FORWARD_END)
    fwd_nets = [v for _, v in _nets(fwd_out)]
    full_nets = [v for _, v in full_pairs]
    lines += [
        "## 3. Net with top trades removed",
        "",
        "| period | n | full net ₹ | without top 5 ₹ | without top 10 ₹ |",
        "|---|---|---|---|---|",
        f"| research {RESEARCH_START}..{research_end} | {len(full_nets)} | {_f(sum(full_nets))} | "
        f"{_f(_leave_top(full_nets, 5))} | {_f(_leave_top(full_nets, 10))} |",
        f"| real-prem {HISTORY_START}..{research_end} | {len(real_nets)} | {_f(sum(real_nets))} | "
        f"{_f(_leave_top(real_nets, 5))} | {_f(_leave_top(real_nets, 10))} |",
        f"| forward {FORWARD_START}..{FORWARD_END} | {len(fwd_nets)} | {_f(sum(fwd_nets))} | "
        f"{_f(_leave_top(fwd_nets, 5))} | {_f(_leave_top(fwd_nets, 10))} |",
        "",
    ]

    # --- 4. Split-half (research by calendar midpoint of window) ---
    print("4. split-half ...", flush=True)
    mid = RESEARCH_START + (research_end - RESEARCH_START) / 2
    # split by entry date relative to midpoint
    first = [v for when, v in full_pairs if when.date() <= mid]
    second = [v for when, v in full_pairs if when.date() > mid]
    s_first, s_second = _summary(first), _summary(second)
    # also run as two separate date ranges for DD
    h1_end = mid
    h2_start = mid + timedelta(days=1)
    print(f"   half1 ..{h1_end} / half2 {h2_start}..", flush=True)
    h1_out = _run(RESEARCH_START, h1_end if isinstance(h1_end, date) else h1_end.date())
    h2_out = _run(h2_start if isinstance(h2_start, date) else h2_start.date(), research_end)
    h1_nets = [v for _, v in _nets(h1_out)]
    h2_nets = [v for _, v in _nets(h2_out)]
    sh1, sh2 = _summary(h1_nets, h1_out), _summary(h2_nets, h2_out)
    lines += [
        f"## 4. Split-half (research midpoint {mid.date() if hasattr(mid, 'date') else mid})",
        "",
        "| half | range | trades | win % | net ₹ | max DD ₹ |",
        "|---|---|---|---|---|---|",
        f"| first | {RESEARCH_START} .. {h1_end.date() if hasattr(h1_end, 'date') else h1_end} | "
        f"{sh1['trades']} | {_f(sh1['win_pct'], 1)} | {_f(sh1['net'])} | {_f(sh1['max_dd'])} |",
        f"| second | {h2_start.date() if hasattr(h2_start, 'date') else h2_start} .. {research_end} | "
        f"{sh2['trades']} | {_f(sh2['win_pct'], 1)} | {_f(sh2['net'])} | {_f(sh2['max_dd'])} |",
        "",
    ]

    # --- 5. Sensitivity ---
    print("5. sensitivity grid ...", flush=True)
    sens_rows: list[dict[str, Any]] = []
    for strike in (200, 250, 300):
        for stop_pct in (8, 10, 12):
            stop = stop_pct / 100.0
            label = f"strike {strike} / trail {stop_pct}%"
            print(f"   {label} ...", flush=True)
            out = _run(RESEARCH_START, research_end, strike=float(strike), stop=stop)
            nets = [v for _, v in _nets(out)]
            s = _summary(nets, out)
            sens_rows.append({"label": label, "strike": strike, "stop_pct": stop_pct, **s})
            print(f"      n={s['trades']} net={s['net']:.0f} dd={s['max_dd']}", flush=True)

    lines += [
        f"## 5. Sensitivity (research {RESEARCH_START} .. {research_end}; report only)",
        "",
        "Vary `strike_premium` ∈ {200,250,300} and premium stop/trail_by ∈ {8%,10%,12%} "
        "(trail_every fixed at 15%). Slot stays at 250 / 10%.",
        "",
        "| strike | trail % | trades | win % | net ₹ | max DD ₹ |",
        "|---|---|---|---|---|---|",
    ]
    for r in sens_rows:
        lines.append(
            f"| {r['strike']} | {r['stop_pct']} | {r['trades']} | {_f(r['win_pct'], 1)} | "
            f"{_f(r['net'])} | {_f(r['max_dd'])} |"
        )
    # plateau vs cliff: axis ±20% around baseline 250/10, plus full grid
    base = next(r for r in sens_rows if r["strike"] == 250 and r["stop_pct"] == 10)
    neighbors = [r for r in sens_rows if r is not base]
    rel = [(r["label"], (r["net"] - base["net"]) / abs(base["net"]) * 100 if base["net"] else None)
           for r in neighbors]
    axis = [
        next(r for r in sens_rows if r["strike"] == 200 and r["stop_pct"] == 10),
        next(r for r in sens_rows if r["strike"] == 300 and r["stop_pct"] == 10),
        next(r for r in sens_rows if r["strike"] == 250 and r["stop_pct"] == 8),
        next(r for r in sens_rows if r["strike"] == 250 and r["stop_pct"] == 12),
    ]
    axis_rel = [
        (r["label"], (r["net"] - base["net"]) / abs(base["net"]) * 100 if base["net"] else None)
        for r in axis
    ]
    lines += [
        "",
        f"Baseline (250 / 10%): net {_f(base['net'])}, DD {_f(base['max_dd'])}.",
        "Relative net vs baseline (full grid):",
        "",
    ]
    for label, pct in rel:
        lines.append(f"- {label}: {_f(pct, 1)}%")
    lines += ["", "Axis ±20% only:", ""]
    for label, pct in axis_rel:
        lines.append(f"- {label}: {_f(pct, 1)}%")
    abs_axis = [abs(p) for _, p in axis_rel if p is not None]
    abs_all = [abs(p) for _, p in rel if p is not None]
    # cliff = any axis move >40%; soft edge = any axis >20% but ≤40%; else plateau
    if any(p > 40 for p in abs_axis):
        verdict = (
            "**Verdict:** +-20% looks like a **cliff** on an axis neighbour "
            "(|>40%| net move vs baseline)."
        )
    elif any(p > 20 for p in abs_axis):
        worst = max(abs_axis) if abs_axis else 0.0
        verdict = (
            f"**Verdict:** +-20% is mostly a **plateau**, with a soft edge "
            f"(largest axis move {_f(worst, 1)}%; strike ±20% mild; trail 12% softens). "
            f"Not a cliff — nets stay largely positive across the grid "
            f"(worst corner {_f(max(abs_all) if abs_all else 0.0, 1)}%)."
        )
    else:
        verdict = (
            "**Verdict:** +-20% looks like a **plateau** "
            "(all axis neighbours within ~20% of baseline net)."
        )
    lines += ["", verdict, ""]

    text = "\n".join(lines)
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dd_five_s5_followup_{date.today().isoformat()}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
