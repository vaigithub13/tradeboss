"""Phase B dry-run: research strategies on HDFCBANK, RELIANCE, BANKNIFTY (2022-01..2024-12 only).

Prints trades, costs per trade, and sizing. Does NOT run the full universe or 2025+.

    uv run python -m scripts.phase_b_dry_run
    uv run python -m scripts.phase_b_dry_run --strategy dd_five_confirm --symbol HDFCBANK
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_run
from app.backtest.instruments import resolve_candle_symbol
from app.config import settings

SYMBOLS = ("HDFCBANK", "RELIANCE", "BANKNIFTY")
START, END = "2022-01-03", "2024-12-31"

# Strategy presets for dry-run mechanics (equity_intraday; BANKNIFTY uses index series as cash-like)
STRATEGIES: dict[str, dict[str, Any]] = {
    "dd_five_confirm": {
        "strategy": "dd_five_confirm",
        "params": {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
        "timeframe": "5m",
    },
    "dd_five_confirm_short": {
        "strategy": "dd_five_confirm",
        "params": {"mode": "short_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"},
        "timeframe": "5m",
    },
    "price_channel": {
        "strategy": "price_channel",
        "params": {"length": 20},
        "timeframe": "5m",
    },
    "ema_pivot_pullback": {
        "strategy": "ema_pivot_pullback",
        "params": {"mode": "long_short", "use_pivots": True},
        "timeframe": "5m",
    },
    "morning_star": {
        "strategy": "price_action",
        "params": {
            "pattern": "morning_star",
            "mode": "long_only",
            "level_atr": 0.15,
            "max_width_atr": 2.0,
        },
        "timeframe": "15m",
    },
    "evening_star": {
        "strategy": "price_action",
        "params": {
            "pattern": "evening_star",
            "mode": "short_only",
            "level_atr": 0.15,
            "max_width_atr": 2.0,
        },
        "timeframe": "15m",
    },
    "gold_trend_9": {
        "strategy": "gold_trend_9",
        "params": {"mode": "long_short"},
        "timeframe": "5m",
    },
    "gold_trend_14": {
        "strategy": "gold_trend_14",
        "params": {"mode": "long_short"},
        "timeframe": "5m",
    },
}


def _mode_for(symbol: str) -> str:
    # BANKNIFTY dry-run uses the index series with equity_intraday cost/sizing mechanics
    # (index_future history is incomplete before late-2024 expired FUT API coverage).
    return "equity_intraday"


def _run_one(name: str, symbol: str, *, max_print: int = 8) -> dict[str, Any]:
    preset = STRATEGIES[name]
    body = {
        **preset,
        "symbol": symbol,
        "start": START,
        "end": END,
        "sessions": ["normal"],
        "mode": _mode_for(symbol),
        "live_timing": True,
        "risk_rupees": 300,
        "notional_cap": 75_000,
        "lot_size": 1,
        "slippage_ticks": 1,
        "tick_size": 0.05,
    }
    cfg = parse_config(body)
    folder = resolve_candle_symbol(symbol, candles_dir=settings.candles_dir)
    print(f"\n=== {name} @ {symbol} ({folder}) {START}..{END} mode={cfg['mode']} ===", flush=True)
    out = execute_run(cfg, lambda _e: None, hash_data=False)
    view = out["result"]
    trades = view.get("trades") or []
    summary = view.get("summary") or {}
    index = summary.get("index") or summary
    print(
        f"trades={len(trades)} net={index.get('net_pnl')} "
        f"charges={index.get('total_charges')} slip={index.get('total_slippage')} "
        f"warnings={out.get('warnings')}",
        flush=True,
    )
    for i, t in enumerate(trades[:max_print]):
        print(
            f"  #{i+1} {t.get('side')} lots={t.get('lots')} units={t.get('units')} "
            f"entry={t.get('entry_price')} exit={t.get('exit_price')} "
            f"gross={t.get('gross_pnl')} charges={t.get('charges_total')} "
            f"net={t.get('net_pnl')} reason={t.get('exit_reason')}",
            flush=True,
        )
    if len(trades) > max_print:
        print(f"  ... {len(trades) - max_print} more", flush=True)
    return {
        "strategy": name,
        "symbol": symbol,
        "folder": folder,
        "trades": len(trades),
        "net_pnl": index.get("net_pnl"),
        "total_charges": index.get("total_charges"),
        "sample": trades[:max_print],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), help="run one strategy only")
    ap.add_argument("--symbol", choices=list(SYMBOLS), help="run one symbol only")
    ap.add_argument("--json-out", type=str, default="", help="optional summary json path under data/validation")
    args = ap.parse_args(argv)

    # Refuse holdout overlap: end is 2024-12-31, holdout is 2026 — OK
    assert date.fromisoformat(END) < date(2026, 7, 1)

    names = [args.strategy] if args.strategy else list(STRATEGIES)
    symbols = [args.symbol] if args.symbol else list(SYMBOLS)
    rows = []
    for name in names:
        for sym in symbols:
            try:
                rows.append(_run_one(name, sym))
            except Exception as exc:
                print(f"ERROR {name}@{sym}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                rows.append({"strategy": name, "symbol": sym, "error": str(exc)})
    if args.json_out:
        path = args.json_out
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"as_of": "2026-11-21", "window": [START, END], "runs": rows}, f, indent=2, default=str)
            f.write("\n")
        print(f"wrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
