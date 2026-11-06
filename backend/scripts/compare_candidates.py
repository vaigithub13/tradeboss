"""Run the pre-registered options candidates on NIFTY50 and print one comparison table. No network.

    cd backend && uv run python -m scripts.compare_candidates [--start 2022-01-03] [--end 2026-06-30]

The list below was fixed before any of these runs: report every row, do not tune and rerun to find a
winner. The default period stops before the fixed holdout (2026-07-01 to 2026-10-01), which this refuses.
Each run is options mode (ATM option, the shipped premium model, charges and slippage), so the
"after costs" columns are the ones that decide. The table is also written to data/validation/.
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

CANDIDATES: list[tuple[str, str, dict[str, Any]]] = [
    ("C1", "opening_range_breakout", {"range_minutes": 15}),
    ("C2", "orb_plus", {"rr": 2.0}),
    ("C3", "orb_plus", {"rr": 3.0}),
    ("C4", "orb_plus", {"rr": 2.0, "prior_day": "nr7"}),
    ("C5", "orb_plus", {"rr": 2.0, "prior_day": "nr4"}),
    ("C6", "orb_plus", {"rr": 2.0, "cpr": "narrow"}),
    ("C7", "orb_plus", {"rr": 2.0, "bias": "cpr"}),
    ("C8", "orb_plus", {"rr": 2.0, "cpr": "narrow", "bias": "cpr"}),
    ("C9", "orb_plus", {"rr": 2.0, "range_minutes": 30}),
    ("C10", "orb_plus", {"rr": 2.0, "range_atr_max": 0.35}),
    ("C11", "pdh_pdl_breakout", {"rr": 2.0, "stop_atr": 0.2}),
    ("C12", "pdh_pdl_breakout", {"rr": 3.0, "stop_atr": 0.2}),
    ("C13", "pdh_pdl_breakout", {"rr": 2.0, "stop_atr": 0.2, "bias": "cpr"}),
]


def _pf(values: list[float]) -> float | None:
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    return None if losses == 0 else gains / losses


def _year(t: int) -> int:
    return datetime.fromtimestamp(int(t), IST).year


def summarise(cid: str, strategy: str, params: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    view = out["result"]
    trades = view["trades"]
    idx = view["summary"]["index"]
    opt = view["summary"]["option"] or {}
    option_nets = [t["option"]["net_pnl"] for t in trades if t.get("option") and t["option"].get("net_pnl") is not None]
    points = [((t["exit_price"] - t["entry_price"]) * (1 if t["direction"] == "LONG" else -1)) for t in trades]
    by_year: dict[int, float] = defaultdict(float)
    for t in trades:
        if t.get("option") and t["option"].get("net_pnl") is not None:
            by_year[_year(t["entry_time"])] += t["option"]["net_pnl"]
    return {
        "id": cid,
        "name": strategy + " " + ", ".join(f"{k}={v}" for k, v in params.items()),
        "trades": len(trades),
        "win_rate": idx.get("win_rate"),
        "pts_per_trade": sum(points) / len(points) if points else 0.0,
        "index_net": idx.get("net_pnl"),
        "option_net": opt.get("net_pnl"),
        "option_pf": _pf(option_nets),
        "option_dd": opt.get("max_drawdown"),
        "option_per_trade": (sum(option_nets) / len(option_nets)) if option_nets else None,
        "years": dict(sorted(by_year.items())),
        "warnings": out.get("warnings") or [],
    }


def _fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return str(x)


def table(rows: list[dict[str, Any]]) -> str:
    years = sorted({y for r in rows for y in r["years"]})
    head = ["id", "strategy", "trades", "win %", "pts/trade", "option net ₹", "option PF", "option max DD ₹",
            "₹/trade", *[str(y) for y in years]]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        win = r["win_rate"]
        win_s = "-" if win is None else f"{(win * 100 if win <= 1 else win):.1f}"
        cells = [r["id"], r["name"], str(r["trades"]), win_s, _fmt(r["pts_per_trade"]), _fmt(r["option_net"], 0),
                 _fmt(r["option_pf"]), _fmt(r["option_dd"], 0), _fmt(r["option_per_trade"], 0),
                 *[_fmt(r["years"].get(y), 0) for y in years]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare the pre-registered options candidates")
    parser.add_argument("--start", default="2022-01-03")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--only", default="", help="comma-separated ids, e.g. C2,C11")
    args = parser.parse_args(argv)
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    rows = []
    for cid, strategy, params in CANDIDATES:
        if wanted and cid not in wanted:
            continue
        config = parse_config({
            "strategy": strategy, "params": params, "symbol": "NIFTY50", "timeframe": args.timeframe,
            "start": args.start, "end": args.end, "sessions": ["normal"], "mode": "options",
        })
        print(f"{cid} {strategy} {params} ...", flush=True)
        out = execute_run(config, lambda _event: None, hash_data=False)
        rows.append(summarise(cid, strategy, params, out))
        r = rows[-1]
        print(f"   trades {r['trades']}, pts/trade {r['pts_per_trade']:.2f}, option net {_fmt(r['option_net'], 0)}, "
              f"PF {_fmt(r['option_pf'])}", flush=True)
    text = (f"# Options candidates, NIFTY50 {args.timeframe}, {args.start} to {args.end} (options mode, after costs)\n\n"
            + table(rows) + "\n")
    for r in rows:
        if r["warnings"]:
            text += f"\n{r['id']} warnings: " + "; ".join(r["warnings"])
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"candidates_{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
