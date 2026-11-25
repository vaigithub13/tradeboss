"""Swing Phase 2 worker: delivery-cost dry run for 2016–2018.

Research only. This process is not the backend and it does not place orders.
Nothing under data/ is committed.

    cd backend && uv run python -m scripts.swing_phase2
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from app.config import settings
from app.data.history import read_parquet, symbol_dir_name
from app.swing.bhav_pit import market_hours_block
from app.swing.daily import IST
from app.swing.delivery import DeliveryTable
from app.swing.engine import DRY_RUN_END, DRY_RUN_START, EngineConfig, research_window, run_breakout
from app.swing.fixes import (
    apply_ohlc,
    as_date,
    event_flags,
    is_mismatch,
    mismatch_stats,
    price_step,
    research_prices,
    subjects_near,
    substitution_reason,
    turnover_units,
)
from app.swing.quality import series_path

UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "app" / "swing" / "data" / "swing_universe_v1.json"
NIFTY_500 = "NSE_INDEX|Nifty 500"


def live_block(live: bool) -> str | None:
    if live:
        return "LIVE_TRADING is true. Swing Phase 2 does not run."
    return None


def main(argv: list[str] | None = None) -> int:
    del argv
    blocked = live_block(settings.live_trading)
    if blocked:
        print(blocked, file=sys.stderr)
        return 2
    hours = market_hours_block(datetime.now(IST))
    if hours:
        print("Swing Phase 2 does not run during 09:00–16:10 IST.", file=sys.stderr)
        return 2
    window = research_window(DRY_RUN_START, DRY_RUN_END)
    if window:
        print(window, file=sys.stderr)
        return 2

    universe_path = settings.bhavcopy_dir / "universe.parquet"
    if not universe_path.is_file():
        print("bhavcopy universe is missing. Run Swing Phase 1b first.", file=sys.stderr)
        return 1

    print("loading 2015–2018 bars", flush=True)
    bars = _load_bars(universe_path, date(2015, 1, 1), DRY_RUN_END)
    bars = research_prices(bars)
    print(f"bars {len(bars)}", flush=True)

    print("turnover units around the 2024 format change", flush=True)
    units = turnover_units(_load_turnover(universe_path))

    stocks = json.loads(UNIVERSE_PATH.read_text())["stocks"]
    print("comparing mismatched names with Upstox", flush=True)
    replaced, examined, skipped = _substitute(bars, stocks, universe_path)
    print(f"substituted {len(replaced)} of {examined} mismatches", flush=True)

    nifty = _nifty()
    if nifty.empty:
        print("Nifty 500 daily bars are missing.", file=sys.stderr)
        return 1

    calendar = sorted({as_date(day) for day in bars["date"]})
    keepers = set(
        bars.loc[
            bars["passes"].fillna(False).astype(bool) & (bars["date"].map(as_date) >= DRY_RUN_START),
            "lineage",
        ].astype(str)
    )
    tradable = bars.loc[bars["lineage"].astype(str).isin(keepers)].copy()
    print(f"lineages that pass at least once: {len(keepers)}", flush=True)
    flags = event_flags(tradable)
    costs = DeliveryTable.load()
    result = run_breakout(
        tradable,
        nifty,
        costs,
        EngineConfig(),
        calendar=calendar,
        flags=flags,
    )
    problems = _sanity(result, flags)
    out_dir = settings.data_dir / "swing_phase2"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = settings.data_dir / "validation" / "swing_phase2_2016_2018.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    text = _report(result, replaced, examined, skipped, units, flags, costs, problems)
    report_path.write_text(text)
    _write(out_dir, result, replaced, skipped, units, flags)
    print(text)
    print(f"wrote {report_path}")
    return 1 if problems else 0


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


def _load_bars(path: Path, start: date, end: date) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"""
            SELECT CAST(date AS DATE) AS date, symbol, series, isin, lineage,
                   open, high, low, close, volume, turnover,
                   adj_open, adj_high, adj_low, adj_close,
                   factor_today, passes, size
            FROM read_parquet('{_quote(path)}')
            WHERE date >= DATE '{start.isoformat()}' AND date <= DATE '{end.isoformat()}'
            """
        ).df()
    finally:
        con.close()
    frame["date"] = frame["date"].map(as_date)
    frame["lineage"] = frame["lineage"].astype(str)
    return frame


def _load_turnover(path: Path) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"""
            SELECT CAST(date AS DATE) AS date, series, close, volume, turnover
            FROM read_parquet('{_quote(path)}')
            WHERE date BETWEEN DATE '2024-06-01' AND DATE '2024-08-31'
              AND series IN ('EQ', 'BE')
            """
        ).df()
    finally:
        con.close()
    return frame


def _nifty() -> pd.Series:
    path = series_path(settings.candles_daily_dir / symbol_dir_name(NIFTY_500))
    if not path.is_file():
        return pd.Series(dtype=float)
    frame = read_parquet(path)
    dates = pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    return pd.Series(frame["close"].astype(float).to_numpy(), index=pd.Index(list(dates)), name="close")


def _lineage_history(path: Path, isins: list[str]) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        con.execute("CREATE TEMP TABLE want(isin VARCHAR)")
        con.executemany("INSERT INTO want VALUES (?)", [(isin,) for isin in isins])
        frame = con.execute(
            f"""
            SELECT CAST(date AS DATE) AS date, lineage, symbol, isin, adj_close
            FROM read_parquet('{_quote(path)}')
            WHERE lineage IN (
                SELECT DISTINCT lineage FROM read_parquet('{_quote(path)}')
                WHERE isin IN (SELECT isin FROM want)
            )
            """
        ).df()
    finally:
        con.close()
    frame["date"] = frame["date"].map(as_date)
    frame["lineage"] = frame["lineage"].astype(str)
    return frame


def _upstox_frame(stock: dict) -> pd.DataFrame | None:
    path = series_path(settings.candles_daily_dir / symbol_dir_name(str(stock["instrument_key"])))
    if not path.is_file():
        return None
    frame = read_parquet(path)
    dates = pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    return pd.DataFrame(
        {
            "date": list(dates),
            "open": frame["open"].astype(float).to_numpy(),
            "high": frame["high"].astype(float).to_numpy(),
            "low": frame["low"].astype(float).to_numpy(),
            "close": frame["close"].astype(float).to_numpy(),
        }
    )


def _actions_around(when: date, cache: dict[str, list[dict]]) -> list[dict]:
    found: list[dict] = []
    year, month = when.year, when.month
    for shift in (-1, 0, 1):
        slot = month - 1 + shift
        y = year + slot // 12
        m = slot % 12 + 1
        key = f"{y:04d}-{m:02d}"
        if key not in cache:
            file = settings.bhavcopy_dir / "ca" / f"{key}.json"
            cache[key] = json.loads(file.read_text()) if file.is_file() else []
        found.extend(cache[key])
    return found


def _substitute(bars: pd.DataFrame, stocks: list[dict], universe: Path) -> tuple[list[dict], int, list[dict]]:
    history = _lineage_history(universe, [row["isin"] for row in stocks if row.get("isin")])
    grouped = {key: part.drop_duplicates("date").set_index("date") for key, part in history.groupby("lineage", sort=False)}
    isin_lineage = dict(zip(history["isin"].astype(str), history["lineage"].astype(str), strict=False))
    cache: dict[str, list[dict]] = {}
    replaced = []
    skipped = []
    examined = 0
    for stock in stocks:
        lineage = isin_lineage.get(str(stock.get("isin") or ""))
        part = grouped.get(str(lineage)) if lineage else None
        other = _upstox_frame(stock)
        if part is None or other is None:
            continue
        closes = pd.Series(other["close"].to_numpy(), index=pd.Index(other["date"]))
        stats = mismatch_stats(part["adj_close"], closes)
        if not is_mismatch(stats):
            continue
        examined += 1
        step = price_step(part["adj_close"], closes)
        subjects: list[str] = []
        if step:
            day_symbol = str(part["symbol"].get(step["date"], stock["symbol"])) if "symbol" in part.columns else stock["symbol"]
            subjects = subjects_near(_actions_around(step["date"], cache), step["date"], str(stock["symbol"]), str(stock["isin"]))
            subjects += subjects_near(_actions_around(step["date"], cache), step["date"], day_symbol, str(stock["isin"]))
        reason = substitution_reason(step, subjects)
        record = {
            "symbol": stock["symbol"],
            "isin": stock["isin"],
            "lineage": str(lineage),
            "event_date": step["date"].isoformat() if step else "",
            "step": None if step is None else round(float(step["magnitude"]), 4),
            "left_gap": None if step is None or step.get("left_gap") is None else round(float(step["left_gap"]), 4),
            "median_abs_pct_before": float(stats["median_abs_pct"]),
            "overlap": int(stats["overlap"]),
        }
        if reason is None:
            skipped.append(record)
            continue
        record["reason"] = reason
        record["sessions_replaced"] = apply_ohlc(bars, str(lineage), other)
        replaced.append(record)
    return replaced, examined, skipped


def _sanity(result: dict, flags: pd.DataFrame) -> list[str]:
    trades = result["trades"]
    problems = []
    if trades.empty:
        return ["the dry run produced no trades"]
    if (trades["entry_date"] <= trades["signal_date"]).any():
        problems.append("a fill is on or before the signal day")
    if (trades["entry_date"] < "2016-01-01").any() or (trades["exit_date"] > "2018-12-31").any():
        problems.append("a trade sits outside 2016-01-01..2018-12-31")
    if not flags.empty:
        flagged = {(str(lineage), as_date(day).isoformat()) for lineage, day in zip(flags["lineage"], flags["date"], strict=False)}
        held = 0
        for trade in trades.itertuples(index=False):
            # A flag strictly between the entry and the exit means the book held across it.
            for lineage, day in flagged:
                if lineage == trade.lineage and trade.entry_date < day < trade.exit_date:
                    held += 1
                    break
        if held:
            problems.append(f"{held} trades are open across a flagged session")
    return problems


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    """DuckDB writes the parquet. pandas has no parquet engine in this environment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    for column in out.columns:
        if len(out) and out[column].dtype == object and isinstance(out[column].iloc[0], date):
            out[column] = pd.to_datetime(out[column])
    tmp = path.with_suffix(".parquet.tmp")
    escaped = str(tmp).replace("'", "''")
    con = duckdb.connect()
    try:
        con.register("swing_frame", out)
        con.execute(f"COPY (SELECT * FROM swing_frame) TO '{escaped}' (FORMAT PARQUET)")
    finally:
        con.close()
    tmp.replace(path)


