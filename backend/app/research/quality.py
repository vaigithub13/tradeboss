"""Per-symbol research candle quality: coverage, missing days, gaps, split-jump checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from app.backtest.expiry import load_default_calendar
from app.data.history import read_meta
from app.research.corporate_actions import ADJ_NAME, LEDGER_NAME, RAW_NAME, CorporateAction

JUMP_PCT = 0.20  # overnight move without a matching corporate action


def _parquet_sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


@dataclass(frozen=True)
class SymbolQuality:
    symbol: str
    folder: str
    role: str
    instrument_key: str
    rows: int
    first_date: str | None
    last_date: str | None
    trading_days_expected: int
    days_present: int
    missing_days: int
    missing_sample: list[str]
    gap_count: int
    largest_gap_days: int
    split_jumps: int
    split_jump_sample: list[str]
    adjustments_applied: int
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _session_dates(symbol_dir: Path, parquet_name: str = RAW_NAME) -> list[date]:
    path = symbol_dir / parquet_name
    if not path.is_file():
        return []
    q = _parquet_sql_path(path)
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT CAST(timezone('Asia/Kolkata', to_timestamp(time)) AS DATE) AS d "
            f"FROM read_parquet('{q}') WHERE session_type = 'normal' ORDER BY d"
        ).fetchall()
    finally:
        con.close()
    return [r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0])) for r in rows]


def _daily_closes(symbol_dir: Path, parquet_name: str) -> list[tuple[date, float]]:
    path = symbol_dir / parquet_name
    if not path.is_file():
        return []
    q = _parquet_sql_path(path)
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"""
            SELECT CAST(timezone('Asia/Kolkata', to_timestamp(time)) AS DATE) AS d,
                   arg_max(close, time) AS close
            FROM read_parquet('{q}')
            WHERE session_type = 'normal'
            GROUP BY 1
            ORDER BY 1
            """
        ).fetchall()
    finally:
        con.close()
    out: list[tuple[date, float]] = []
    for d, close in rows:
        day = d if isinstance(d, date) else date.fromisoformat(str(d))
        out.append((day, float(close)))
    return out


def _expected_trading_days(start: date, end: date) -> list[date]:
    cal = load_default_calendar()
    days: list[date] = []
    cur = start
    while cur <= end:
        if cal.is_trading_day(cur):
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _gaps(sorted_days: list[date], expected: set[date]) -> tuple[int, int]:
    """(gap_count, largest missing-trading-day run between consecutive present days).

    Weekends/holidays alone do not count; only holes that skip expected NSE trading days.
    """
    if len(sorted_days) < 2:
        return 0, 0
    count = 0
    largest = 0
    for a, b in zip(sorted_days, sorted_days[1:], strict=False):
        missing = sum(1 for d in expected if a < d < b)
        if missing > 0:
            count += 1
            largest = max(largest, missing)
    return count, largest


def _ca_ex_dates(actions: list[CorporateAction]) -> set[date]:
    return {a.ex_date for a in actions if a.kind.lower() in ("split", "bonus") and a.price_factor}


def detect_split_jumps(
    closes: list[tuple[date, float]],
    ca_dates: set[date],
    *,
    threshold: float = JUMP_PCT,
) -> list[str]:
    """Overnight |return| > threshold with no Split/Bonus ex-date on the later day."""
    hits: list[str] = []
    for (d0, c0), (d1, c1) in zip(closes, closes[1:], strict=False):
        if c0 <= 0:
            continue
        move = abs(c1 / c0 - 1.0)
        if move > threshold and d1 not in ca_dates:
            hits.append(f"{d0.isoformat()}→{d1.isoformat()} {move * 100:.1f}%")
    return hits


def assess_symbol(
    *,
    symbol: str,
    folder: str,
    role: str,
    instrument_key: str,
    candles_dir: Path,
    history_from: date,
    history_to: date,
    actions: list[CorporateAction] | None = None,
) -> SymbolQuality:
    symbol_dir = candles_dir / folder
    notes: list[str] = []
    meta = read_meta(symbol_dir) if symbol_dir.is_dir() else None
    days = _session_dates(symbol_dir, RAW_NAME)
    rows = 0
    raw = symbol_dir / RAW_NAME
    if raw.is_file():
        con = duckdb.connect()
        try:
            rows = int(con.execute(f"SELECT count(*) FROM read_parquet('{_parquet_sql_path(raw)}')").fetchone()[0])
        finally:
            con.close()
    expected = _expected_trading_days(history_from, history_to)
    expected_set = set(expected)
    present = set(days)
    missing = [d for d in expected if d not in present]
    gap_count, largest_gap = _gaps(days, expected_set)
    import json

    actions = list(actions or [])
    ledger = symbol_dir / LEDGER_NAME
    applied = 0
    ca_dates = _ca_ex_dates(actions)
    if ledger.is_file():
        body = json.loads(ledger.read_text(encoding="utf-8"))
        applied = len(body.get("applied_splits_bonuses") or [])
        if not ca_dates:
            for row in body.get("applied_splits_bonuses") or []:
                try:
                    ca_dates.add(date.fromisoformat(str(row["ex_date"])))
                except (KeyError, ValueError):
                    continue
    closes = _daily_closes(symbol_dir, ADJ_NAME if (symbol_dir / ADJ_NAME).is_file() else RAW_NAME)
    jumps = detect_split_jumps(closes, ca_dates) if role == "equity" else []
    if role == "equity" and not (symbol_dir / ADJ_NAME).is_file():
        notes.append("no 1m_adj.parquet yet")
    if meta and meta.source_empty:
        notes.append(f"{len(meta.source_empty)} source_empty window(s)")
    return SymbolQuality(
        symbol=symbol,
        folder=folder,
        role=role,
        instrument_key=instrument_key,
        rows=rows,
        first_date=None if not days else days[0].isoformat(),
        last_date=None if not days else days[-1].isoformat(),
        trading_days_expected=len(expected),
        days_present=len([d for d in days if history_from <= d <= history_to]),
        missing_days=len(missing),
        missing_sample=[d.isoformat() for d in missing[:8]],
        gap_count=gap_count,
        largest_gap_days=largest_gap,
        split_jumps=len(jumps),
        split_jump_sample=jumps[:5],
        adjustments_applied=applied,
        notes=notes,
    )


def render_quality_markdown(
    *,
    as_of: str,
    history_from: str,
    history_to: str,
    unresolved: list[str],
    rows: list[SymbolQuality],
) -> str:
    lines = [
        f"# Research universe v1 quality ({as_of})",
        "",
        f"History window `{history_from}` .. `{history_to}` (normal sessions vs NSE holiday calendar).",
        f"Split-jump threshold: overnight move > {JUMP_PCT * 100:.0f}% without a matching Split/Bonus ex-date.",
        "Corporate actions: Upstox `/v2/fundamentals/{ISIN}/corporate-actions` (recent events only; dividends recorded, splits/bonuses applied to `1m_adj.parquet`).",
        "Gaps = runs of missing expected trading days between present sessions (weekends alone do not count).",
        "",
        "## Unresolved symbols",
        "",
    ]
    if unresolved:
        for u in unresolved:
            lines.append(f"- **{u}** (not substituted)")
    else:
        lines.append("- (none)")
    lines += [
        "",
        "## Coverage",
        "",
        "| symbol | role | rows | first | last | expected days | present | missing | gaps | max gap d | jumps | adj | notes |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        notes = "; ".join(r.notes) if r.notes else ""
        if r.missing_sample:
            notes = (notes + "; " if notes else "") + "missing e.g. " + ", ".join(r.missing_sample[:3])
        if r.split_jump_sample:
            notes = (notes + "; " if notes else "") + "jumps " + "; ".join(r.split_jump_sample[:2])
        lines.append(
            f"| {r.symbol} | {r.role} | {r.rows} | {r.first_date or '—'} | {r.last_date or '—'} | "
            f"{r.trading_days_expected} | {r.days_present} | {r.missing_days} | {r.gap_count} | "
            f"{r.largest_gap_days} | {r.split_jumps} | {r.adjustments_applied} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)
