"""Price and cost check for the Phase 3 book. No new rules, no holdout.

    cd backend && PYTHONPATH=. uv run python -m scripts.swing_phase3_sanity

Prints the 20 worst primary-book trades per strategy and a ₹1 crore run with
no costs and no slippage. Writes the long listing under data/validation/.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import settings
from app.swing.bhav_pit import market_hours_block
from app.swing.daily import IST
from app.swing.delivery import DeliveryLeg, DeliveryTable
from app.swing.fixes import as_date, blocked_entries, event_flags, research_prices
from app.swing.research import BookSpec, bar_lookup, index_hold, random_signals, research_windows
from app.swing.research import run_research
from app.swing.strategies import make_signals, prepare_tape, week_end_dates
from scripts.swing_phase2 import UNIVERSE_PATH, _substitute
from scripts.swing_phase3 import (
    KINDS,
    PRIMARY_BROKERAGE,
    PRIMARY_CAPITAL,
    PRIMARY_SLOTS,
    _calendar,
    _clip,
    _last_session,
    _load,
    _nifty_frame,
    _pool,
    live_block,
)

GROSS_CAPITAL = Decimal("10000000")
OUT = settings.data_dir / "validation" / "swing_phase3_sanity_2026-11-26.md"


class _NoCharges:
    """Every leg is zero. Used only by this check."""

    def leg(self, side, price, qty, on, flat_override=None) -> DeliveryLeg:
        del price, qty, on, flat_override
        return DeliveryLeg(side=side, components={}, total=Decimal(0))


def main() -> int:
    blocked = live_block(settings.live_trading)
    if blocked:
        print(blocked, file=sys.stderr)
        return 2
    if market_hours_block(datetime.now(IST)):
        print("Swing Phase 3 sanity does not run during 09:00–16:10 IST.", file=sys.stderr)
        return 2
    universe = settings.bhavcopy_dir / "universe.parquet"
    nifty = _nifty_frame()
    latest = min(as_date(nifty["date"].max()), _last_session(universe))
    windows = research_windows(latest)
    print("loading", flush=True)
    bars = research_prices(_load(universe, date(2015, 1, 1), latest))
    stocks = json.loads(Path(UNIVERSE_PATH).read_text())["stocks"]
    replaced, examined, _skipped = _substitute(bars, stocks, universe)
    substituted = {str(item.get("lineage") or item.get("symbol")) for item in replaced}
    substituted_symbols = {str(item.get("symbol")) for item in replaced}
    print(f"substituted {len(replaced)} of {examined}", flush=True)
    calendar = _calendar(universe, date(2015, 1, 1), latest)
    tape = prepare_tape(bars, pd.Series(nifty["close"].to_numpy(), index=pd.Index(list(nifty["date"]))))
    flags = event_flags(tape)
    lookup = bar_lookup(tape)
    ends = week_end_dates(calendar)
    costs = DeliveryTable.load()
    prices = _price_map(tape)
    flag_set = {(str(a), as_date(b)) for a, b in zip(flags["lineage"], flags["date"], strict=False)} if not flags.empty else set()
    signals = {kind: make_signals(kind, tape, ends) for kind in KINDS}
    lines = [
        "# Swing Phase 3 sanity",
        "",
        f"Substitutions: {len(replaced)}. Event flags: {len(flags)}.",
        "Primary book is ₹50,000, 5 positions, Upstox costs. Gross book is ₹1 crore, 5 positions, no costs, no slippage.",
        "",
    ]

    primary_trades: dict[str, pd.DataFrame] = {}
    for kind in KINDS:
        frames = []
        for name in ("DEV", "OOS"):
            start, end = windows[name]
            spec = BookSpec(PRIMARY_CAPITAL, PRIMARY_SLOTS, start, end, PRIMARY_BROKERAGE)
            result = run_research(
                tape, _clip(signals[kind], calendar, start, end), costs, spec,
                calendar=calendar, flags=flags, week_ends=ends, lookup=lookup,
            )
            frame = result["trades"].copy()
            if not frame.empty:
                frame["window"] = name
            frames.append(frame)
            print(f"primary {kind} {name}: {0 if frame.empty else len(frame)} trades pnl {0 if frame.empty else frame['pnl'].sum():.0f}", flush=True)
        primary_trades[kind] = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        lines.extend(_worst(kind, primary_trades[kind], prices, flag_set, substituted, substituted_symbols, calendar))

    lines += ["", "## Gross book, ₹1 crore, no costs, no slippage", ""]
    gross_spec_slip = dict(slip_liquid=Decimal(0), slip_other=Decimal(0))
    gross_costs = _NoCharges()
    pool = _pool(tape)
    blocked_set = blocked_entries(flags, calendar, 2)
    prev_of = {calendar[i]: calendar[i - 1] for i in range(1, len(calendar))}
    universe_ret = _universe_five_day(tape, calendar)
    lines.append("| Strategy | Window | Trades | Win | Avg gross | Universe 5-day | Random avg | Random win |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    yearly_rows = []
    for kind in KINDS:
        for name in ("DEV", "OOS"):
            start, end = windows[name]
            spec = BookSpec(GROSS_CAPITAL, PRIMARY_SLOTS, start, end, "zero", **gross_spec_slip)
            result = run_research(
                tape, _clip(signals[kind], calendar, start, end), gross_costs, spec,
                calendar=calendar, flags=flags, week_ends=ends, lookup=lookup,
            )
            trades = result["trades"]
            avg, win, count = _avg_win(trades)
            base = _matched_universe(trades, universe_ret)
            seeds = _random_gross(
                kind, tape, trades, pool, blocked_set, prev_of, gross_costs, calendar, flags, ends, lookup, start, end
            )
            seed_avg = float(np.mean([item[0] for item in seeds])) if seeds else float("nan")
            seed_win = float(np.mean([item[1] for item in seeds])) if seeds else float("nan")
            print(
                f"gross {kind} {name}: n={count} win={win*100:.1f}% avg={avg*100:.2f}% "
                f"universe={base*100:.2f}% random_avg={seed_avg*100:.2f}% random_win={seed_win*100:.1f}%",
                flush=True,
            )
            lines.append(
                f"| {kind} | {name} | {count} | {_pct(win)} | {_pct(avg)} | {_pct(base)} | {_pct(seed_avg)} | {_pct(seed_win)} |"
            )
            if kind == "S2":
                yearly_rows.append((name, result, start, end))
    lines += ["", "## S2 gross year vs Nifty 500 price index", ""]
    lines.extend(_yearly(yearly_rows, nifty, prices, flag_set))
    lines += ["", "## Universe 5-day open to close, every eligible name", ""]
    for name, (start, end) in windows.items():
        if name == "FORWARD":
            continue
        value = _window_universe(tape, calendar, universe_ret, start, end)
        lines.append(f"- {name}: {_pct(value)}")
        print(f"universe {name}: {value*100:.2f}%", flush=True)
    nifty_stats = {name: index_hold(nifty, start, end, 1.0) for name, (start, end) in windows.items()}
    lines += ["", "## Nifty price index", ""]
    for name, stats in nifty_stats.items():
        if stats:
            lines.append(f"- {name}: CAGR {_pct(stats['cagr'])}, max DD {_pct(stats['max_dd'])}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT}", flush=True)
    return 0


def _avg_win(trades: pd.DataFrame) -> tuple[float, float, int]:
    if trades is None or trades.empty:
        return 0.0, 0.0, 0
    ret = trades["pnl"] / trades["notional"]
    return float(ret.mean()), float((trades["pnl"] > 0).mean()), int(len(trades))


def _price_map(tape: pd.DataFrame) -> dict:
    out = {}
    raw_open = tape["open"].to_numpy(dtype=float) if "open" in tape.columns else tape["px_open"].to_numpy(dtype=float)
    raw_close = tape["close"].to_numpy(dtype=float) if "close" in tape.columns else tape["px_close"].to_numpy(dtype=float)
    adj_open = tape["adj_open"].to_numpy(dtype=float) if "adj_open" in tape.columns else tape["px_open"].to_numpy(dtype=float)
    adj_close = tape["adj_close"].to_numpy(dtype=float) if "adj_close" in tape.columns else tape["px_close"].to_numpy(dtype=float)
    lineages = tape["lineage"].astype(str).to_numpy()
    days = tape["date"].to_numpy()
    px_open = tape["px_open"].to_numpy(dtype=float)
    px_close = tape["px_close"].to_numpy(dtype=float)
    for i in range(len(tape)):
        out[(lineages[i], as_date(days[i]))] = (
            float(raw_open[i]), float(raw_close[i]), float(adj_open[i]), float(adj_close[i]), float(px_open[i]), float(px_close[i])
        )
    return out


def _worst(kind, trades, prices, flags, substituted, symbols, calendar) -> list[str]:
    lines = ["", f"## {kind} worst 20 by trade percent", ""]
    if trades.empty:
        lines.append("(no trades)")
        return lines
    work = trades.copy()
    work["ret"] = work["pnl"] / work["notional"]
    work["price_ret"] = work["exit_px"] / work["entry_open"] - 1.0
    small = work["notional"] < 5000
    lines.append(
        f"Trades {len(work)}. Share below ₹5,000 notional: {float(small.mean())*100:.1f}%. "
        f"Mean net {work['ret'].mean()*100:.2f}%. Mean price move {work['price_ret'].mean()*100:.2f}%. "
        f"Mean net on notional ≥ ₹5,000: {work.loc[~small, 'ret'].mean()*100:.2f}% "
        f"({int((~small).sum())} trades). Reasons: {work['reason'].value_counts().to_dict()}."
    )
    mismatch = 0
    factor_shift = 0
    for row in work.itertuples(index=False):
        entry = prices.get((str(row.lineage), date.fromisoformat(row.entry_date)))
        exit_ = prices.get((str(row.lineage), date.fromisoformat(row.exit_date)))
        if entry is None or exit_ is None:
            mismatch += 1
            continue
        if abs(row.entry_open - entry[4]) / max(entry[4], 1e-9) > 0.005:
            mismatch += 1
        if _mult(entry) > 0 and _mult(exit_) > 0 and abs(_mult(entry) / _mult(exit_) - 1) > 0.02:
            factor_shift += 1
    lines.append(
        f"Entry price differs from the research open by more than 0.5%: {mismatch}. "
        f"Adjustment multiple changes during the hold by more than 2%: {factor_shift}."
    )
    print(
        f"check {kind}: n={len(work)} below5k={float(small.mean())*100:.1f}% "
        f"net={work['ret'].mean()*100:.2f}% price={work['price_ret'].mean()*100:.2f}% "
        f"large_net={work.loc[~small, 'ret'].mean()*100 if (~small).any() else float('nan'):.2f}% "
        f"entry_mismatch={mismatch} factor_shift={factor_shift}",
        flush=True,
    )
    index = {day: i for i, day in enumerate(calendar)}
    worst = work.sort_values("ret").head(20)
    lines.append("")
    lines.append("| Symbol | Entry | Exit | Reason | Qty | Notional | Net | Price move | Entry px | Exit px | Raw entry o/c | Raw exit o/c | Adj entry o/c | Adj exit o/c | Why |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|")
    for row in worst.itertuples(index=False):
        entry = prices.get((str(row.lineage), date.fromisoformat(row.entry_date)))
        exit_ = prices.get((str(row.lineage), date.fromisoformat(row.exit_date)))
        why = _why(row, entry, exit_, flags, substituted, symbols, index)
        lines.append(
            f"| {row.symbol} | {row.entry_date} | {row.exit_date} | {row.reason} | {row.qty} | {row.notional:.0f} | "
            f"{row.ret*100:.1f}% | {row.price_ret*100:.1f}% | {row.entry_open:.2f} | {row.exit_px:.2f} | "
            f"{_pair(entry, 0, 1)} | {_pair(exit_, 0, 1)} | {_pair(entry, 2, 3)} | {_pair(exit_, 2, 3)} | {why} |"
        )
    return lines


def _mult(quote) -> float:
    raw = quote[1]
    adj = quote[3]
    if raw <= 0:
        return 0.0
    return adj / raw


def _pair(quote, a: int, b: int) -> str:
    if quote is None:
        return "—"
    return f"{quote[a]:.2f}/{quote[b]:.2f}"


def _why(row, entry, exit_, flags, substituted, symbols, index) -> str:
    if entry is None or exit_ is None:
        return "missing bar on the entry or exit date"
    notes = []
    if str(row.lineage) in substituted or str(row.symbol) in symbols:
        notes.append("Upstox series replaced the bhavcopy")
    if abs(row.entry_open - entry[4]) / max(entry[4], 1e-9) > 0.005:
        notes.append("entry price is not the research open")
    else:
        notes.append("entry is the research open")
    exit_matches = min(
        abs(row.exit_px - exit_[4]) / max(abs(exit_[4]), 1e-9),
        abs(row.exit_px - exit_[5]) / max(abs(exit_[5]), 1e-9),
        abs(row.exit_px - row.stop) / max(abs(row.stop), 1e-9),
    )
    if exit_matches > 0.005:
        notes.append("exit price is not the research open, close, or stop")
    else:
        notes.append("exit is the research open, close, or stop")
    if _mult(entry) > 0 and _mult(exit_) > 0 and abs(_mult(entry) / _mult(exit_) - 1) > 0.02:
        raw_move = exit_[1] / entry[0] - 1 if entry[0] else float("nan")
        notes.append(
            f"adjustment multiple changed ({_mult(entry):.3f} to {_mult(exit_):.3f}); raw close/open {raw_move*100:.1f}%"
        )
    start = index.get(date.fromisoformat(row.entry_date), 0)
    end = index.get(date.fromisoformat(row.exit_date), start)
    # The flag set is checked by the caller via dates. A gap inside the hold is noted when the multiple moves.
    if row.notional < 5000:
        notes.append(f"notional ₹{row.notional:.0f} makes the fixed delivery charge the trade")
    elif abs(row.ret - row.price_ret) > 0.02:
        notes.append("net and the price move differ by the charges")
    else:
        notes.append("the loss is the price move")
    del flags, start, end
    return "; ".join(notes)


def _universe_five_day(tape: pd.DataFrame, calendar: list[date]) -> dict[tuple[str, date], float]:
    """Open of the next session to the close four sessions later, per lineage and signal date."""
    index = {day: i for i, day in enumerate(calendar)}
    by_lineage: dict[str, dict[int, tuple[float, float]]] = {}
    for lineage, day, open_, close, passes in zip(
        tape["lineage"].astype(str), tape["date"], tape["px_open"], tape["px_close"], tape["passes"], strict=False
    ):
        slot = index.get(as_date(day))
        if slot is None:
            continue
        by_lineage.setdefault(lineage, {})[slot] = (float(open_), float(close), bool(passes))
    out = {}
    for lineage, bars in by_lineage.items():
        for slot, (_open, _close, passes) in bars.items():
            if not passes:
                continue
            entry = bars.get(slot + 1)
            exit_ = bars.get(slot + 5)
            if entry is None or exit_ is None or entry[0] <= 0:
                continue
            out[(lineage, calendar[slot])] = exit_[1] / entry[0] - 1.0
    return out


def _matched_universe(trades: pd.DataFrame, universe: dict) -> float:
    if trades is None or trades.empty:
        return float("nan")
    values = []
    signal_days = []
    for day in trades["signal_date"]:
        signal_days.append(date.fromisoformat(day) if isinstance(day, str) else as_date(day))
    by_day: dict[date, list[float]] = {}
    for (lineage, day), value in universe.items():
        del lineage
        by_day.setdefault(day, []).append(value)
    taken = [float(np.mean(by_day[day])) for day in signal_days if day in by_day]
    values.extend(taken)
    if not values:
        return float("nan")
    return float(np.mean(values))


def _window_universe(tape, calendar, universe, start: date, end: date) -> float:
    del tape, calendar
    values = [value for (_lineage, day), value in universe.items() if start <= day <= end]
    if not values:
        return float("nan")
    return float(np.mean(values))


def _random_gross(kind, bars, trades, pool, blocked, prev_of, costs, calendar, flags, ends, lookup, start, end):
    if trades is None or trades.empty:
        return []
    counts: dict[date, int] = {}
    for day in trades["entry_date"]:
        stamp = date.fromisoformat(day) if isinstance(day, str) else as_date(day)
        counts[stamp] = counts.get(stamp, 0) + 1
    spec = BookSpec(GROSS_CAPITAL, PRIMARY_SLOTS, start, end, "zero", slip_liquid=Decimal(0), slip_other=Decimal(0))
    found = []
    for seed in range(1, 21):
        generated = random_signals(list(counts.items()), pool, seed, kind, prev_of, blocked)
        result = run_research(
            bars, generated, costs, spec, calendar=calendar, flags=flags, week_ends=ends, lookup=lookup,
        )
        avg, win, _count = _avg_win(result["trades"])
        found.append((avg, win))
        print(f"  random {kind} {start.year} seed {seed}: avg {avg*100:.2f}% win {win*100:.1f}%", flush=True)
    return found


def _yearly(rows, nifty, prices, flags) -> list[str]:
    del prices, flags
    lines = ["| Year | S2 return | Nifty return | Gap | Trades |", "|---|---:|---:|---:|---:|"]
    nifty = nifty.copy()
    nifty["date"] = nifty["date"].map(as_date)
    nifty["year"] = nifty["date"].map(lambda day: day.year)
    for name, result, _start, _end in rows:
        daily = result["daily"]
        trades = result["trades"]
        if daily.empty:
            continue
        curve = daily.copy()
        curve["year"] = curve["date"].str.slice(0, 4).astype(int)
        for year, part in curve.groupby("year"):
            start_eq = float(part["equity"].iloc[0])
            end_eq = float(part["equity"].iloc[-1])
            # First row is already marked. Use the previous year's last equity when present.
            ret = end_eq / start_eq - 1.0 if start_eq > 0 else float("nan")
            year_nifty = nifty[nifty["year"] == int(year)]
            if year_nifty.empty or not (year_nifty["open"].iloc[0] > 0):
                nret = float("nan")
            else:
                nret = float(year_nifty["close"].iloc[-1] / year_nifty["open"].iloc[0] - 1.0)
            gap = ret - nret
            booked = trades[trades["exit_date"].str.startswith(str(year))] if not trades.empty else trades
            lines.append(f"| {name} {int(year)} | {_pct(ret)} | {_pct(nret)} | {_pct(gap)} | {0 if booked is None else len(booked)} |")
            print(f"s2 {name} {int(year)}: book {ret*100:.1f}% nifty {nret*100:.1f}% gap {gap*100:.1f} trades {0 if booked is None else len(booked)}", flush=True)
            if gap < -0.30:
                lines.append("")
                lines.append(f"### {name} {int(year)} trades, S2 more than 30 points under the index")
                lines.append("")
                lines.extend(_trade_dump(booked))
                lines.append("")
    return lines


def _trade_dump(trades: pd.DataFrame) -> list[str]:
    if trades is None or trades.empty:
        return ["(none)"]
    lines = ["| Symbol | Entry | Exit | Reason | Net | Price move |", "|---|---|---|---|---:|---:|"]
    work = trades.copy()
    work["ret"] = work["pnl"] / work["notional"]
    work["price_ret"] = work["exit_px"] / work["entry_open"] - 1.0
    for row in work.sort_values("ret").itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {row.entry_date} | {row.exit_date} | {row.reason} | {row.ret*100:.1f}% | {row.price_ret*100:.1f}% |"
        )
    return lines


def _pct(value) -> str:
    if value is None or value != value:
        return "—"
    return f"{float(value) * 100:.1f}%"


if __name__ == "__main__":
    sys.exit(main())
