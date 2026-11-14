"""Backtest the DD Nifty 5-confirmation strategy and compare it with the paper slots' strategies. No network.

    cd backend && uv run python -m scripts.dd_five_confirm_backtest [--start 2022-01-03] [--end 2026-06-30]

Options mode (shipped premium model, charges, slippage), normal sessions, the period before the fixed holdout.
Rows: dd_five_confirm on 5m and 3m with the ATM strike and one strike in the money, plus log_xz and
price_channel(20) on 5m as the comparison. Extra columns: longest losing streak, the share of the net made by
the 5 best trades, long vs short. The table is written to data/validation/.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_run
from app.config import settings

IST = timezone(timedelta(hours=5, minutes=30))

RUNS: list[tuple[str, str, dict[str, Any], str, int]] = [
    ("DD-5m-ATM", "dd_five_confirm", {}, "5m", 0),
    ("DD-5m-ITM1", "dd_five_confirm", {}, "5m", -1),
    ("DD-3m-ATM", "dd_five_confirm", {}, "3m", 0),
    ("DD-3m-ITM1", "dd_five_confirm", {}, "3m", -1),
    ("LogXZ-5m", "log_xz", {}, "5m", 0),
    ("PChan20-5m", "price_channel", {"length": 20}, "5m", 0),
]


def _pf(values: list[float]) -> float | None:
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    return None if losses == 0 else gains / losses


def _streak(values: list[float]) -> int:
    best = cur = 0
    for v in values:
        cur = cur + 1 if v <= 0 else 0
        best = max(best, cur)
    return best


def summarise(rid: str, out: dict[str, Any]) -> dict[str, Any]:
    trades = out["result"]["trades"]
    opt = [t["option"]["net_pnl"] if t.get("option") and t["option"].get("net_pnl") is not None else None
           for t in trades]
    pts = [(t["exit_price"] - t["entry_price"]) * (1 if t["direction"] == "LONG" else -1) for t in trades]
    nets = [v for v in opt if v is not None]
    total = sum(nets)
    top5 = sum(sorted(nets, reverse=True)[:5])
    by_year: dict[int, float] = defaultdict(float)
    side: dict[str, list[float]] = defaultdict(list)
    for t, v in zip(trades, opt):
        if v is None:
            continue
        by_year[datetime.fromtimestamp(int(t["entry_time"]), IST).year] += v
        side[t["direction"]].append(v)
    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p <= 0]
    return {
        "id": rid, "trades": len(trades),
        "win_pct": 100.0 * len(wins) / len(pts) if pts else 0.0,
        "avg_win_pts": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_pts": sum(losses) / len(losses) if losses else 0.0,
        "net_pts": sum(pts), "pts_pf": _pf(pts),
        "opt_net": total, "opt_pf": _pf(nets), "opt_per_trade": total / len(nets) if nets else 0.0,
        "opt_dd": (out["result"]["summary"].get("option") or {}).get("max_drawdown"),
        "streak": _streak(nets), "best": max(nets) if nets else 0.0,
        "top5_share": (100.0 * top5 / total) if total > 0 else None,
        "long": (len(side["LONG"]), sum(side["LONG"])), "short": (len(side["SHORT"]), sum(side["SHORT"])),
        "years": dict(sorted(by_year.items())), "warnings": out.get("warnings") or [],
    }


def _f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "-"
    return f"{x:,.{nd}f}" if isinstance(x, float) else str(x)


def table(rows: list[dict[str, Any]]) -> str:
    years = sorted({y for r in rows for y in r["years"]})
    head = ["run", "trades", "win %", "avg win pts", "avg loss pts", "net pts", "pts PF", "option net ₹",
            "option PF", "₹/trade", "max DD ₹", "lose streak", "best ₹", "top-5 share %", "long n / ₹",
            "short n / ₹", *map(str, years)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        lines.append("| " + " | ".join([
            r["id"], str(r["trades"]), _f(r["win_pct"], 1), _f(r["avg_win_pts"], 1), _f(r["avg_loss_pts"], 1),
            _f(r["net_pts"]), _f(r["pts_pf"], 2), _f(r["opt_net"]), _f(r["opt_pf"], 2), _f(r["opt_per_trade"]),
            _f(r["opt_dd"]), str(r["streak"]), _f(r["best"]), _f(r["top5_share"], 0),
            f"{r['long'][0]} / {_f(r['long'][1])}", f"{r['short'][0]} / {_f(r['short'][1])}",
            *[_f(r["years"].get(y)) for y in years]]) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="DD 5-confirmation backtest")
    p.add_argument("--start", default="2022-01-03")
    p.add_argument("--end", default="2026-06-30")
    p.add_argument("--only", default="", help="comma-separated run ids")
    args = p.parse_args(argv)
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    rows = []
    for rid, strategy, params, tf, offset in RUNS:
        if wanted and rid not in wanted:
            continue
        config = parse_config({"strategy": strategy, "params": params, "symbol": "NIFTY50", "timeframe": tf,
                               "start": args.start, "end": args.end, "sessions": ["normal"], "mode": "options",
                               "strike_offset": offset})
        print(f"{rid} ...", flush=True)
        rows.append(summarise(rid, execute_run(config, lambda _e: None, hash_data=False)))
        r = rows[-1]
        print(f"   trades {r['trades']}, win {r['win_pct']:.1f}%, net pts {r['net_pts']:.0f}, "
              f"option net {_f(r['opt_net'])}, PF {_f(r['opt_pf'], 2)}", flush=True)
    text = (f"# DD 5-confirmation vs paper strategies, NIFTY50, {args.start} to {args.end} "
            f"(options mode, 1 lot, after costs)\n\n" + table(rows) + "\n")
    for r in rows:
        if r["warnings"]:
            text += f"\n{r['id']} warnings: " + "; ".join(r["warnings"])
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dd_five_confirm_{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
