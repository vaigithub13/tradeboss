"""Compare Quantman dd_five_confirm exit overlays on 3m (slot 5 long / slot 6 short). No network.

    cd backend && uv run python -m scripts.dd_five_exit_compare

Real premiums (shipped model), live timing, slippage 0.2. Research = 2022-01-03 through the day before
the fixed holdout (fills before 2024-10-03 are mostly modelled). Forward = 2026-10-02..2026-11-13.
Does not change paper_slots.json. Writes data/validation/dd_five_exit_compare_<today>.md.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_run
from app.backtest.holdout import load_holdout
from app.config import settings
from app.exits.rules import NO_TARGET
IST = timezone(timedelta(hours=5, minutes=30))
RESEARCH_START = date(2022, 1, 3)
FORWARD_START = date(2026, 10, 2)
FORWARD_END = date(2026, 11, 13)

#: slot 5 / 6 strategy params (exit_rule is the variant under test)
SLOT5 = {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"}
SLOT6 = {"mode": "short_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"}
BASE_EXTRA = {"strike_premium": 250, "slippage_points": 0.2, "live_timing": True}

#: (id, side label, strategy params, exit_rule setting — None = Supertrend flip only)
VARIANTS: list[tuple[str, str, dict[str, Any], Any]] = [
    ("S5-a-trail10", "long", SLOT5, "premium_trail_10"),
    ("S5-b-st_only", "long", SLOT5, None),
    ("S5-c-stop20", "long", SLOT5, {"kind": "premium", "stop": 0.20, "target": NO_TARGET}),
    ("S6-a-trail10", "short", SLOT6, "premium_trail_10"),
    ("S6-b-st_only", "short", SLOT6, None),
    ("S6-c-stop20", "short", SLOT6, {"kind": "premium", "stop": 0.20, "target": NO_TARGET}),
]


def summarise(rid: str, period: str, out: dict[str, Any]) -> dict[str, Any]:
    trades = out["result"]["trades"]
    nets: list[float] = []
    holds: list[int] = []
    for t in trades:
        opt = t.get("option") or {}
        if opt.get("net_pnl") is None:
            continue
        nets.append(float(opt["net_pnl"]))
        holds.append(int(t["exit_time"]) - int(t["entry_time"]))
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]
    total = sum(nets)
    top5 = sum(sorted(nets, reverse=True)[:5])
    within_2m = sum(1 for h in holds if h <= 120)
    return {
        "id": rid,
        "period": period,
        "trades": len(nets),
        "win_pct": 100.0 * len(wins) / len(nets) if nets else 0.0,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "net": total,
        "max_dd": (out["result"]["summary"].get("option") or {}).get("max_drawdown"),
        "exit_le_2m": within_2m,
        "exit_le_2m_pct": 100.0 * within_2m / len(holds) if holds else 0.0,
        "top5_share": (100.0 * top5 / total) if total > 0 else None,
        "warnings": out.get("warnings") or [],
    }


def _f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "-"
    return f"{x:,.{nd}f}" if isinstance(x, (int, float)) else str(x)


def table(rows: list[dict[str, Any]]) -> str:
    head = ["run", "period", "trades", "win %", "avg win ₹", "avg loss ₹", "net ₹", "max DD ₹",
            "exit ≤2m", "exit ≤2m %", "top-5 share %"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        lines.append("| " + " | ".join([
            r["id"], r["period"], str(r["trades"]), _f(r["win_pct"], 1),
            _f(r["avg_win"]), _f(r["avg_loss"]), _f(r["net"]), _f(r["max_dd"]),
            str(r["exit_le_2m"]), _f(r["exit_le_2m_pct"], 1), _f(r["top5_share"], 0),
        ]) + " |")
    return "\n".join(lines)


def run_one(rid: str, params: dict[str, Any], exit_rule: Any, start: date, end: date,
            period: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "strategy": "dd_five_confirm",
        "params": params,
        "symbol": "NIFTY50",
        "timeframe": "3m",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": ["normal"],
        "mode": "options",
        "strike_offset": 0,
        **BASE_EXTRA,
    }
    if exit_rule is not None:
        body["exit_rule"] = exit_rule
    config = parse_config(body)
    print(f"{rid} {period} {start}..{end} ...", flush=True)
    out = execute_run(config, lambda _e: None, hash_data=False)
    row = summarise(rid, period, out)
    print(f"   trades {row['trades']}, win {row['win_pct']:.1f}%, net {_f(row['net'])}, "
          f"DD {_f(row['max_dd'])}, ≤2m {row['exit_le_2m']} ({row['exit_le_2m_pct']:.1f}%), "
          f"top5 {_f(row['top5_share'], 0)}%", flush=True)
    return row


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    holdout = load_holdout()
    research_start, research_end = RESEARCH_START, holdout[0] - timedelta(days=1)
    periods = [
        ("research", research_start, research_end),
        ("forward", FORWARD_START, FORWARD_END),
    ]
    rows: list[dict[str, Any]] = []
    for period, start, end in periods:
        for rid, _side, params, exit_rule in VARIANTS:
            rows.append(run_one(rid, params, exit_rule, start, end, period))

    text = (
        f"# DD Quantman exit comparison, NIFTY50 3m, real premiums, live timing, slippage 0.2\n\n"
        f"Slot 5/6 strategy params unchanged (mode, use_wr, max_trades_per_day, last_entry, strike_premium 250).\n"
        f"Variants: **a** `premium_trail_10` (current slots); **b** Supertrend flip only (no premium exit_rule); "
        f"**c** premium stop 20% / no target (+ Supertrend EXIT still applies).\n\n"
        f"- Research: {research_start} .. {research_end} (all data except holdout "
        f"{holdout[0]}..{holdout[1]}; fills before 2024-10-03 mostly modelled)\n"
        f"- Forward: {FORWARD_START} .. {FORWARD_END}\n\n"
        + table(rows) + "\n"
    )
    for r in rows:
        modelled = [w for w in r["warnings"] if "modelled" in w.lower()]
        if modelled:
            text += f"\n{r['id']} ({r['period']}): " + modelled[0]
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dd_five_exit_compare_{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
