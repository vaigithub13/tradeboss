"""Data fixes for the swing research series.

Three jobs, all offline:

- Prefer the Upstox daily bar where a mismatch against the bhavcopy is one
  level shift and a corporate-action subject explains it (demerger, scheme,
  rights). A drifting residual is left on the bhavcopy.
- Flag an overnight move above 15% on the research series. A move that the
  adjustments and substitutions already removed does not flag. The engine
  refuses to hold across a flagged session.
- Compare bhavcopy turnover units on either side of the 2024 file-format
  change. The check reports the ratio. It does not rescale.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from app.swing.bhav_adjust import compare_closes

UDIFF_FROM = date(2024, 7, 8)
EVENT_MOVE = 0.15
STEP_MIN = 0.10
RIGHT_STABLE = 0.02
STEP_EDGE = 5
SUBJECT_WINDOW_DAYS = 7
SPECIAL_WORDS = (
    "demerger",
    "scheme",
    "arrangement",
    "spin-off",
    "spin off",
    "spinoff",
    "reduction of capital",
    "amalgam",
    "merger",
    "rights",
)


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def research_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """`px_*` is the series the engine trades. Adjusted bhavcopy, unless a substitution overwrote it."""
    out = frame.copy()
    for name in ("open", "high", "low", "close"):
        column = f"px_{name}"
        if column in out.columns:
            continue
        adjusted = f"adj_{name}"
        out[column] = out[adjusted] if adjusted in out.columns else out[name]
    return out


def price_step(bhav: pd.Series, other: pd.Series) -> dict | None:
    """The largest one-day jump in bhav/other, and how quiet the ratio is after it.

    `bhav` and `other` are indexed by date. Returns None when the overlap is
    too short to judge.
    """
    both = pd.concat([bhav.rename("bhav"), other.rename("other")], axis=1, join="inner")
    both = both.loc[(both["bhav"] > 0) & (both["other"] > 0)]
    if len(both) < 20:
        return None
    ratio = both["bhav"] / both["other"]
    jump = ratio.pct_change().abs().dropna()
    if jump.empty:
        return None
    ordered = jump.sort_values(ascending=False)
    when = as_date(ordered.index[0])
    right = ratio.loc[ordered.index[0] :]
    median = float(right.median())
    deviation = None if median == 0 else float((right / median - 1).abs().median())
    position = list(ratio.index).index(ordered.index[0])
    left = ratio.iloc[:position]
    left_gap = float((left - 1).abs().median()) if len(left) else None
    return {
        "date": when,
        "magnitude": float(ordered.iloc[0]),
        "second": float(ordered.iloc[1]) if len(ordered) > 1 else 0.0,
        "position": int(position),
        "right_dev": deviation,
        "left_gap": left_gap,
        "overlap": int(len(both)),
    }


def dominant_step(step: dict | None) -> bool:
    """One interior jump of at least 10%, larger than twice anything else, then a stable ratio."""
    if not step or step["right_dev"] is None:
        return False
    return (
        step["magnitude"] >= STEP_MIN
        and step["second"] < step["magnitude"] / 2
        and step["position"] >= STEP_EDGE
        and step["right_dev"] < RIGHT_STABLE
    )


def explaining_subject(subjects: list[str]) -> str | None:
    """The first corporate-action subject that names a demerger, scheme, or rights issue."""
    for subject in subjects:
        text = subject.lower()
        if any(word in text for word in SPECIAL_WORDS):
            return " ".join(subject.split())
    return None


def substitution_reason(step: dict | None, subjects: list[str]) -> str | None:
    """Why Upstox replaces this series, or None when the bhavcopy stands."""
    if not dominant_step(step):
        return None
    return explaining_subject(subjects)


def _parse_ex_date(value) -> date | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def subjects_near(actions: list[dict], when: date, symbol: str, isin: str, days: int = SUBJECT_WINDOW_DAYS) -> list[str]:
    """Subjects for this name whose ex-date is within `days` of the price step."""
    found = []
    symbol = symbol.upper()
    for action in actions:
        ex = _parse_ex_date(action.get("exDate") or action.get("ex_date"))
        if ex is None or abs((ex - when).days) > days:
            continue
        same_symbol = str(action.get("symbol") or "").upper() == symbol
        same_isin = bool(isin) and str(action.get("isin") or "") == isin
        if same_symbol or same_isin:
            found.append(str(action.get("subject") or ""))
    return found


def is_mismatch(stats: dict, median: float = 0.01, day_share: float = 0.05, minimum: int = 20) -> bool:
    """The Phase 1b rule: median gap above 1%, or more than 5% of days beyond 1%."""
    overlap = int(stats.get("overlap") or 0)
    if overlap < minimum or stats.get("median_abs_pct") is None:
        return False
    return stats["median_abs_pct"] > median or stats["over_tol"] / overlap > day_share


def event_flags(frame: pd.DataFrame, move: float = EVENT_MOVE) -> pd.DataFrame:
    """Sessions whose research open is more than `move` away from the previous research close.

    The previous close is the lineage's own prior bar. A split or demerger that
    the research series already reflects does not show up here. Columns:
    lineage, date, gap.
    """
    empty = pd.DataFrame(columns=["lineage", "date", "gap"])
    if frame.empty:
        return empty
    work = research_prices(frame)
    work["date"] = work["date"].map(as_date)
    work = work.sort_values(["lineage", "date"])
    previous = work.groupby("lineage", sort=False)["px_close"].shift(1)
    gap = (work["px_open"] / previous - 1).abs()
    keep = gap > move
    out = work.loc[keep, ["lineage", "date"]].copy()
    out["gap"] = gap.loc[keep].astype(float).to_numpy()
    return out.reset_index(drop=True)


def blocked_entries(flags: pd.DataFrame, calendar: list[date], extra: int = 2) -> set[tuple[str, date]]:
    """No new entry on a flagged session, or on the next `extra` sessions."""
    index = {day: i for i, day in enumerate(calendar)}
    blocked: set[tuple[str, date]] = set()
    if flags is None or flags.empty:
        return blocked
    for lineage, day in zip(flags["lineage"], flags["date"], strict=False):
        start = index.get(as_date(day))
        if start is None:
            continue
        for step in range(0, extra + 1):
            if start + step < len(calendar):
                blocked.add((str(lineage), calendar[start + step]))
    return blocked


def apply_ohlc(frame: pd.DataFrame, lineage: str, ohlc: pd.DataFrame) -> int:
    """Replace `px_*` for one lineage on dates present in `ohlc`. Returns the number of sessions replaced."""
    if ohlc.empty or lineage not in set(frame["lineage"]):
        return 0
    other = ohlc.copy()
    other["date"] = other["date"].map(as_date)
    other = other.drop_duplicates("date", keep="last").set_index("date")
    count = 0
    mask = frame["lineage"].astype(str) == str(lineage)
    for idx in frame.index[mask]:
        day = as_date(frame.at[idx, "date"])
        if day not in other.index:
            continue
        row = other.loc[day]
        opens, highs, lows, closes = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
        if min(opens, highs, lows, closes) <= 0 or highs < lows:
            continue
        frame.at[idx, "px_open"] = opens
        frame.at[idx, "px_high"] = highs
        frame.at[idx, "px_low"] = lows
        frame.at[idx, "px_close"] = closes
        count += 1
    return count


def turnover_units(frame: pd.DataFrame, cut: date = UDIFF_FROM) -> dict:
    """Median of (turnover / volume) / close on each side of the format change.

    Both sides are rupees when each median is near 1 and the two medians match.
    `same_units` is the before/after comparison. The caller does not rescale.
    """
    work = frame
    if "series" in work.columns:
        work = work.loc[work["series"].astype(str).str.upper().isin(("EQ", "BE"))]
    work = work.loc[(work["volume"] > 0) & (work["close"] > 0) & (work["turnover"] > 0)].copy()
    work["date"] = work["date"].map(as_date)
    ratio = work["turnover"] / work["volume"] / work["close"]

    def median(part: pd.Series) -> float | None:
        if part.empty:
            return None
        return float(part.median())

    before = median(ratio.loc[work["date"] < cut])
    after = median(ratio.loc[work["date"] >= cut])
    if before is None or after is None or before == 0:
        compared = None
        same = False
    else:
        compared = after / before
        same = abs(compared - 1) < 0.02
    return {
        "before": before,
        "after": after,
        "after_over_before": compared,
        "same_units": same,
        "cut": cut.isoformat(),
    }


def mismatch_stats(bhav: pd.Series, other: pd.Series) -> dict:
    return compare_closes(bhav, other)


def window_dates(start: date, end: date) -> list[date]:
    """Calendar days from start through end, used by tests that build a short tape."""
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days
