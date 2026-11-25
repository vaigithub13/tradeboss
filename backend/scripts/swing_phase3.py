"""Swing Phase 3 worker. Research only. DEV, OOS, and forward. Holdout stays sealed.

    cd backend && uv run python -m scripts.swing_phase3

The gate uses one book chosen before the run: ₹50,000, 5 positions, Upstox
brokerage. Other books are reported and do not get a separate pass. 2019+ is
in this research window. The holdout, 2026-07-01 through 2026-10-01, is not.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from app.config import settings
from app.data.history import read_parquet, symbol_dir_name
from app.swing.bhav_pit import market_hours_block
from app.swing.daily import IST
from app.swing.delivery import DeliveryTable
from app.swing.fixes import as_date, blocked_entries, event_flags, research_prices
from app.swing.quality import series_path
from app.swing.research import (
    PRIMARY_BROKERAGE,
    PRIMARY_CAPITAL,
    PRIMARY_SLOTS,
    BookSpec,
    bar_lookup,
    cliff_labels,
    index_hold,
    judge,
    param_grid,
    pnl_without_top_winners,
    random_signals,
    research_windows,
    run_research,
)
from app.swing.strategies import DEFAULTS, NAMES, make_signals, prepare_tape, week_end_dates
from scripts.swing_phase2 import UNIVERSE_PATH, _substitute

NIFTY = "NSE_INDEX|Nifty 500"
KINDS = ("S1", "S2", "S3", "S4", "S5")
CAPITALS = (Decimal("50000"), Decimal("200000"))
SLOTS = (3, 5)
BROKERAGES = ("upstox", "zero")


def live_block(live: bool) -> str | None:
    if live:
        return "LIVE_TRADING is true. Swing Phase 3 does not run."
    return None


def main(argv: list[str] | None = None) -> int:
    del argv
    blocked = live_block(settings.live_trading)
    if blocked:
        print(blocked, file=sys.stderr)
        return 2
    if market_hours_block(datetime.now(IST)):
        print("Swing Phase 3 does not run during 09:00–16:10 IST.", file=sys.stderr)
        return 2
    universe = settings.bhavcopy_dir / "universe.parquet"
    if not universe.is_file():
        print("bhavcopy universe is missing.", file=sys.stderr)
        return 1
    nifty = _nifty_frame()
    if nifty.empty:
        print("Nifty 500 daily bars are missing.", file=sys.stderr)
        return 1
    latest = min(as_date(nifty["date"].max()), _last_session(universe))
    windows = research_windows(latest)
    print(f"windows { {k: (a.isoformat(), b.isoformat()) for k, (a, b) in windows.items()} }", flush=True)

    print("loading bars", flush=True)
    bars = _load(universe, date(2015, 1, 1), latest)
    bars = research_prices(bars)
    stocks = json.loads(Path(UNIVERSE_PATH).read_text())["stocks"]
    replaced, examined, _skipped = _substitute(bars, stocks, universe)
    print(f"substituted {len(replaced)} of {examined}", flush=True)
    calendar = _calendar(universe, date(2015, 1, 1), latest)
    tape = prepare_tape(bars, pd.Series(nifty["close"].to_numpy(), index=pd.Index(list(nifty["date"]))))
    print(f"tape {len(tape)} lineages {tape['lineage'].nunique()}", flush=True)
    flags = event_flags(tape)
    lookup = bar_lookup(tape)
    ends = week_end_dates(calendar)
    costs = DeliveryTable.load()
    signals = {}
    for kind in KINDS:
        print(f"signals {kind}", flush=True)
        signals[kind] = make_signals(kind, tape, ends)
        print(f"  {len(signals[kind])}", flush=True)

    books: dict[tuple, dict] = {}
    for window, (start, end) in windows.items():
        for capital in CAPITALS:
            for slots in SLOTS:
                for brokerage in BROKERAGES:
                    spec = BookSpec(capital, slots, start, end, brokerage)
                    key = (window, str(capital), slots, brokerage)
                    books[key] = {}
                    for kind in KINDS:
                        result = run_research(
                            tape, _clip(signals[kind], calendar, start, end), costs, spec,
                            calendar=calendar, flags=flags, week_ends=ends, lookup=lookup,
                        )
                        books[key][kind] = result
                        print(
                            f"{window} {kind} c{capital} n{slots} {brokerage}: "
                            f"{result['metrics']['trades']} trades pnl {result['metrics']['pnl']:.0f}",
                            flush=True,
                        )

    primary = {}
    gates = {}
    extras = {}
    nifty_stats = {name: index_hold(nifty, start, end, float(PRIMARY_CAPITAL)) for name, (start, end) in windows.items()}
    pool = _pool(tape)
    blocked_entries_set = blocked_entries(flags, calendar, 2)
    prev_of = {calendar[i]: calendar[i - 1] for i in range(1, len(calendar))}
    for kind in KINDS:
        oos = books[("OOS", str(PRIMARY_CAPITAL), PRIMARY_SLOTS, PRIMARY_BROKERAGE)][kind]
        dev = books[("DEV", str(PRIMARY_CAPITAL), PRIMARY_SLOTS, PRIMARY_BROKERAGE)][kind]
        primary[kind] = {"DEV": dev, "OOS": oos}
        print(f"sensitivity {kind}", flush=True)
        variants = _sensitivity(kind, tape, ends, costs, calendar, flags, lookup, windows["OOS"])
        print(f"random {kind}", flush=True)
        seeds = _random_cagrs(kind, tape, oos["trades"], pool, blocked_entries_set, prev_of, costs, calendar, flags, ends, lookup)
        top10 = pnl_without_top_winners(oos["trades"]["pnl"].tolist() if not oos["trades"].empty else [])
        cliffs = cliff_labels(oos["metrics"]["cagr"], oos["metrics"]["pnl"], variants)
        gates[kind] = judge(dev["metrics"], oos["metrics"], nifty_stats["OOS"], seeds, top10, cliffs)
        extras[kind] = {"variants": variants, "seeds": seeds, "top10": top10, "cliffs": cliffs}
        print(f"gate {kind} {'PASS' if gates[kind]['pass'] else 'FAIL'}", flush=True)

    problems = _sanity(books)
    passers = [kind for kind in KINDS if gates[kind]["pass"]]
    passers = sorted(passers, key=lambda kind: -primary[kind]["OOS"]["metrics"]["cagr"])[:2]
    holdout = _holdout(passers, tape, ends, costs, calendar, flags, lookup, signals)
    text = _report(books, primary, gates, extras, nifty_stats, replaced, flags, problems, holdout, latest)
    out = settings.data_dir / "swing_phase3"
    out.mkdir(parents=True, exist_ok=True)
    report = settings.data_dir / "validation" / "swing_phase3_2026-11-26.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text)
    (out / "summary.json").write_text(json.dumps(_jsonable(gates, primary, nifty_stats, holdout), indent=2, default=str))
    print(text)
    print(f"wrote {report}")
    return 1 if problems else 0


def _clip(signals: pd.DataFrame, calendar: list[date], start: date, end: date) -> pd.DataFrame:
    if signals.empty:
        return signals
    nxt = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    entry = signals["date"].map(lambda day: nxt.get(as_date(day)))
    keep = entry.map(lambda day: day is not None and start <= day <= end)
    return signals.loc[keep.to_numpy()].copy()


def _pool(tape: pd.DataFrame) -> pd.DataFrame:
    mask = tape["passes"].to_numpy(dtype=bool) & tape["regime"].to_numpy(dtype=bool)
    atr = tape["atr"].to_numpy(dtype=float)
    mask = mask & np.isfinite(atr) & (atr > 0)
    return tape.loc[mask, ["date", "lineage", "symbol", "size", "atr", "px_low"]].copy()


def _sensitivity(kind, tape, ends, costs, calendar, flags, lookup, window):
    start, end = window
    spec = BookSpec(PRIMARY_CAPITAL, PRIMARY_SLOTS, start, end, PRIMARY_BROKERAGE)
    found = []
    for label, params in param_grid(DEFAULTS[kind]):
        generated = make_signals(kind, tape, ends, params)
        result = run_research(
            tape, _clip(generated, calendar, start, end), costs, spec,
            calendar=calendar, flags=flags, week_ends=ends, lookup=lookup,
        )
        found.append((label, result["metrics"]["cagr"], result["metrics"]["pnl"]))
    return found


def _random_cagrs(kind, tape, trades, pool, blocked, prev_of, costs, calendar, flags, ends, lookup) -> list[float]:
    if trades.empty:
        return [0.0] * 20
    counts: dict[date, int] = {}
    for day in trades["entry_date"]:
        stamp = date.fromisoformat(day) if isinstance(day, str) else as_date(day)
        counts[stamp] = counts.get(stamp, 0) + 1
    spec = BookSpec(PRIMARY_CAPITAL, PRIMARY_SLOTS, date(2022, 1, 1), date(2026, 6, 30), PRIMARY_BROKERAGE)
    out = []
    for seed in range(1, 21):
        generated = random_signals(list(counts.items()), pool, seed, kind, prev_of, blocked)
        result = run_research(
            tape, generated, costs, spec, calendar=calendar, flags=flags, week_ends=ends, lookup=lookup,
        )
        out.append(result["metrics"]["cagr"])
    return out


def _holdout(passers, tape, ends, costs, calendar, flags, lookup, signals) -> dict:
    marker = settings.data_dir / "swing_phase3" / "holdout_used.json"
    if not passers:
        return {"opened": False, "reason": "no strategy passed the gate"}
    if marker.is_file():
        return {"opened": False, "reason": "holdout was already used", "saved": json.loads(marker.read_text())}
    spec = BookSpec(PRIMARY_CAPITAL, PRIMARY_SLOTS, date(2026, 7, 1), date(2026, 10, 1), PRIMARY_BROKERAGE, holdout=True)
    done = {}
    for kind in passers:
        result = run_research(
            tape, _clip(signals[kind], calendar, spec.start, spec.end), costs, spec,
            calendar=calendar, flags=flags, week_ends=ends, lookup=lookup,
        )
        done[kind] = result["metrics"]
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {"strategies": passers, "metrics": done}
    marker.write_text(json.dumps(payload, indent=2, default=str))
    return {"opened": True, "strategies": passers, "metrics": done}


def _sanity(books: dict) -> list[str]:
    problems = []
    for (window, _capital, _slots, _brokerage), by_kind in books.items():
        for kind, result in by_kind.items():
            trades = result["trades"]
            if trades.empty:
                continue
            if (trades["entry_date"] <= trades["signal_date"]).any():
                problems.append(f"{window} {kind} filled on the signal day")
            if window != "HOLDOUT" and ((trades["entry_date"] >= "2026-07-01") & (trades["entry_date"] <= "2026-10-01")).any():
                problems.append(f"{window} {kind} entered during the holdout")
    return problems


def _report(books, primary, gates, extras, nifty_stats, replaced, flags, problems, holdout, latest) -> str:
    lines = [
        "# Swing Phase 3",
        "",
        "Research only. No order was sent. The gate is the ₹50,000, 5-position, Upstox-brokerage book.",
        "Other capital, slot, and brokerage combinations are reported and were not used to pick a pass.",
        f"Nifty 500 total-return index is not in the stored daily bars. The benchmark is the price index, bought at the first open and held to the last close, with no costs.",
        f"Forward runs through {latest.isoformat()}. Holdout 2026-07-01 through 2026-10-01 was not a research window.",
        "",
        f"Upstox substitutions applied: {len(replaced)}. Event-guard sessions: {len(flags)}.",
        "",
        "## Gate",
        "",
        "| Strategy | DEV CAGR | DEV DD | OOS CAGR | OOS DD | OOS PF | Trades | Win | Avg trade | Cost | Nifty OOS | Random | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    nifty_oos = nifty_stats.get("OOS") or {}
    for kind in KINDS:
        dev = primary[kind]["DEV"]["metrics"]
        oos = primary[kind]["OOS"]["metrics"]
        gate = gates[kind]
        lines.append(
            f"| {kind} {NAMES[kind]} | {_pct(dev['cagr'])} | {_pct(dev['max_dd'])} | {_pct(oos['cagr'])} | "
            f"{_pct(oos['max_dd'])} | {_num(oos['pf'])} | {oos['trades']} | {_pct(oos['win'])} | "
            f"{_pct(oos['avg_trade_pct'])} | {_pct(oos['cost_pct'])} | {_pct(nifty_oos.get('cagr'))} | "
            f"{_pct(gate['random_hit'])} | {'PASS' if gate['pass'] else 'FAIL'} |"
        )
    lines += ["", "### Why", ""]
    for kind in KINDS:
        reason = "all conditions met" if gates[kind]["pass"] else "; ".join(gates[kind]["reasons"])
        lines.append(f"- {kind}: {reason}")
    lines += ["", "## Small and large, primary book", ""]
    lines.append("| Strategy | Window | Large trades | Large P&L | Large avg | Small trades | Small P&L | Small avg |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for kind in KINDS:
        for window in ("DEV", "OOS"):
            rows = {row["size"]: row for row in primary[kind][window]["metrics"]["by_size"]}
            large = rows.get("large", {})
            small = rows.get("small", {})
            lines.append(
                f"| {kind} | {window} | {large.get('trades', 0)} | {_rs(large.get('pnl', 0))} | {_pct(large.get('avg_trade_pct'))} | "
                f"{small.get('trades', 0)} | {_rs(small.get('pnl', 0))} | {_pct(small.get('avg_trade_pct'))} |"
            )
    lines += ["", "## Primary book by year, OOS", ""]
    for kind in KINDS:
        lines.append(f"### {kind}")
        lines.append("")
        lines.append(_year_table(primary[kind]["OOS"]["metrics"]["by_year"]))
        lines.append("")
    lines += ["## All books", ""]
    lines.append("| Window | Strategy | Capital | Slots | Brokerage | CAGR | Max DD | PF | Trades | Win | Avg | Cost | Exposure | P&L |")
    lines.append("|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for key, by_kind in books.items():
        window, capital, slots, brokerage = key
        for kind, result in by_kind.items():
            m = result["metrics"]
            lines.append(
                f"| {window} | {kind} | {capital} | {slots} | {brokerage} | {_pct(m['cagr'])} | {_pct(m['max_dd'])} | "
                f"{_num(m['pf'])} | {m['trades']} | {_pct(m['win'])} | {_pct(m['avg_trade_pct'])} | {_pct(m['cost_pct'])} | "
                f"{_pct(m['exposure'])} | {_rs(m['pnl'])} |"
            )
    lines += ["", "## Nifty 500 price index", ""]
    for name, stats in nifty_stats.items():
        if stats is None:
            lines.append(f"- {name}: no bars")
        else:
            lines.append(f"- {name}: CAGR {_pct(stats['cagr'])}, max DD {_pct(stats['max_dd'])}, end ₹{stats['end_equity']:.0f}")
    lines += ["", "## Holdout", ""]
    if holdout.get("opened"):
        for kind, metrics in holdout["metrics"].items():
            lines.append(f"- {kind}: CAGR {_pct(metrics['cagr'])}, P&L {_rs(metrics['pnl'])}, trades {metrics['trades']}")
    else:
        lines.append(holdout.get("reason", "not opened"))
    if problems:
        lines += ["", "## Sanity", ""]
        lines.extend(f"- {item}" for item in problems)
    lines.append("")
    return "\n".join(lines)


def _year_table(rows: list[dict]) -> str:
    if not rows:
        return "(none)"
    lines = ["| Year | Trades | P&L | End equity | Exposure |", "|---|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['year']} | {row['trades']} | {_rs(row['pnl'])} | {_rs(row['end_equity'])} | {_pct(row['exposure'])} |")
    return "\n".join(lines)


def _pct(value) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _rs(value) -> str:
    return f"₹{float(value):,.0f}"


def _num(value) -> str:
    if value is None or value == float("inf"):
        return "inf"
    return f"{float(value):.2f}"


def _jsonable(gates, primary, nifty_stats, holdout) -> dict:
    out = {"gates": gates, "nifty": nifty_stats, "holdout": {k: v for k, v in holdout.items() if k != "metrics"}, "primary": {}}
    for kind, windows in primary.items():
        out["primary"][kind] = {name: result["metrics"] for name, result in windows.items()}
    if holdout.get("metrics"):
        out["holdout"]["metrics"] = holdout["metrics"]
    return out


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


def _load(path: Path, start: date, end: date) -> pd.DataFrame:
    quoted = _quote(path)
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"""
            SELECT CAST(date AS DATE) AS date, symbol, isin, lineage,
                   open, high, low, close, volume, turnover,
                   adj_open, adj_high, adj_low, adj_close, passes, size
            FROM read_parquet('{quoted}')
            WHERE date >= DATE '{start.isoformat()}' AND date <= DATE '{end.isoformat()}'
              AND lineage IN (
                SELECT DISTINCT lineage FROM read_parquet('{quoted}')
                WHERE passes AND date >= DATE '2016-01-01' AND date <= DATE '{end.isoformat()}'
              )
            """
        ).df()
    finally:
        con.close()
    frame["date"] = frame["date"].map(as_date)
    frame["lineage"] = frame["lineage"].astype(str)
    return frame


def _calendar(path: Path, start: date, end: date) -> list[date]:
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"""
            SELECT DISTINCT CAST(date AS DATE) AS d FROM read_parquet('{_quote(path)}')
            WHERE date >= DATE '{start.isoformat()}' AND date <= DATE '{end.isoformat()}'
            ORDER BY d
            """
        ).fetchall()
    finally:
        con.close()
    return [as_date(row[0]) for row in rows]


def _last_session(path: Path) -> date:
    con = duckdb.connect()
    try:
        row = con.execute(f"SELECT MAX(CAST(date AS DATE)) FROM read_parquet('{_quote(path)}')").fetchone()
    finally:
        con.close()
    return as_date(row[0])


def _nifty_frame() -> pd.DataFrame:
    path = series_path(settings.candles_daily_dir / symbol_dir_name(NIFTY))
    if not path.is_file():
        return pd.DataFrame()
    frame = read_parquet(path)
    dates = list(pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date)
    return pd.DataFrame({"date": dates, "open": frame["open"].astype(float).to_numpy(), "close": frame["close"].astype(float).to_numpy()})


if __name__ == "__main__":
    sys.exit(main())
