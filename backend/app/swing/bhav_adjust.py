"""Lineage, split and bonus factors, and the Upstox comparison.

A rename keeps its ISIN. An ISIN change is stitched only when the same symbol
continues the next session and the new previous close is the old close.
Filters must use the raw close. Adjusted prices are for the comparison.
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from app.swing.bhavcopy import COLUMNS

CA_TOLERANCE = 0.25
PRICE_TOLERANCE = 0.02
STITCH_GAP_DAYS = 7
STITCH_PRICE = 0.03
MIN_FACTOR_MOVE = 0.08

_BONUS = re.compile(r"Bonus\s*-?\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_SPLIT = re.compile(
    r"Face\s+Valu\w*\s+Split\b.*?(?:From\s+)?Rs\.?\s*(\d+(?:\.\d+)?).*?To\s+(?:Rs\.?|Re\.?)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)


def price_factor(subject: str) -> float | None:
    """Price multiplier on the ex-date. Bonus 1:1 is 0.5. A 5-to-1 split is 0.2."""
    factor = 1.0
    found = False
    for new, held in _BONUS.findall(subject or ""):
        new_f, held_f = float(new), float(held)
        if new_f + held_f <= 0:
            continue
        factor *= held_f / (new_f + held_f)
        found = True
    for old_fv, new_fv in _SPLIT.findall(subject or ""):
        old_f, new_f = float(old_fv), float(new_fv)
        if old_f <= 0:
            continue
        factor *= new_f / old_f
        found = True
    if not found:
        return None
    return factor


def capital_change(subject: str) -> bool:
    """Equity split or bonus. A bonus debenture or preference issue is not a share-price factor."""
    text = (subject or "").lower()
    if any(word in text for word in ("debenture", "ncrps", "preference")):
        return False
    return any(word in text for word in ("bonus", "split", "sub-division", "subdivision", "consolidation"))


def parse_actions(rows: list[dict]) -> list[dict]:
    actions = []
    for row in rows:
        ex = row.get("ex_date")
        if not isinstance(ex, date):
            ex = _action_date(row.get("exDate"))
        if ex is None:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        subject = str(row.get("subject") or "")
        actions.append({
            "symbol": symbol,
            "ex_date": ex,
            "isin": str(row.get("isin") or "").strip().upper(),
            "subject": subject,
            "factor": price_factor(subject),
            "capital_change": capital_change(subject),
        })
    return actions


def _action_date(value: object) -> date | None:
    from app.swing.bhavcopy import parse_nse_date
    return parse_nse_date(value)


def standard_factors() -> list[float]:
    values: list[float] = []
    for new, held in (
        (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 10),
        (2, 1), (2, 3), (2, 5), (3, 1), (3, 2), (3, 5), (4, 1), (5, 1), (5, 2),
    ):
        values.append(held / (new + held))
    for new_fv, old_fv in (
        (1, 2), (1, 5), (1, 10), (2, 5), (2, 10), (5, 10),
        (2, 1), (5, 1), (10, 1), (5, 2), (10, 2),
    ):
        values.append(new_fv / old_fv)
    unique: list[float] = []
    for value in sorted(values):
        if value <= 0:
            continue
        if all(abs(value - kept) / kept > 0.005 for kept in unique):
            unique.append(value)
    return unique


def nearest_factor(ratio: float, factors: list[float], tolerance: float) -> float | None:
    if ratio <= 0 or not factors:
        return None
    best: float | None = None
    error = tolerance
    for factor in factors:
        gap = abs(ratio / factor - 1.0)
        if gap <= error:
            best = factor
            error = gap
    return best


def meaningful(factor: float) -> bool:
    return abs(factor - 1.0) >= MIN_FACTOR_MOVE


def assign_lineages(frame: pd.DataFrame) -> pd.DataFrame:
    """One lineage per company. Renames share an ISIN. A split that changes the ISIN is stitched."""
    if frame.empty:
        out = frame.copy()
        out["lineage"] = pd.Series(dtype=str)
        return out
    work = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
    isin_last = work.groupby("isin")["date"].max().to_dict()
    parent: dict[str, str] = {}

    def find(isin: str) -> str:
        parent.setdefault(isin, isin)
        while parent[isin] != isin:
            parent[isin] = parent[parent[isin]]
            isin = parent[isin]
        return isin

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    last: dict[str, tuple[date, str, float]] = {}
    for row in work.itertuples(index=False):
        prev = last.get(row.symbol)
        if prev is not None and prev[1] != row.isin:
            gap = (row.date - prev[0]).days
            base = prev[2]
            price_ok = base > 0 and abs(float(row.prev_close) - base) / base <= STITCH_PRICE
            ended = isin_last.get(prev[1]) == prev[0]
            if gap <= STITCH_GAP_DAYS and price_ok and ended:
                union(prev[1], row.isin)
        last[row.symbol] = (row.date, row.isin, float(row.close))

    first_date = work.groupby("isin")["date"].min().to_dict()
    roots: dict[str, str] = {}
    for isin in work["isin"].unique():
        root = find(str(isin))
        current = roots.get(root)
        if current is None or first_date[isin] < first_date[current]:
            roots[root] = str(isin)
    work["lineage"] = work["isin"].map(lambda isin: roots[find(str(isin))])
    return work


def one_row_per_day(frame: pd.DataFrame) -> pd.DataFrame:
    """A stitched lineage keeps the higher-turnover print when two symbols share a day."""
    if frame.empty or "lineage" not in frame.columns:
        return frame
    sums = frame.groupby(["lineage", "date"], as_index=False)[["volume", "turnover"]].sum()
    chosen = (
        frame.sort_values("turnover", ascending=False)
        .groupby(["lineage", "date"], sort=False, as_index=False)
        .head(1)
        .drop(columns=["volume", "turnover"])
    )
    merged = chosen.merge(sums, on=["lineage", "date"], how="left")
    return merged.sort_values(["lineage", "date"]).reset_index(drop=True)


def apply_adjustments(
    frame: pd.DataFrame,
    actions: list[dict],
    covered_months: set[tuple[int, int]],
) -> tuple[pd.DataFrame, list[dict]]:
    """Back-adjust OHLC. A factor is applied only when the price ratio agrees, or when no CA file exists."""
    work = frame.sort_values(["lineage", "date"]).reset_index(drop=True)
    if work.empty:
        work["factor_today"] = pd.Series(dtype=float)
        work["adj_open"] = pd.Series(dtype=float)
        work["adj_high"] = pd.Series(dtype=float)
        work["adj_low"] = pd.Series(dtype=float)
        work["adj_close"] = pd.Series(dtype=float)
        return work, []
    factors = standard_factors()
    ledger: list[dict] = []
    applied: dict[tuple[str, date], float] = {}
    decided: set[tuple[str, date]] = set()
    located = {
        (str(symbol), day): index
        for index, symbol, day in zip(work.index, work["symbol"], work["date"], strict=True)
    }
    by_isin = {
        (str(isin), day): index
        for index, isin, day in zip(work.index, work["isin"], work["date"], strict=True)
    }

    def find_row(action: dict) -> pd.Series | None:
        index = located.get((action["symbol"], action["ex_date"]))
        if index is None and action.get("isin"):
            index = by_isin.get((action["isin"], action["ex_date"]))
        if index is None:
            return None
        return work.loc[index]

    groups: dict[tuple[str, date], list[dict]] = {}
    for action in actions:
        if not action.get("capital_change"):
            continue
        groups.setdefault((action["symbol"], action["ex_date"]), []).append(action)

    for key, group in groups.items():
        row = None
        for action in group:
            row = find_row(action)
            if row is not None:
                break
        observed = _observed(row)
        meaningful_factors = [
            float(action["factor"])
            for action in group
            if action.get("factor") is not None and meaningful(float(action["factor"]))
        ]
        product = 1.0
        for factor in meaningful_factors:
            product *= factor
        if (
            row is not None
            and len(meaningful_factors) >= 2
            and meaningful(product)
            and observed is not None
            and abs(observed / product - 1.0) <= CA_TOLERANCE
        ):
            entry = _entry(
                str(row["symbol"]),
                row["date"],
                row,
                observed,
                " | ".join(str(action.get("subject") or "") for action in group),
            )
            entry["applied"] = True
            entry["reason"] = "confirmed_product"
            entry["factor"] = product
            entry["source"] = "nse_corporate_action"
            _store_factor(applied, row, product)
            decided.add(key)
            decided.add((str(row["symbol"]), row["date"]))
            ledger.append(entry)
            continue
        for action in group:
            factor = action.get("factor")
            entry = _entry(action["symbol"], action["ex_date"], row, observed, action.get("subject") or "")
            if row is not None:
                entry["symbol"] = str(row["symbol"])
                entry["ex_date"] = row["date"].isoformat()
            if row is None:
                entry["reason"] = "no_bar"
            elif factor is None or not meaningful(float(factor)):
                if factor is not None:
                    entry["reason"] = "small_factor"
                    entry["factor"] = float(factor)
                else:
                    match = nearest_factor(observed, factors, PRICE_TOLERANCE) if observed else None
                    if match is not None and meaningful(match):
                        entry["applied"] = True
                        entry["reason"] = "confirmed_unparsed"
                        entry["factor"] = match
                        entry["source"] = "price_ratio"
                        _store_factor(applied, row, match)
                    else:
                        entry["reason"] = "small_or_unparsed"
                        entry["factor"] = factor
            elif observed is None or abs(observed / float(factor) - 1.0) > CA_TOLERANCE:
                entry["reason"] = "price_mismatch"
                entry["factor"] = float(factor)
            else:
                entry["applied"] = True
                entry["reason"] = "confirmed"
                entry["factor"] = float(factor)
                entry["source"] = "nse_corporate_action"
                _store_factor(applied, row, float(factor))
            decided.add(key)
            if row is not None:
                decided.add((str(row["symbol"]), row["date"]))
            ledger.append(entry)

    ratio = work["close"].astype(float) / work["prev_close"].astype(float)
    big = ratio.notna() & ((ratio - 1.0).abs() >= MIN_FACTOR_MOVE)
    for row in work.loc[big].itertuples(index=False):
        observed = float(row.close) / float(row.prev_close)
        key = (row.symbol, row.date)
        if key in decided:
            continue
        match = nearest_factor(observed, factors, PRICE_TOLERANCE)
        if match is None:
            continue
        month = (row.date.year, row.date.month)
        entry = _entry(row.symbol, row.date, row, observed, "")
        entry["factor"] = match
        entry["source"] = "price_ratio"
        if month in covered_months:
            entry["reason"] = "no_corporate_action"
        else:
            entry["applied"] = True
            entry["reason"] = "ca_feed_missing"
            _store_factor(applied, row, match)
        ledger.append(entry)

    work["factor_today"] = [
        applied.get((str(row.lineage), row.date), 1.0) for row in work.itertuples(index=False)
    ]
    work["adj_mult"] = work.groupby("lineage", sort=False)["factor_today"].transform(_future_mult)
    for column in ("open", "high", "low", "close"):
        work[f"adj_{column}"] = work[column].astype(float) * work["adj_mult"].astype(float)
    return work, ledger


def _field(row: object, name: str):
    if isinstance(row, pd.Series):
        return row[name]
    return getattr(row, name)


def _observed(row: object | None) -> float | None:
    if row is None:
        return None
    prev = float(_field(row, "prev_close"))
    if prev <= 0:
        return None
    return float(_field(row, "close")) / prev


def _entry(symbol: str, ex_date: date, row: object | None, observed: float | None, subject: str) -> dict:
    return {
        "symbol": symbol,
        "lineage": None if row is None else str(_field(row, "lineage")),
        "ex_date": ex_date.isoformat(),
        "observed": None if observed is None else round(float(observed), 6),
        "factor": None,
        "source": "nse_corporate_action",
        "subject": subject,
        "applied": False,
        "reason": "",
    }


def _store_factor(applied: dict[tuple[str, date], float], row: object, factor: float) -> None:
    key = (str(_field(row, "lineage")), _field(row, "date"))
    applied[key] = applied.get(key, 1.0) * factor


def _future_mult(factors: pd.Series) -> pd.Series:
    """Product of this lineage's later ex-date factors. Today's own factor is not applied to today."""
    reverse = factors.iloc[::-1].cumprod().iloc[::-1]
    return reverse / factors


def compare_closes(bhav: pd.Series, other: pd.Series, tolerance: float = 0.01) -> dict:
    """Overlap of two close series indexed by date. Prices at or below zero are left out."""
    both = pd.concat([bhav.rename("bhav"), other.rename("other")], axis=1, join="inner")
    both = both.loc[(both["bhav"] > 0) & (both["other"] > 0)]
    if both.empty:
        return {"overlap": 0, "over_tol": 0, "median_abs_pct": None}
    pct = (both["bhav"] - both["other"]).abs() / both["other"]
    return {
        "overlap": int(len(pct)),
        "over_tol": int((pct > tolerance).sum()),
        "median_abs_pct": float(pct.median()),
    }


def research_columns() -> list[str]:
    return COLUMNS + ["lineage", "factor_today", "adj_open", "adj_high", "adj_low", "adj_close"]
