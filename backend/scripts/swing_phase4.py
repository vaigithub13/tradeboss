"""Swing Phase 4 worker. Research only. Monthly momentum, no orders.

    cd backend && uv run python -m scripts.swing_phase4

The gate is four books chosen before the run: M1, M1 with the regime filter,
M2, and M2 with the regime filter. Each is ₹50,000, 10 names, Upstox brokerage.
The ₹2,00,000 book and the ₹0 brokerage book are reported and are not extra
attempts. Holdout 2026-07-01 through 2026-09-30 stays sealed unless one of
those four passes, and then at most two are opened, once.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd

from app.config import settings
from app.data.history import read_parquet, symbol_dir_name
from app.swing.bhav_pit import market_hours_block
from app.swing.daily import IST
from app.swing.delivery import DeliveryTable
from app.swing.engine import nifty_above
from app.swing.fixes import as_date, blocked_entries, event_flags, research_prices
from app.swing.momentum import (
    DEFAULTS,
    DEV_START,
    EW_CAPITAL,
    HOLDOUT_END,
    HOLDOUT_START,
    NAMES,
    OOS_END,
    OOS_START,
    PRIMARY_BROKERAGE,
    PRIMARY_CAPITAL,
    PRIMARY_N,
    RANDOM_SEEDS,
    REGIME_SMA,
    MonthSpec,
    eligible_panel,
    fair_market_values,
    index_path,
    judge,
    month_end_dates,
    prepare,
    random_panel,
    research_windows,
    run_month,
    score_panel,
    variant_grid,
)
from app.swing.quality import series_path
from app.swing.research import cliff_labels, pnl_without_top_winners
from app.swing.tax import TaxTable
from scripts.swing_phase2 import UNIVERSE_PATH, _substitute

NIFTY = "NSE_INDEX|Nifty 500"
KINDS = ("M1", "M1R", "M2", "M2R")
CAPITALS = (Decimal("50000"), Decimal("200000"))
BROKERAGES = ("upstox", "zero")


def live_block(live: bool) -> str | None:
    if live:
        return "LIVE_TRADING is true. Swing Phase 4 does not run."
    return None


def main(argv: list[str] | None = None) -> int:
    del argv
    blocked_reason = live_block(settings.live_trading)
    if blocked_reason:
        print(blocked_reason, file=sys.stderr)
        return 2
    if market_hours_block(datetime.now(IST)):
        print("Swing Phase 4 does not run during 09:00–16:10 IST.", file=sys.stderr)
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
    bars = bars.sort_values(["lineage", "date"])
    bars["prev_close"] = bars.groupby("lineage", sort=False)["px_close"].shift(1)
    calendar = _calendar(universe, date(2015, 1, 1), latest)
    ends = month_end_dates(calendar)
    print("scores", flush=True)
    panels = {
        "M1": score_panel(bars, "M1", ends),
        "M2": score_panel(bars, "M2", ends),
    }
    universe_panel = eligible_panel(bars, ends)
    print(f"M1 rows {len(panels['M1'])} M2 rows {len(panels['M2'])} universe {len(universe_panel)}", flush=True)
    flags = event_flags(bars)
    flag_set = {(str(a), as_date(b)) for a, b in zip(flags["lineage"], flags["date"], strict=False)}
    blocked = blocked_entries(flags, calendar, 2)
    fmv = fair_market_values(bars)
    lookup, last_known = prepare(bars)
    closes = pd.Series(nifty["close"].to_numpy(), index=pd.Index([as_date(day) for day in nifty["date"]]))
    regime = nifty_above(closes, REGIME_SMA)
    costs = DeliveryTable.load()
    taxes = TaxTable.load()
    print(f"lookup {len(lookup)} regime days {sum(regime.values())}", flush=True)

    books: dict[tuple, dict] = {}
    for window, (start, end) in windows.items():
        for capital in CAPITALS:
            for brokerage in BROKERAGES:
                books[(window, str(capital), brokerage)] = {}
                for kind in KINDS:
                    result = _run(
                        bars, panels["M1" if kind.startswith("M1") else "M2"], costs, taxes,
                        MonthSpec(capital, PRIMARY_N, start, end, brokerage, regime=kind.endswith("R")),
                        calendar, regime, flag_set, blocked, fmv, lookup, last_known,
                    )
                    books[(window, str(capital), brokerage)][kind] = result
                    m = result["metrics"]
                    print(
                        f"{window} {kind} {capital} {brokerage}: cagr {m['cagr']:.3f} tax {m['cagr_tax']:.3f} "
                        f"dd {m['max_dd']:.3f} trades {m['trades']}",
                        flush=True,
                    )

    baskets = {}
    for window, (start, end) in windows.items():
        for brokerage in BROKERAGES:
            spec = MonthSpec(
                EW_CAPITAL, 1, start, end, brokerage, mode="equal", fractional=True, min_notional=Decimal("0"),
            )
            baskets[(window, brokerage)] = _run(
                bars, universe_panel, costs, taxes, spec, calendar, regime, flag_set, blocked, fmv, lookup, last_known,
            )
            m = baskets[(window, brokerage)]["metrics"]
            print(f"equal-weight {window} {brokerage}: cagr {m['cagr']:.3f} tax {m['cagr_tax']:.3f}", flush=True)

    nifty_stats = {}
    for window, (start, end) in windows.items():
        for brokerage in BROKERAGES:
            nifty_stats[(window, brokerage)] = index_path(nifty, start, end, PRIMARY_CAPITAL, costs, taxes, brokerage)
            stats = nifty_stats[(window, brokerage)]
            print(f"nifty {window} {brokerage}: {None if stats is None else round(stats['cagr'], 3)}", flush=True)

    primary = {}
    gates = {}
    extras = {}
    for kind in KINDS:
        key = ("OOS", str(PRIMARY_CAPITAL), PRIMARY_BROKERAGE)
        dev = books[("DEV", str(PRIMARY_CAPITAL), PRIMARY_BROKERAGE)][kind]
        oos = books[key][kind]
        primary[kind] = {"DEV": dev, "OOS": oos, "FORWARD": books[("FORWARD", str(PRIMARY_CAPITAL), PRIMARY_BROKERAGE)][kind]}
        print(f"sensitivity {kind}", flush=True)
        variants = _sensitivity(kind, bars, ends, costs, taxes, calendar, regime, flag_set, blocked, fmv, lookup, last_known)
        print(f"random {kind}", flush=True)
        seeds = _random(kind, bars, universe_panel, costs, taxes, calendar, regime, flag_set, blocked, fmv, lookup, last_known)
        top10 = pnl_without_top_winners(oos["trades"]["pnl"].tolist() if not oos["trades"].empty else [])
        cliffs = cliff_labels(oos["metrics"]["cagr"], oos["metrics"]["pnl"], [(label, cagr, pnl) for label, cagr, pnl in variants])
        basket = baskets[("OOS", PRIMARY_BROKERAGE)]["metrics"]
        gates[kind] = judge(dev["metrics"], oos["metrics"], nifty_stats[("OOS", PRIMARY_BROKERAGE)], basket, [row["cagr"] for row in seeds], cliffs)
        extras[kind] = {"variants": variants, "seeds": seeds, "top10": top10, "cliffs": cliffs}
        print(f"gate {kind} {'PASS' if gates[kind]['pass'] else 'FAIL'} {gates[kind]['reasons']}", flush=True)

    problems = _sanity(books, baskets)
    passers = [kind for kind in KINDS if gates[kind]["pass"] and not problems]
    passers = sorted(passers, key=lambda kind: -primary[kind]["OOS"]["metrics"]["cagr"])[:2]
    if problems:
        holdout = {"opened": False, "reason": "sanity failed, holdout not opened"}
    else:
        holdout = _holdout(passers, panels, costs, taxes, calendar, regime, flag_set, blocked, fmv, lookup, last_known, bars)
    text = _report(books, baskets, primary, gates, extras, nifty_stats, replaced, flags, problems, holdout, latest)
    out = settings.data_dir / "swing_phase4"
    out.mkdir(parents=True, exist_ok=True)
    report = settings.data_dir / "validation" / "swing_phase4_2026-11-26.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text)
    (out / "summary.json").write_text(json.dumps(_jsonable(gates, primary, baskets, nifty_stats, extras, holdout), indent=2, default=str))
    print(text)
    print(f"wrote {report}")
    return 1 if problems else 0


def _run(bars, scores, costs, taxes, spec, calendar, regime, flags, blocked, fmv, lookup, last_known) -> dict:
    return run_month(
        bars, scores, costs, taxes, spec, calendar,
        regime=regime if spec.regime else None,
        flags=flags, blocked=blocked, fmv=fmv, lookup=lookup, last_known=last_known,
    )


def _sensitivity(kind, bars, ends, costs, taxes, calendar, regime, flags, blocked, fmv, lookup, last_known):
    spec = MonthSpec(PRIMARY_CAPITAL, PRIMARY_N, OOS_START, OOS_END, PRIMARY_BROKERAGE, regime=kind.endswith("R"))
    found = []
    for label, params in variant_grid(kind):
        panel = score_panel(bars, "M1" if kind.startswith("M1") else "M2", ends, params)
        result = _run(bars, panel, costs, taxes, spec, calendar, regime, flags, blocked, fmv, lookup, last_known)
        found.append((label, result["metrics"]["cagr"], result["metrics"]["pnl"]))
        print(f"  {kind} {label} cagr {result['metrics']['cagr']:.3f}", flush=True)
    return found


def _random(kind, bars, panel, costs, taxes, calendar, regime, flags, blocked, fmv, lookup, last_known) -> list[dict]:
    spec = MonthSpec(PRIMARY_CAPITAL, PRIMARY_N, OOS_START, OOS_END, PRIMARY_BROKERAGE, regime=kind.endswith("R"))
    rows = []
    for seed in RANDOM_SEEDS:
        result = _run(
            bars, random_panel(panel, seed), costs, taxes, spec, calendar, regime, flags, blocked, fmv, lookup, last_known,
        )
        rows.append({"seed": seed, "cagr": result["metrics"]["cagr"], "cagr_tax": result["metrics"]["cagr_tax"]})
    print(f"  {kind} random median {sorted(row['cagr'] for row in rows)[len(rows) // 2]:.3f}", flush=True)
    return rows


def _holdout(passers, panels, costs, taxes, calendar, regime, flags, blocked, fmv, lookup, last_known, bars) -> dict:
    marker = settings.data_dir / "swing_phase4" / "holdout_used.json"
    if not passers:
        return {"opened": False, "reason": "no book passed the gate"}
    if marker.is_file():
        return {"opened": False, "reason": "holdout was already used", "saved": json.loads(marker.read_text())}
    done = {}
    for kind in passers:
        spec = MonthSpec(
            PRIMARY_CAPITAL, PRIMARY_N, HOLDOUT_START, HOLDOUT_END, PRIMARY_BROKERAGE,
            regime=kind.endswith("R"), holdout=True,
        )
        result = _run(
            bars, panels["M1" if kind.startswith("M1") else "M2"], costs, taxes, spec,
            calendar, regime, flags, blocked, fmv, lookup, last_known,
        )
        done[kind] = result["metrics"]
        print(f"holdout {kind} cagr {result['metrics']['cagr']:.3f}", flush=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {"books": passers, "metrics": done}
    marker.write_text(json.dumps(payload, indent=2, default=str))
    return {"opened": True, "books": passers, "metrics": done}


def _sanity(books: dict, baskets: dict) -> list[str]:
    problems = []
    start = HOLDOUT_START.isoformat()
    end = HOLDOUT_END.isoformat()
    for key, by_kind in list(books.items()) + [(("EW", window, brokerage), {"EW": result}) for (window, brokerage), result in baskets.items()]:
        window = key[0]
        for kind, result in by_kind.items():
            trades = result["trades"]
            daily = result["daily"]
            label = f"{window} {kind} {' '.join(str(part) for part in key[1:])}"
            if not daily.empty and (daily["cash"] < -0.01).any():
                problems.append(f"{label} cash went negative")
            if trades.empty:
                continue
            if (trades["entry_date"] <= trades["signal_date"]).any():
                problems.append(f"{label} filled on the signal day")
            if window != "HOLDOUT" and ((trades["entry_date"] >= start) & (trades["entry_date"] <= end)).any():
                problems.append(f"{label} entered during the holdout")
    return problems


def _report(books, baskets, primary, gates, extras, nifty_stats, replaced, flags, problems, holdout, latest) -> str:
    nifty = nifty_stats[("OOS", PRIMARY_BROKERAGE)] or {}
    basket = baskets[("OOS", PRIMARY_BROKERAGE)]["metrics"]
    lines = [
        "# Swing Phase 4",
        "",
        "Research only. No order was sent. The gate is four books: M1, M1 with the Nifty 500 regime filter,",
        "M2, and M2 with the regime filter. Each is ₹50,000, 10 names, Upstox delivery brokerage.",
        "The ₹2,00,000 book and the ₹0 brokerage book are reported and were not extra attempts.",
        "Nifty 500 total return is not in the stored bars. The benchmark is the price index, one delivery round trip, no slippage.",
        "The equal-weight benchmark holds every ₹10 crore name, fractional, no ₹5,000 floor, resized every month, on ₹1 crore.",
        "The strategy does not resize a name it keeps. A kept name stays while it ranks in the top 20.",
"A new name needs an equal-weight slot of at least ₹5,000. Integer shares can leave a remainder under ₹5,000;",
"that remainder is kept when it is within 20% of the slot. A wider gap, such as one ₹4,000 share in a ₹7,000 slot, is skipped,",
"and the next name in the ranking fills the slot. If a loss leaves the account unable to fund",
"10 positions of ₹5,000, it holds fewer names. It does not go to cash for the rest of the window.",
        "Tax rates are unverified. Surcharge is not applied. Sources are in app/swing/data/tax_rates_equity.json.",
        f"Forward runs through {latest.isoformat()}. Holdout {HOLDOUT_START.isoformat()} through {HOLDOUT_END.isoformat()} was not a research window.",
        f"Lookback shifts are half-up: 252 becomes 202 and 302, 126 becomes 101 and 151, 21 becomes 17 and 25.",
        "",
        f"Upstox substitutions applied: {len(replaced)}. Event-guard sessions: {len(flags)}.",
        "",
        "## Gate",
        "",
        "| Book | DEV CAGR | DEV DD | OOS CAGR | OOS DD | OOS vol | Sharpe | Turnover | Cost drag | OOS after tax | Nifty OOS | EW OOS | Random | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for kind in KINDS:
        dev = primary[kind]["DEV"]["metrics"]
        oos = primary[kind]["OOS"]["metrics"]
        gate = gates[kind]
        lines.append(
            f"| {kind} {NAMES[kind]} | {_pct(dev['cagr'])} | {_pct(dev['max_dd'])} | {_pct(oos['cagr'])} | {_pct(oos['max_dd'])} | "
            f"{_pct(oos['vol'])} | {_num(oos['sharpe'])} | {_pct(oos['turnover'])} | {_pct(oos['cost_drag'])} | {_pct(oos['cagr_tax'])} | "
            f"{_pct(nifty.get('cagr'))} | {_pct(basket['cagr'])} | {gate['random_beaten']}/{len(RANDOM_SEEDS)} | "
            f"{'PASS' if gate['pass'] else 'FAIL'} |"
        )
    lines += ["", "### Why", ""]
    for kind in KINDS:
        reason = "all conditions met" if gates[kind]["pass"] else "; ".join(gates[kind]["reasons"])
        lines.append(f"- {kind}: {reason}")
    lines += [
        "",
        "## After tax, OOS, primary book",
        "",
        "Pre-tax is the equity curve before capital-gains tax. After tax deducts STCG or LTCG, with cess, at the sale.",
        "The Nifty and equal-weight hurdles use the same tax table. The equal-weight book is ₹1 crore so the ₹20 ticket is not the hurdle.",
        "",
        "| Book | Pre-tax CAGR | After-tax CAGR | Nifty pre-tax | Nifty after tax | EW pre-tax | EW after tax |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for kind in KINDS:
        oos = primary[kind]["OOS"]["metrics"]
        lines.append(
            f"| {kind} | {_pct(oos['cagr'])} | {_pct(oos['cagr_tax'])} | {_pct(nifty.get('cagr'))} | {_pct(nifty.get('cagr_tax'))} | "
            f"{_pct(basket['cagr'])} | {_pct(basket['cagr_tax'])} |"
        )
    zero_nifty = nifty_stats[("OOS", "zero")] or {}
    zero_ew = baskets[("OOS", "zero")]["metrics"]
    lines += [
        "",
        f"₹0 brokerage hurdles, OOS: Nifty pre-tax {_pct(zero_nifty.get('cagr'))}, after tax {_pct(zero_nifty.get('cagr_tax'))}; "
        f"equal-weight pre-tax {_pct(zero_ew['cagr'])}, after tax {_pct(zero_ew['cagr_tax'])}.",
        "",
        "## Primary book, every window",
        "",
    ]
    for kind in KINDS:
        lines.append(f"### {kind} {NAMES[kind]}")
        lines.append("")
        lines.append("| Window | CAGR | Max DD | Vol | Sharpe | Turnover | Cost drag | After-tax CAGR | P&L | Trades |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for window in ("DEV", "OOS", "FORWARD"):
            m = primary[kind][window]["metrics"]
            lines.append(
                f"| {window} | {_pct(m['cagr'])} | {_pct(m['max_dd'])} | {_pct(m['vol'])} | {_num(m['sharpe'])} | "
                f"{_pct(m['turnover'])} | {_pct(m['cost_drag'])} | {_pct(m['cagr_tax'])} | {_rs(m['pnl'])} | {m['trades']} |"
            )
        lines.append("")
        lines.append("Years")
        lines.append("")
        for window in ("DEV", "OOS", "FORWARD"):
            lines.append(f"{window}")
            lines.append("")
            lines.append(_year_table(primary[kind][window]["metrics"]["by_year"]))
            lines.append("")
        lines.append("Holdings")
        lines.append("")
        lines.append(_size_table(primary[kind]["OOS"]["metrics"]))
        lines.append("")
        lines.append(f"OOS P&L without the 10 largest winners: {_rs(extras[kind]['top10'])}. This is not a gate condition.")
        lines.append("")
        lines.append("| Lookback | OOS CAGR | OOS P&L |")
        lines.append("|---|---:|---:|")
        for label, cagr, pnl in extras[kind]["variants"]:
            lines.append(f"| {label} | {_pct(cagr)} | {_rs(pnl)} |")
        lines.append("")
        lines.append(f"Defaults: {DEFAULTS['M1' if kind.startswith('M1') else 'M2']}.")
        lines.append("")
    lines += ["## Other books", ""]
    lines.append("| Window | Book | Capital | Brokerage | CAGR | Max DD | After-tax CAGR | P&L | Trades |")
    lines.append("|---|---|---:|---|---:|---:|---:|---:|---:|")
    for key, by_kind in books.items():
        window, capital, brokerage = key
        for kind, result in by_kind.items():
            if capital == str(PRIMARY_CAPITAL) and brokerage == PRIMARY_BROKERAGE:
                continue
            m = result["metrics"]
            lines.append(
                f"| {window} | {kind} | {capital} | {brokerage} | {_pct(m['cagr'])} | {_pct(m['max_dd'])} | "
                f"{_pct(m['cagr_tax'])} | {_rs(m['pnl'])} | {m['trades']} |"
            )
    lines += ["", "## Benchmarks", ""]
    for (window, brokerage), stats in nifty_stats.items():
        if stats is None:
            lines.append(f"- Nifty {window} {brokerage}: no bars")
        else:
            lines.append(
                f"- Nifty {window} {brokerage}: CAGR {_pct(stats['cagr'])}, after tax {_pct(stats['cagr_tax'])}, max DD {_pct(stats['max_dd'])}"
            )
    for (window, brokerage), result in baskets.items():
        m = result["metrics"]
        lines.append(
            f"- Equal-weight {window} {brokerage}: CAGR {_pct(m['cagr'])}, after tax {_pct(m['cagr_tax'])}, max DD {_pct(m['max_dd'])}"
        )
    lines += ["", "## Holdout", ""]
    if holdout.get("opened"):
        for kind, metrics in holdout["metrics"].items():
            lines.append(f"- {kind}: CAGR {_pct(metrics['cagr'])}, after tax {_pct(metrics['cagr_tax'])}, P&L {_rs(metrics['pnl'])}, trades {metrics['trades']}")
    else:
        lines.append(holdout.get("reason", "not opened"))
        if holdout.get("saved"):
            lines.append("")
            lines.append("Saved earlier:")
            lines.append("")
            lines.append("```")
            lines.append(json.dumps(holdout["saved"], indent=2, default=str))
            lines.append("```")
    if problems:
        lines += ["", "## Sanity", ""]
        lines.extend(f"- {item}" for item in problems)
    lines.append("")
    return "\n".join(lines)


def _year_table(rows: list[dict]) -> str:
    if not rows:
        return "(none)"
    lines = ["| Year | Trades | P&L | End pre-tax | End after tax | Exposure |", "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(
            f"| {row['year']} | {row['trades']} | {_rs(row['pnl'])} | {_rs(row['end_pretax'])} | {_rs(row['end_tax'])} | {_pct(row['exposure'])} |"
        )
    return "\n".join(lines)


def _size_table(metrics: dict) -> str:
    rows = {row["size"]: row for row in metrics["by_size"]}
    share = metrics.get("hold_share") or {}
    lines = ["| Size | Trades | P&L | Share of position-days |", "|---|---:|---:|---:|"]
    for size in ("large", "mid", "small", "rest"):
        row = rows.get(size, {})
        lines.append(f"| {size} | {row.get('trades', 0)} | {_rs(row.get('pnl', 0))} | {_pct(share.get(size, 0))} |")
    return "\n".join(lines)


def _jsonable(gates, primary, baskets, nifty_stats, extras, holdout) -> dict:
    out = {
        "gates": gates,
        "primary": {kind: {name: result["metrics"] for name, result in windows.items()} for kind, windows in primary.items()},
        "nifty": {f"{window}:{brokerage}": stats for (window, brokerage), stats in nifty_stats.items()},
        "equal_weight": {f"{window}:{brokerage}": result["metrics"] for (window, brokerage), result in baskets.items()},
        "extras": {
            kind: {
                "variants": values["variants"],
                "top10": values["top10"],
                "cliffs": values["cliffs"],
                "random": values["seeds"],
            }
            for kind, values in extras.items()
        },
        "holdout": {k: v for k, v in holdout.items() if k != "metrics"},
    }
    if holdout.get("metrics"):
        out["holdout"]["metrics"] = holdout["metrics"]
    return out


def _pct(value) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _rs(value) -> str:
    return f"₹{float(value):,.0f}"


def _num(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.2f}"


def _load(path: Path, start: date, end: date) -> pd.DataFrame:
    quoted = _quote(path)
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"""
            SELECT CAST(date AS DATE) AS date, symbol, isin, lineage,
                   open, high, low, close, volume, turnover,
                   adj_open, adj_high, adj_low, adj_close, passes, size, turnover_20d
            FROM read_parquet('{quoted}')
            WHERE date >= DATE '{start.isoformat()}' AND date <= DATE '{end.isoformat()}'
              AND lineage IN (
                SELECT DISTINCT lineage FROM read_parquet('{quoted}')
                WHERE passes AND turnover_20d >= 100000000
                  AND date >= DATE '{DEV_START.isoformat()}' AND date <= DATE '{end.isoformat()}'
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


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


if __name__ == "__main__":
    sys.exit(main())
