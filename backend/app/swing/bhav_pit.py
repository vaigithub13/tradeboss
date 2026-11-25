"""Point-in-time filters on the bhavcopy universe.

A day sees that lineage's own sessions up to that day. Adjusted prices are not
an input. The 20% flag is the ASM/GSM proxy: a confirmed split is taken out of
the move before the flag is set.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

TURNOVER_MIN = 50_000_000.0  # ₹5 crore
PRICE_MIN = 50.0
LISTED_SESSIONS = 250
TURNOVER_WINDOW = 20
MOVE_LIMIT = 0.20
MOVE_WINDOW = 5
DELISTED_AFTER_DAYS = 10

SIZE_LARGE = 100
SIZE_MID = 250
SIZE_SMALL = 500


def apply_pit(frame: pd.DataFrame) -> pd.DataFrame:
    """Adds the rolling flags. `passes` is turnover, price, history, and a quiet last 5 sessions."""
    if frame.empty:
        out = frame.copy()
        for column, value in (
            ("turnover_20d", float("nan")),
            ("sessions", 0),
            ("big_move", False),
            ("passes", False),
            ("size", "rest"),
        ):
            out[column] = value
        return out
    work = frame.sort_values(["lineage", "date"]).reset_index(drop=True)
    grouped = work.groupby("lineage", sort=False)
    work["turnover_20d"] = grouped["turnover"].transform(
        lambda series: series.rolling(TURNOVER_WINDOW, min_periods=TURNOVER_WINDOW).mean()
    )
    work["sessions"] = grouped.cumcount() + 1
    gross = work["close"].astype(float) / work["prev_close"].astype(float)
    economic = gross.copy()
    adjusted = work["factor_today"].astype(float) != 1.0
    economic.loc[adjusted] = gross.loc[adjusted] / work.loc[adjusted, "factor_today"].astype(float)
    work["abs_move"] = (economic - 1.0).abs()
    work["big_move"] = work.groupby("lineage", sort=False)["abs_move"].transform(
        lambda series: series.rolling(MOVE_WINDOW, min_periods=1).max() > MOVE_LIMIT
    )
    work["big_move"] = work["big_move"].fillna(False)
    history = work["sessions"] >= LISTED_SESSIONS
    liquid = work["turnover_20d"] >= TURNOVER_MIN
    priced = work["close"].astype(float) >= PRICE_MIN
    work["passes"] = history & liquid & priced & ~work["big_move"]
    work["proxy_only"] = history & liquid & priced & work["big_move"]
    work["size"] = "rest"
    eligible = work["turnover_20d"].notna()
    ranks = work.loc[eligible].groupby("date")["turnover_20d"].rank(ascending=False, method="min")
    work.loc[eligible, "size"] = ranks.map(_size_tag)
    return work


def _size_tag(rank: float) -> str:
    if rank <= SIZE_LARGE:
        return "large"
    if rank <= SIZE_MID:
        return "mid"
    if rank <= SIZE_SMALL:
        return "small"
    return "rest"


def summarize_years(
    frame: pd.DataFrame,
    nifty_isins: set[str],
    nifty_symbols: set[str],
) -> list[dict]:
    """Last session of each year. Outside-500 and later-delisted are counted on the passers."""
    if frame.empty:
        return []
    work = frame.copy()
    work["date"] = work["date"].map(_as_date)
    global_last = max(work["date"])
    lineage_last = work.groupby("lineage")["date"].max().to_dict()
    lineage_isins: dict[str, set[str]] = {}
    for lineage, isin in zip(work["lineage"], work["isin"], strict=True):
        lineage_isins.setdefault(str(lineage), set()).add(str(isin))
    last_symbol = (
        work.sort_values("date")
        .groupby("lineage", sort=False)["symbol"]
        .last()
        .to_dict()
    )
    in_today = {
        lineage: _still_in_index(
            lineage_isins.get(str(lineage), set()),
            lineage_last[lineage],
            str(last_symbol.get(lineage, "")),
            global_last,
            nifty_isins,
            nifty_symbols,
        )
        for lineage in lineage_last
    }
    delisted = {
        lineage: (global_last - lineage_last[lineage]).days > DELISTED_AFTER_DAYS
        for lineage in lineage_last
    }
    rows = []
    for year, part in work.groupby(work["date"].map(lambda day: day.year)):
        as_of = max(part["date"])
        day = part.loc[part["date"] == as_of]
        passed = day.loc[day["passes"].astype(bool)]
        counts = passed["size"].value_counts().to_dict() if not passed.empty else {}
        outside = 0
        later = 0
        for lineage in passed["lineage"]:
            if not in_today.get(lineage, False):
                outside += 1
            if delisted.get(lineage, False):
                later += 1
        rows.append({
            "year": int(year),
            "as_of": as_of,
            "passed": int(len(passed)),
            "large": int(counts.get("large", 0)),
            "mid": int(counts.get("mid", 0)),
            "small": int(counts.get("small", 0)),
            "rest": int(counts.get("rest", 0)),
            "outside_nifty500": outside,
            "later_delisted": later,
            "proxy_only": int(day["proxy_only"].sum()) if "proxy_only" in day.columns else 0,
        })
    return rows


def _still_in_index(
    isins: set[str],
    last_day: date,
    last_symbol: str,
    global_last: date,
    nifty_isins: set[str],
    nifty_symbols: set[str],
) -> bool:
    if isins & nifty_isins:
        return True
    still_trading = (global_last - last_day).days <= DELISTED_AFTER_DAYS
    return still_trading and last_symbol in nifty_symbols


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def render_report(
    *,
    sessions: list[date],
    weekday_absent: int,
    lineages: int,
    renamed: int,
    ledger: list[dict],
    ca_note: str,
    years: list[dict],
    mismatches: dict,
) -> str:
    first = sessions[0].isoformat() if sessions else "—"
    last = sessions[-1].isoformat() if sessions else "—"
    applied = [row for row in ledger if row.get("applied")]
    rejected = [row for row in ledger if not row.get("applied")]
    lines = [
        "# Swing Phase 1b quality",
        "",
        "The trading calendar is the set of dates in the NSE cash bhavcopy. "
        "It replaces the 2022+ holiday file for this daily research. "
        "No name passes in 2015: the files start on 2015-01-01, so no lineage has 250 sessions by the end of that year.",
        "",
        f"Sessions: {len(sessions)} ({first} through {last}). "
        f"Weekdays with no file: {weekday_absent}.",
        f"Lineages: {lineages}. Lineages that changed symbol: {renamed}.",
        f"Adjustments applied: {len(applied)}. Recorded and not applied: {len(rejected)}.",
        ca_note,
        "",
        "A name is outside today's Nifty 500 when none of its ISINs is on that list. "
        "A name is later delisted when its last bhavcopy is more than "
        f"{DELISTED_AFTER_DAYS} calendar days before the last session. "
        "The two counts can overlap. The proxy column is names that cleared turnover, "
        "price, and history and were flagged for a move above 20% in the last 5 sessions.",
        "",
        "| Year | As of | Large | Mid | Small | Rest | Outside today's 500 | Later delisted | Proxy excluded |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in years:
        lines.append(
            f"| {row['year']} | {row['as_of'].isoformat()} | {row['large']} | {row['mid']} | "
            f"{row['small']} | {row['rest']} | {row['outside_nifty500']} | {row['later_delisted']} | "
            f"{row['proxy_only']} |"
        )
    lines.extend(["", "## Upstox daily bars, current Nifty 500", ""])
    lines.append(mismatches.get("summary", "No comparison was run."))
    worst = mismatches.get("worst") or []
    if worst:
        lines.append("")
        lines.append("| Symbol | Overlap | Median abs % | Days over 1% |")
        lines.append("|---|---:|---:|---:|")
        for row in worst:
            lines.append(
                f"| {row['symbol']} | {row['overlap']} | {row['median_abs_pct']:.2f} | {row['over_tol']} |"
            )
    lines.append("")
    return "\n".join(lines)


def market_hours_block(now) -> str | None:
    """Weekday 09:00 through 16:10 IST. `now` is timezone-aware."""
    local = now
    if local.weekday() >= 5:
        return None
    minutes = local.hour * 60 + local.minute
    if 9 * 60 <= minutes <= 16 * 60 + 10:
        return "Swing Phase 1b does not download during 09:00–16:10 IST."
    return None


def weekday_absent(start: date, end: date, sessions: set[date]) -> int:
    """Weekdays on or before the last session that have no bhavcopy."""
    if not sessions:
        return 0
    last = max(sessions)
    count = 0
    day = start
    while day <= end and day <= last:
        if day.weekday() < 5 and day not in sessions:
            count += 1
        day += timedelta(days=1)
    return count