def _write(out_dir: Path, result: dict, replaced: list[dict], skipped: list[dict], units: dict, flags: pd.DataFrame) -> None:
    _write_frame(result["trades"], out_dir / "trades.parquet")
    _write_frame(result["daily"], out_dir / "daily.parquet")
    result["by_size"].to_json(out_dir / "by_size.json", orient="records")
    result["by_year"].to_json(out_dir / "by_year.json", orient="records")
    (out_dir / "substitutions.json").write_text(json.dumps(replaced, indent=2))
    (out_dir / "not_substituted.json").write_text(json.dumps(skipped, indent=2))
    (out_dir / "turnover_units.json").write_text(json.dumps(units, indent=2))
    _write_frame(flags, out_dir / "event_flags.parquet")


def _report(result, replaced, examined, skipped, units, flags, costs: DeliveryTable, problems: list[str]) -> str:
    trades = result["trades"]
    daily = result["daily"]
    lines = [
        "# Swing Phase 2 dry run, 2016–2018",
        "",
        "Research only. No order was sent. The book stops at 2018-12-31.",
        "",
        "## Turnover units",
        "",
        f"Median (turnover / volume) / close for EQ and BE from 2024-06-01 through 2024-08-31.",
        f"Before 2024-07-08: {units['before']}. From 2024-07-08: {units['after']}.",
        f"After / before: {units['after_over_before']}. Same units: {units['same_units']}.",
        "No rescaling was applied.",
        "",
        "## Substitutions",
        "",
        f"Names with a Phase 1b mismatch against Upstox: {examined}.",
        f"Replaced with the Upstox daily bar because one step lines up with a demerger, scheme, or rights subject: {len(replaced)}.",
        f"Left on the bhavcopy, because the gap is not that kind of step: {len(skipped)}.",
        "",
    ]
    if replaced:
        lines.append("| Symbol | Event | Step | Gap before the step | Overlap median | Sessions in 2015–2018 |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for row in sorted(replaced, key=lambda item: -(item["left_gap"] or 0)):
            lines.append(
                f"| {row['symbol']} | {row['event_date']} {row['reason']} | {row['step']:.1%} | "
                f"{(row['left_gap'] or 0):.1%} | {row['median_abs_pct_before']:.1%} | {row['sessions_replaced']} |"
            )
        lines.append("")
    lines.append(
        "An overlap median near zero means the bars after the event already match Upstox. "
        "The gap before the step is the part this dry run trades when the event is in 2019 or later."
    )
    lines.append(
        "The other 42 mismatches are not one demerger, scheme, or rights step. "
        "VEDL, IOC, and SUZLON are a drift of several percent. "
        "CGCL has an 80% step on 2016-11-23 with no such subject, so its bhavcopy prices stay and the event guard flags the gap."
    )
    lines.append("")
    lines += [
        "## Event guard",
        "",
        f"Flagged sessions (research open more than 15% from the previous research close): {len(flags)}.",
        "A position that would still be open on a flagged session is sold at the previous close.",
        "The flagged session and the next two sessions take no new entry.",
        "A lower-circuit lock (open = high = low = close, below the previous close) is not sold that day.",
        "",
        "## Delivery costs",
        "",
        "Source page retrieved 2026-11-26: https://upstox.com/brokerage-charges/",
        "Every rate is unverified. No contract note was checked. The 2016 stamp, service tax, DP, and the pre-2021 exchange rate are the rows a 2016–2018 fill uses.",
        "",
        "| From | Brokerage | STT both | Exchange | Stamp buy | GST | DP sell | IPFT |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in costs.schedule():
        lines.append(
            f"| {row['effective_from']} | {row['brokerage']} | {row['stt_both']} | {row['exchange']} | "
            f"{row['stamp_buy']} | {row['gst']} | {row['dp_sell']} | {row['ipft']} |"
        )
    lines += ["", "Slippage is 0.10% per side for large and mid (top 250 by trailing turnover that day) and 0.25% for the rest.", ""]
    lines += _book_section(trades, daily, result)
    if problems:
        lines += ["", "## Sanity", ""]
        lines.extend(f"- {item}" for item in problems)
    lines.append("")
    return "\n".join(lines)


def _book_section(trades: pd.DataFrame, daily: pd.DataFrame, result: dict) -> list[str]:
    if trades.empty:
        return ["## Book", "", "No trades."]
    end_equity = float(daily["equity"].iloc[-1]) if not daily.empty else float("nan")
    lines = [
        "## Book",
        "",
        f"Trades {len(trades)}. Realised P&L ₹{trades['pnl'].sum():,.0f}. "
        f"Ending equity ₹{end_equity:,.0f} on capital ₹50,000. "
        f"Mean exposure {daily['exposure'].mean():.1%}. Max positions on a day {int(daily['positions'].max())}.",
        "",
        "### By size",
        "",
        _table(result["by_size"]),
        "",
        "### By year",
        "",
        _table(result["by_year"]),
        "",
        "### Sample",
        "",
        _table(trades.head(8)),
        "",
    ]
    reasons = trades["reason"].value_counts()
    lines.append("Exits: " + ", ".join(f"{name} {int(count)}" for name, count in reasons.items()) + ".")
    return lines


def _table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "(none)"
    show = frame.copy()
    for column in show.columns:
        if show[column].dtype.kind == "f":
            show[column] = show[column].map(lambda value: f"{value:.4g}")
    header = "| " + " | ".join(show.columns) + " |"
    rule = "|" + "|".join("---" for _ in show.columns) + "|"
    body = ["| " + " | ".join(str(value) for value in row) + " |" for row in show.itertuples(index=False)]
    return "\n".join([header, rule, *body])


if __name__ == "__main__":
    sys.exit(main())
