"""Slot 4 as it runs live, next to slot 2. Research only. No orders, no slot changes.

    cd backend && uv run python -m scripts.slot4_deploy_check

Nifty options, price channel length 20, 5m, ATM weekly, live timing, slippage 0.2 a side,
dated costs and lots. Slot 4 adds the premium exit (−20% / +40%). Real premiums from
2024-10-03; earlier fills use the shipped model. The holdout is opened through the
holdout action, which records a peek. This does not edit paper slots.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.catalog import NIFTY_SYMBOL, parse_config
from app.backtest.execute import execute_run
from app.backtest.metrics import TradeRecord
from app.backtest.walkforward import parse_holdout
from app.config import settings
from app.data.sessions import DEFAULT_INCLUDED_SESSION_TYPES
from app.paper.deploy_stats import by_year, summarise

IST = timezone(timedelta(hours=5, minutes=30))
# A weekday run must be finished before the cash open. 08:30 is the cutoff the check was given.
CUTOFF_MINUTES = 8 * 60 + 30
SESSION_BLOCK = (9 * 60, 16 * 60 + 10)

WINDOWS = (
    ("2022-2024", date(2022, 1, 1), date(2024, 12, 31), False),
    ("2025-H1 2026", date(2025, 1, 1), date(2026, 6, 30), False),
    ("holdout", date(2026, 7, 1), date(2026, 10, 1), True),
)
SLOTS = (
    ("slot 4", "premium_1to2"),
    ("slot 2", None),
)


def live_block(live: bool) -> str | None:
    if live:
        return "LIVE_TRADING is true. The slot check does not run."
    return None


def clock_block(now: datetime) -> str | None:
    """Weekday 08:30 through 16:10 IST. The check has to be done before the open."""
    local = now.astimezone(IST)
    if local.weekday() >= 5:
        return None
    minutes = local.hour * 60 + local.minute
    if CUTOFF_MINUTES <= minutes <= SESSION_BLOCK[1]:
        return "The slot check does not run from 08:30 through 16:10 IST."
    return None


def slot_body(exit_rule: str | None, start: date, end: date) -> dict[str, Any]:
    """The settings the paper check uses for this slot, on one date range."""
    body: dict[str, Any] = {
        "strategy": "price_channel",
        "params": {"length": 20},
        "symbol": NIFTY_SYMBOL,
        "timeframe": "5m",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": list(DEFAULT_INCLUDED_SESSION_TYPES),
        "mode": "options",
        "strike_offset": 0,
        "slippage_points": 0.2,
        "live_timing": True,
        "option_fill": "delta_adjusted",
    }
    if exit_rule is not None:
        body["exit_rule"] = exit_rule
    return body


def option_records(view: dict[str, Any]) -> list[TradeRecord]:
    records = []
    for row in view["trades"]:
        option = row.get("option")
        if not option or option.get("net_pnl") is None:
            continue
        exit_time = int(option.get("exit_time") or row["exit_time"])
        records.append(TradeRecord(int(row["entry_time"]), exit_time, float(option["net_pnl"])))
    return records


def _run(name: str, exit_rule: str | None, start: date, end: date, holdout: bool) -> dict[str, Any]:
    body = slot_body(exit_rule, start, end)
    config = parse_holdout(body) if holdout else parse_config(body)
    print(f"{name} {start.isoformat()}..{end.isoformat()} {'holdout' if holdout else 'research'}", flush=True)

    def report(event: dict[str, Any]) -> None:
        if event.get("phase") == "overlay" and event.get("done") in (1, event.get("total")):
            print(f"  overlay {event['done']}/{event['total']}", flush=True)

    payload = execute_run(config, report, hash_data=False)
    view = payload["result"]
    records = option_records(view)
    option = view["summary"]["option"] or {}
    return {
        "name": name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "holdout": holdout,
        "stats": summarise(records),
        "years": by_year(records),
        "real_trades": option.get("real_trades"),
        "modelled_trades": option.get("modelled_trades"),
        "real_pnl": option.get("real_pnl"),
        "modelled_pnl": option.get("modelled_pnl"),
        "warnings": payload.get("warnings") or [],
    }


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{100.0 * float(value):.1f}%"


def _inr(value: Any) -> str:
    if value is None:
        return "—"
    return f"₹{float(value):,.0f}"


def _pf(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.2f}"


def _row(label: str, stats: dict[str, Any]) -> str:
    streak = stats["streak"]
    month = stats["worst_month"] or {}
    return (
        f"| {label} | {stats['trades']} | {_pct(stats['win_rate'])} | {_inr(stats['net'])} | {_pf(stats['profit_factor'])} | "
        f"{_inr(stats['max_dd'])} | {streak['longest_trades']} / {_inr(streak['longest_rupees'])} | "
        f"{streak['deepest_trades']} / {_inr(streak['deepest_rupees'])} | "
        f"{month.get('month', '—')} {_inr(month.get('net'))} | {stats['dd_days']} |"
    )


def _report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Slot 4 deployment check, next to slot 2",
        "",
        "Research only. No order was sent and no paper slot was changed. Both slots are the price channel, "
        "length 20, on Nifty 5-minute bars, one lot, ATM weekly (strike offset 0), live timing, "
        "delta-adjusted fills, slippage 0.2 a side, and the dated cost and lot tables. "
        "Slot 4 also exits the option at −20% or +40% of the fill. Slot 2 does not. "
        "Premiums are the stored option tape from 2024-10-03 and the shipped model before that.",
        "",
        "The holdout 2026-07-01 through 2026-10-01 was opened through the holdout action, so each slot "
        "recorded one peek. That is a deployment look, not a new research window.",
        "",
        "| Book | Trades | Win | Net | PF | Max DD | Longest streak (trades / ₹) | Deepest streak (trades / ₹) | Worst month | DD days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in rows:
        lines.append(_row(f"{row['name']} {row['start'][:4]}–{row['end'][:7]}", row["stats"]))
    lines.append("")
    for row in rows:
        lines.append(f"## {row['name']} {row['start']}..{row['end']}")
        lines.append("")
        lines.append(
            f"Real trades {row['real_trades']} ({_inr(row['real_pnl'])}), "
            f"modelled trades {row['modelled_trades']} ({_inr(row['modelled_pnl'])})."
        )
        lines.append("")
        lines.append(
            "| Year | Trades | Win | Net | PF | Max DD | Longest streak | Deepest streak | Worst month | DD days |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---:|")
        for year in row["years"]:
            streak = year["streak"]
            month = year["worst_month"] or {}
            lines.append(
                f"| {year['year']} | {year['trades']} | {_pct(year['win_rate'])} | {_inr(year['net'])} | "
                f"{_pf(year['profit_factor'])} | {_inr(year['max_dd'])} | "
                f"{streak['longest_trades']} / {_inr(streak['longest_rupees'])} | "
                f"{streak['deepest_trades']} / {_inr(streak['deepest_rupees'])} | "
                f"{month.get('month', '—')} {_inr(month.get('net'))} | {year['dd_days']} |"
            )
        lines.append("")
    lines.append("A drawdown day-count runs from the exit that set the peak to the exit that makes a new peak.")
    lines.append("A year's drawdown starts again at zero. A trade at exactly ₹0 ends a losing streak and is not a win.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    del argv
    blocked = live_block(settings.live_trading)
    if blocked:
        print(blocked, file=sys.stderr)
        return 2
    if clock_block(datetime.now(IST)):
        print(clock_block(datetime.now(IST)), file=sys.stderr)
        return 2
    rows: list[dict[str, Any]] = []
    # Research windows first. The holdout peek is recorded only after those have run.
    ordered = [item for item in WINDOWS if not item[3]] + [item for item in WINDOWS if item[3]]
    for name, exit_rule in SLOTS:
        for _label, start, end, holdout in ordered:
            if clock_block(datetime.now(IST)):
                print(clock_block(datetime.now(IST)), file=sys.stderr)
                return 2
            rows.append(_run(name, exit_rule, start, end, holdout))
    text = _report(rows)
    out = settings.data_dir / "validation" / "slot4_deploy_2026-11-27.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
