"""SpringPad 50/200 SMA crossover worker. Research only. No orders.

    cd backend && uv run python -m scripts.swing_springpad

The gate is the uncapped stock book, Upstox delivery, variant A and variant B.
Every golden cross is its own trade. The ₹50,000 and ₹2,00,000 books are
reported and are not extra attempts. Index futures use the dated table; a
window that starts before the first row is not costed. Holdout 2026-07-01
through 2026-10-01 is not opened.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd

from app.backtest.instruments import cost_model_for
from app.config import settings
from app.data.history import (
    BANKNIFTY_INDEX_KEY,
    NIFTY_INDEX_KEY,
    SENSEX_INDEX_KEY,
    read_parquet,
    symbol_dir_name,
)
from app.swing.bhav_pit import LISTED_SESSIONS, PRICE_MIN, TURNOVER_MIN, market_hours_block
from app.swing.daily import IST
from app.swing.delivery import DeliveryTable
from app.swing.fixes import as_date, blocked_entries, event_flags, research_prices
from app.swing.research import (
    FORWARD_START,
    HOLDOUT_END,
    HOLDOUT_START,
    index_hold,
    research_windows,
)
from app.swing.springpad import (
    BOOK_SLOTS,
    FAST,
    MAX_HOLD_B,
    RANDOM_SEEDS,
    SLOW,
    STOP_PCT,
    TARGET_PCT,
    TRADE_NOTIONAL,
    SpringSpec,
    apply_costs,
    breakeven_win_rate,
    build_tapes,
    cliff_hits,
    collect_signals,
    entry_pool,
    futures_gap,
    judge,
    length_variants,
    path_stats,
    percent_variants,
    random_signals,
    round_trip_cost,
    run_book,
    run_uncapped,
    summarize,
    _fee_factory,
    _futures_factory,
)
from app.swing.quality import series_path
from scripts.swing_phase2 import UNIVERSE_PATH, _substitute

NIFTY = "NSE_INDEX|Nifty 500"
INDEXES = (
    ("NIFTY", NIFTY_INDEX_KEY),
    ("BANKNIFTY", BANKNIFTY_INDEX_KEY),
    ("SENSEX", SENSEX_INDEX_KEY),
)
CAPITALS = (Decimal("50000"), Decimal("200000"))


def live_block(live: bool) -> str | None:
    if live:
        return "LIVE_TRADING is true. SpringPad does not run."
    return None


def main(argv: list[str] | None = None) -> int:
    del argv
    blocked_reason = live_block(settings.live_trading)
    if blocked_reason:
        print(blocked_reason, file=sys.stderr)
        return 2
    if market_hours_block(datetime.now(IST)):
        print("SpringPad does not run during 09:00–16:10 IST.", file=sys.stderr)
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
    windows = {name: bounds for name, bounds in research_windows(latest).items() if bounds[0] <= bounds[1]}
    print(f"windows { {k: (a.isoformat(), b.isoformat()) for k, (a, b) in windows.items()} }", flush=True)

    print("loading bars", flush=True)
    bars = _load(universe, date(2015, 1, 1), latest)
    print(f"rows {len(bars)} lineages {bars['lineage'].nunique()}", flush=True)
    bars = research_prices(bars)
    stocks = json.loads(Path(UNIVERSE_PATH).read_text())["stocks"]
    replaced, examined, _skipped = _substitute(bars, stocks, universe)
    print(f"substituted {len(replaced)} of {examined}", flush=True)
    calendar = _calendar(universe, date(2015, 1, 1), latest)
    flags = event_flags(bars)
    flag_set = {(str(a), as_date(b)) for a, b in zip(flags["lineage"], flags["date"], strict=False)}
    blocked = blocked_entries(flags, calendar, 2)
    print(f"flags {len(flags)} blocked entries {len(blocked)}", flush=True)

    print("tapes", flush=True)
    tapes = build_tapes(bars, FAST, SLOW)
    print(f"tapes {len(tapes)}", flush=True)
    delivery = DeliveryTable.load()
    futures = cost_model_for("index_future")
    chargers = {
        "upstox": _fee_factory(delivery, None),
        "zero": _fee_factory(delivery, Decimal("0")),
    }
    future_charge = _futures_factory(futures)

    stock_raw: dict[tuple[str, str], list] = {}
    stock_stats: dict[tuple[str, str, str], dict] = {}
    for window, (start, end) in windows.items():
        signals = collect_signals(tapes, calendar, start, end, blocked, "long", require_eligible=True)
        print(f"{window} golden crosses {len(signals)}", flush=True)
        for variant, hold in (("A", None), ("B", MAX_HOLD_B)):
            spec = SpringSpec(start, end, max_hold=hold)
            raw = run_uncapped(signals, tapes, calendar, spec, flag_set)
            stock_raw[(window, variant)] = raw
            for brokerage, charge in chargers.items():
                stats = summarize([apply_costs(item, charge) for item in raw])
                stock_stats[(window, variant, brokerage)] = stats
                print(
                    f"  {variant} {brokerage} trades {stats['trades']} avg {stats['avg']:.4f} pnl {stats['pnl']:.0f}",
                    flush=True,
                )

    books: dict[tuple, dict] = {}
    nifty_stats: dict[str, dict | None] = {}
    for window, (start, end) in windows.items():
        nifty_stats[window] = index_hold(nifty, start, end, 50_000.0)
        signals = collect_signals(tapes, calendar, start, end, blocked, "long", require_eligible=True)
        for capital in CAPITALS:
            for brokerage, charge in chargers.items():
                for variant, hold in (("A", None), ("B", MAX_HOLD_B)):
                    spec = SpringSpec(
                        start, end, max_hold=hold, capital=capital, max_positions=BOOK_SLOTS, brokerage=brokerage,
                    )
                    result = run_book(signals, tapes, calendar, spec, flag_set, charge)
                    stats = summarize(result["trades"])
                    path = path_stats(result["equity"], start, end, float(capital))
                    books[(window, str(capital), brokerage, variant)] = {"stats": stats, "path": path}
                    print(
                        f"book {window} {variant} {capital} {brokerage} cagr {path['cagr']:.3f} "
                        f"dd {path['max_dd']:.3f} trades {stats['trades']}",
                        flush=True,
                    )

    indexes: dict[str, dict] = {}
    for label, key in INDEXES:
        frame = _index_frame(key)
        if frame is None:
            indexes[label] = {"missing": True, "key": key}
            print(f"{label} daily bars are not stored", flush=True)
            continue
        index_tapes = build_tapes(frame, FAST, SLOW, require_filter=False)
        index_market = index_tapes[key].dates
        indexes[label] = {"missing": False, "key": key, "bars": len(index_market), "raw": {}, "stats": {}}
        print(f"{label} bars {len(index_market)} {index_market[0]}..{index_market[-1]}", flush=True)
        for window, (start, end) in windows.items():
            gap = futures_gap(futures, start, end)
            for side in ("long", "short"):
                signals = collect_signals(
                    index_tapes, index_market, start, end, set(), side, require_eligible=False,
                )
                for variant, hold in (("A", None), ("B", MAX_HOLD_B)):
                    name = (window, side, variant)
                    if gap:
                        indexes[label]["stats"][name] = None
                        indexes[label]["raw"][name] = []
                        print(f"  {label} {window} {side} {variant} not costed", flush=True)
                        continue
                    spec = SpringSpec(start, end, max_hold=hold, kind="index", side=side, brokerage="upstox")
                    raw = run_uncapped(signals, index_tapes, index_market, spec, set())
                    indexes[label]["raw"][name] = raw
                    stats = summarize([apply_costs(item, future_charge) for item in raw])
                    indexes[label]["stats"][name] = stats
                    print(
                        f"  {label} {window} {side} {variant} trades {stats['trades']} avg {stats['avg']:.4f}",
                        flush=True,
                    )
        indexes[label]["tapes"] = index_tapes
        indexes[label]["market"] = index_market

    print("sensitivity", flush=True)
    variants = _sensitivity(tapes, calendar, windows["OOS"], blocked, flag_set, chargers["upstox"])
    print("random", flush=True)
    seeds = _random(tapes, calendar, windows["OOS"], blocked, flag_set, chargers["upstox"], stock_raw)
    index_extra = _index_gate_extra(indexes, futures, windows.get("OOS"), future_charge)

    gates = _gates(stock_stats, variants, seeds, indexes, index_extra)
    problems = _sanity(stock_raw, indexes)
    if problems:
        print("sanity " + "; ".join(problems), file=sys.stderr)
    text = _report(
        stock_stats, books, nifty_stats, indexes, gates, variants, seeds, delivery, replaced, latest, problems,
    )
    out = settings.data_dir / "swing_springpad"
    out.mkdir(parents=True, exist_ok=True)
    report = settings.data_dir / "validation" / "swing_springpad_2026-11-26.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text)
    (out / "summary.json").write_text(
        json.dumps(_jsonable(gates, stock_stats, books, nifty_stats), indent=2, default=str)
    )
    print(text)
    print(f"wrote {report}")
    return 1 if problems else 0


def _sensitivity(tapes, market, bounds, blocked, flags, charge) -> dict[str, list]:
    start, end = bounds
    found = {"A": [], "B": []}
    base_signals = collect_signals(tapes, market, start, end, blocked, "long", require_eligible=True)
    for variant, hold in (("A", None), ("B", MAX_HOLD_B)):
        base = SpringSpec(start, end, max_hold=hold)
        for label, fast, slow in length_variants(FAST, SLOW):
            retargeted = {key: tape.with_lengths(fast, slow) for key, tape in tapes.items()}
            signals = collect_signals(retargeted, market, start, end, blocked, "long", require_eligible=True)
            raw = run_uncapped(signals, retargeted, market, SpringSpec(start, end, fast=fast, slow=slow, max_hold=hold), flags)
            stats = summarize([apply_costs(item, charge) for item in raw])
            found[variant].append((label, stats["pnl"], stats["avg"]))
            print(f"  {variant} {label} trades {stats['trades']} avg {stats['avg']:.4f}", flush=True)
        for label, spec in percent_variants(base):
            raw = run_uncapped(base_signals, tapes, market, spec, flags)
            stats = summarize([apply_costs(item, charge) for item in raw])
            found[variant].append((label, stats["pnl"], stats["avg"]))
            print(f"  {variant} {label} trades {stats['trades']} avg {stats['avg']:.4f}", flush=True)
    return found


def _random(tapes, market, bounds, blocked, flags, charge, stock_raw) -> dict[str, list]:
    start, end = bounds
    spec = SpringSpec(start, end)
    pool = entry_pool(tapes, market, start, end, blocked, spec)
    print(f"  pool {len(pool)}", flush=True)
    found = {"A": [], "B": []}
    for variant, hold in (("A", None), ("B", MAX_HOLD_B)):
        count = len(stock_raw[("OOS", variant)])
        walk = SpringSpec(start, end, max_hold=hold)
        for seed in RANDOM_SEEDS:
            signals = random_signals(pool, tapes, count, seed, "long")
            raw = run_uncapped(signals, tapes, market, walk, flags)
            stats = summarize([apply_costs(item, charge) for item in raw])
            found[variant].append(stats["avg"])
            if seed % 10 == 0:
                print(f"  {variant} seed {seed} avg {stats['avg']:.4f}", flush=True)
    return found


def _index_gate_extra(indexes, futures, bounds, charge) -> dict:
    """±20% and 50 random dates for each stored index book. OOS only."""
    extra = {}
    if bounds is None:
        return extra
    start, end = bounds
    for label, info in indexes.items():
        if info.get("missing"):
            continue
        tapes = info["tapes"]
        market = info["market"]
        for side in ("long", "short"):
            for variant, hold in (("A", None), ("B", MAX_HOLD_B)):
                key = (label, side, variant)
                if futures_gap(futures, start, end):
                    extra[key] = {"variants": [], "seeds": []}
                    continue
                base = SpringSpec(start, end, max_hold=hold, kind="index", side=side)
                signals = collect_signals(tapes, market, start, end, set(), side, require_eligible=False)
                variants = []
                for name, fast, slow in length_variants(FAST, SLOW):
                    retargeted = {item: tape.with_lengths(fast, slow) for item, tape in tapes.items()}
                    crossed = collect_signals(retargeted, market, start, end, set(), side, require_eligible=False)
                    raw = run_uncapped(
                        crossed, retargeted, market,
                        SpringSpec(start, end, fast=fast, slow=slow, max_hold=hold, kind="index", side=side),
                        set(),
                    )
                    stats = summarize([apply_costs(item, charge) for item in raw])
                    variants.append((name, stats["pnl"], stats["avg"]))
                for name, spec in percent_variants(base):
                    raw = run_uncapped(signals, tapes, market, spec, set())
                    stats = summarize([apply_costs(item, charge) for item in raw])
                    variants.append((name, stats["pnl"], stats["avg"]))
                pool = entry_pool(tapes, market, start, end, set(), base)
                count = len(info["raw"].get(("OOS", side, variant), []))
                seeds = []
                for seed in RANDOM_SEEDS:
                    drawn = random_signals(pool, tapes, count, seed, side)
                    raw = run_uncapped(drawn, tapes, market, base, set())
                    stats = summarize([apply_costs(item, charge) for item in raw])
                    seeds.append(stats["avg"])
                extra[key] = {"variants": variants, "seeds": seeds}
                print(f"  {label} {side} {variant} sensitivity and random done", flush=True)
    return extra


def _gates(stock_stats, variants, seeds, indexes, index_extra) -> dict:
    gates = {}
    for variant in ("A", "B"):
        dev = stock_stats[("DEV", variant, "upstox")]
        oos = stock_stats[("OOS", variant, "upstox")]
        cliffs = cliff_hits(oos["pnl"], oos["avg"], variants[variant])
        gates[f"stock {variant}"] = judge(dev, oos, seeds[variant], cliffs)
    for label, info in indexes.items():
        for side in ("long", "short"):
            for variant in ("A", "B"):
                name = f"{label} {side} {variant}"
                if info.get("missing"):
                    gates[name] = {
                        "pass": False,
                        "reasons": [f"{label} daily bars are not stored"],
                        "beaten": 0,
                        "seeds": 0,
                        "random_share": 0.0,
                    }
                    continue
                dev = info["stats"].get(("DEV", side, variant))
                oos = info["stats"].get(("OOS", side, variant))
                extra = index_extra.get((label, side, variant), {"variants": [], "seeds": []})
                oos_stats = oos or {"pnl": 0.0, "avg": 0.0, "trades": 0}
                cliffs = cliff_hits(oos_stats["pnl"], oos_stats["avg"], extra["variants"]) if oos else []
                gates[name] = judge(
                    dev or {"pnl": 0.0, "avg": 0.0, "trades": 0},
                    oos_stats,
                    extra["seeds"],
                    cliffs,
                    dev_costed=dev is not None,
                )
    return gates


def _sanity(stock_raw, indexes) -> list[str]:
    problems = []
    checked = 0
    batches = list(stock_raw.items())
    for label, info in indexes.items():
        if info.get("missing"):
            continue
        batches.extend((f"{label} {side} {variant}", trades) for (side, variant), trades in _flatten_index(info))
    for name, trades in batches:
        for trade in trades:
            checked += 1
            if trade["entry_date"] <= trade["signal_date"]:
                problems.append(f"{name} fills on the signal day")
                return problems
            if HOLDOUT_START <= trade["entry_date"] <= HOLDOUT_END:
                problems.append(f"{name} enters the holdout")
                return problems
    if checked == 0:
        problems.append("no trades were checked")
    return problems


def _flatten_index(info) -> list[tuple[tuple, list]]:
    rows = []
    for (window, side, variant), trades in info.get("raw", {}).items():
        if window == "DEV" and not trades:
            continue
        rows.append(((side, variant), trades))
    return rows


def _report(stock_stats, books, nifty_stats, indexes, gates, variants, seeds, delivery, replaced, latest, problems) -> str:
    lines = [
        "# SpringPad 50/200 SMA crossover",
        "",
        "Research only. No order was sent. The gate is the uncapped stock book on Upstox delivery, "
        "variant A (no time limit) and variant B (exit at the close of session 5). "
        f"Every golden cross is its own ₹{int(TRADE_NOTIONAL):,} trade. "
        "The ₹50,000 and ₹2,00,000 books, five names at a time, are reported and were not extra attempts. "
        "Nifty 500 in the portfolio table is the price index, bought at the first open and held to the last close, with no costs. "
        "A total-return index is not in the stored bars. "
        f"Holdout {HOLDOUT_START.isoformat()} through {HOLDOUT_END.isoformat()} was not opened. "
        f"Forward runs through {latest.isoformat()}.",
        "",
        "Stop 2% and target 4% are measured from the slipped fill. A gap fills at the open. "
        "Both levels in one session fill the stop. An opposite cross fills at the next open. "
        "Stock slippage is 0.10% for large and mid (the stored top 250) and 0.25% for the rest. "
        "Index futures are one unit, with no extra slippage, and the dated futures table. "
        "That table starts 2022-01-01, so index DEV is not costed and the 2022 row is not copied backward.",
        "",
        f"Substituted {len(replaced)} stored series. Delivery and futures rates are unverified.",
        "",
        "## Gate",
        "",
        "| Book | DEV trades | DEV avg | OOS trades | OOS win | OOS avg | Cost | PF | Hold | Stop | Target | Cross | Time | Other | Seeds | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for variant in ("A", "B"):
        lines.append(_gate_row(f"Stock {variant}", stock_stats[("DEV", variant, "upstox")], stock_stats[("OOS", variant, "upstox")], gates[f"stock {variant}"]))
    for label, _key in INDEXES:
        info = indexes[label]
        for side in ("long", "short"):
            for variant in ("A", "B"):
                name = f"{label} {side} {variant}"
                dev = None if info.get("missing") else info["stats"].get(("DEV", side, variant))
                oos = None if info.get("missing") else info["stats"].get(("OOS", side, variant))
                lines.append(_gate_row(name, dev, oos, gates[name]))
    lines.extend(["", "### Verdict", ""])
    for name, gate in gates.items():
        status = "PASS" if gate["pass"] else "FAIL"
        why = "; ".join(gate["reasons"]) if gate["reasons"] else "all conditions held"
        lines.append(f"- {name}: {status}. {why}.")
    lines.extend(["", "## Break-even", ""])
    lines.append(_breakeven(delivery))
    lines.extend(["", "## Stock books by year and size (OOS, Upstox)", ""])
    for variant in ("A", "B"):
        stats = stock_stats[("OOS", variant, "upstox")]
        lines.append(f"### Variant {variant}")
        lines.append("")
        lines.append(_split("Year", stats["by_year"]))
        lines.append("")
        lines.append(_split("Size", stats["by_size"]))
        lines.append("")
        lines.append("Exits: " + ", ".join(f"{key} {value}" for key, value in sorted(stats["exits"].items())))
        lines.append("")
    lines.extend(["## Portfolio", ""])
    lines.append("| Book | Capital | Brokerage | Window | CAGR | Max DD | Nifty CAGR | Nifty DD | Trades | Avg trade |")
    lines.append("|---|---:|---|---|---:|---:|---:|---:|---:|---:|")
    for window in ("DEV", "OOS", "FORWARD"):
        if window not in nifty_stats:
            continue
        bench = nifty_stats[window] or {}
        for capital in CAPITALS:
            for brokerage in ("upstox", "zero"):
                for variant in ("A", "B"):
                    item = books[(window, str(capital), brokerage, variant)]
                    lines.append(
                        f"| {variant} | ₹{int(capital):,} | {brokerage} | {window} | {_pct(item['path']['cagr'])} | "
                        f"{_pct(item['path']['max_dd'])} | {_pct(bench.get('cagr'))} | {_pct(bench.get('max_dd'))} | "
                        f"{item['stats']['trades']} | {_pct(item['stats']['avg'], 2)} |"
                    )
    lines.extend(["", "## Sensitivity (OOS, Upstox, uncapped)", ""])
    for variant in ("A", "B"):
        base = stock_stats[("OOS", variant, "upstox")]
        lines.append(f"Variant {variant} base average {_pct(base['avg'], 2)}, pnl ₹{base['pnl']:,.0f}.")
        for label, pnl, avg in variants[variant]:
            lines.append(f"- {label}: average {_pct(avg, 2)}, pnl ₹{pnl:,.0f}.")
        beaten = gates[f"stock {variant}"]["beaten"]
        total = gates[f"stock {variant}"]["seeds"]
        lines.append(f"Random seeds beaten: {beaten}/{total}.")
        lines.append("")
    if ("FORWARD", "A", "upstox") in stock_stats:
        lines.extend(["## Forward", ""])
        lines.append("| Book | Trades | Win | Avg | PnL |")
        lines.append("|---|---:|---:|---:|---:|")
        for variant in ("A", "B"):
            for brokerage in ("upstox", "zero"):
                stats = stock_stats[("FORWARD", variant, brokerage)]
                lines.append(
                    f"| Stock {variant} {brokerage} | {stats['trades']} | {_pct(stats['win_rate'])} | "
                    f"{_pct(stats['avg'], 2)} | ₹{stats['pnl']:,.0f} |"
                )
        lines.append("")
    lines.append("Zero brokerage is the same trades with the flat brokerage set to ₹0. STT, exchange, stamp, DP, and GST remain.")
    if problems:
        lines.extend(["", "## Sanity", ""])
        for item in problems:
            lines.append(f"- {item}")
    else:
        lines.extend(["", "Sanity: no fill is on the signal day, and no non-holdout window enters the holdout.", ""])
    lines.append("")
    return "\n".join(lines)


def _gate_row(name: str, dev: dict | None, oos: dict | None, gate: dict) -> str:
    dev = dev or {}
    oos = oos or {}
    groups = oos.get("groups") or {}
    beaten = gate.get("beaten", 0)
    total = gate.get("seeds", 0)
    return (
        f"| {name} | {dev.get('trades', 0)} | {_pct(dev.get('avg'), 2)} | {oos.get('trades', 0)} | "
        f"{_pct(oos.get('win_rate'))} | {_pct(oos.get('avg'), 2)} | {_pct(oos.get('cost'), 2)} | {_pf(oos)} | "
        f"{_num(oos.get('hold'))} | {groups.get('stop', 0)} | {groups.get('target', 0)} | {groups.get('cross', 0)} | "
        f"{groups.get('time', 0)} | {groups.get('other', 0)} | {beaten}/{total} | "
        f"{'PASS' if gate['pass'] else 'FAIL'} |"
    )


def _split(title: str, rows: list[dict]) -> str:
    lines = [f"| {title} | Trades | Win | Avg | PnL | Hold |", "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(
            f"| {row['key']} | {row['trades']} | {_pct(row['win_rate'])} | {_pct(row['avg'], 2)} | "
            f"₹{row['pnl']:,.0f} | {_num(row['hold'])} |"
        )
    return "\n".join(lines)


def _breakeven(delivery) -> str:
    on = date(2024, 6, 3)
    row = delivery.row_for(on)
    parts = []
    for label, slip in (("top 250", Decimal("0.001")), ("the rest", Decimal("0.0025"))):
        for name, flat in (("Upstox", None), ("₹0 brokerage", Decimal("0"))):
            charge = _fee_factory(delivery, flat)
            cost = round_trip_cost(charge, on, TRADE_NOTIONAL, slip)
            rate = breakeven_win_rate(float(STOP_PCT), float(TARGET_PCT), cost)
            parts.append(
                f"{name}, {label}: round-trip cost {_pct(cost, 2)} of notional, break-even win rate {_pct(rate, 1)}"
            )
    return (
        f"Delivery row effective {row.effective_from.isoformat()} ({row.verification}). "
        "w* = (0.02 + cost) / (0.02 + 0.04). "
        + ". ".join(parts)
        + "."
    )


def _pct(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{100.0 * float(value):.{digits}f}%"


def _pf(stats: dict) -> str:
    if not stats:
        return "—"
    if stats.get("pf_infinite"):
        return "inf"
    if stats.get("pf") is None:
        return "—"
    return f"{float(stats['pf']):.2f}"


def _num(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}"


def _jsonable(gates, stock_stats, books, nifty_stats) -> dict:
    def shrink(stats: dict) -> dict:
        return {key: value for key, value in stats.items() if key not in ("by_size", "by_year", "exits")}

    return {
        "gates": gates,
        "stock": {f"{window}|{variant}|{brokerage}": shrink(stats) for (window, variant, brokerage), stats in stock_stats.items()},
        "books": {
            f"{window}|{capital}|{brokerage}|{variant}": {"path": item["path"], "stats": shrink(item["stats"])}
            for (window, capital, brokerage, variant), item in books.items()
        },
        "nifty": {window: stats for window, stats in nifty_stats.items()},
    }


def _load(path: Path, start: date, end: date) -> pd.DataFrame:
    quoted = _quote(path)
    floor = int(TURNOVER_MIN)
    price = int(PRICE_MIN)
    sessions = int(LISTED_SESSIONS)
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"""
            SELECT CAST(date AS DATE) AS date, symbol, isin, lineage,
                   open, high, low, close, volume, turnover,
                   adj_open, adj_high, adj_low, adj_close, sessions, size, turnover_20d
            FROM read_parquet('{quoted}')
            WHERE date >= DATE '{start.isoformat()}' AND date <= DATE '{end.isoformat()}'
              AND lineage IN (
                SELECT DISTINCT lineage FROM read_parquet('{quoted}')
                WHERE sessions >= {sessions} AND turnover_20d >= {floor} AND close >= {price}
                  AND date >= DATE '{start.isoformat()}' AND date <= DATE '{end.isoformat()}'
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
    return pd.DataFrame(
        {"date": dates, "open": frame["open"].astype(float).to_numpy(), "close": frame["close"].astype(float).to_numpy()}
    )


def _index_frame(key: str) -> pd.DataFrame | None:
    path = series_path(settings.candles_daily_dir / symbol_dir_name(key))
    if not path.is_file():
        return None
    frame = read_parquet(path)
    for column in ("open", "high", "low", "close", "time"):
        if column not in frame.columns:
            return None
    dates = list(pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date)
    values = {name: frame[name].astype(float).to_numpy() for name in ("open", "high", "low", "close")}
    return pd.DataFrame(
        {
            "date": dates,
            "lineage": key,
            "symbol": key,
            "size": "index",
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
            "close": values["close"],
            "px_open": values["open"],
            "px_high": values["high"],
            "px_low": values["low"],
            "px_close": values["close"],
        }
    )


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


if __name__ == "__main__":
    sys.exit(main())
