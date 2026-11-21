"""Split/bonus factors from Upstox Fundamentals corporate-actions; adjust OHLC + volume.

Dividends and rights are recorded but not applied to OHLC (user asked splits/bonuses only).
Raw 1m stays in `1m.parquet`; adjusted series is written to `1m_adj.parquet` with `adjustments.json`.

Phase B finding (2026-11-21): Upstox historical 1m candles are **already** split/bonus-adjusted
(KOTAKBANK 1:5 ex 2026-01-14 shows no raw jump). Prefer `write_identity_adjusted` so
`1m_adj == 1m`; do not re-apply CA factors to OHLC unless a vendor feed is proven unadjusted.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.data.importer import COLUMNS, write_parquet
from app.data.publish import publish_file
from app.upstox.client import UpstoxClient

IST = timezone(timedelta(hours=5, minutes=30))
RAW_NAME = "1m.parquet"
ADJ_NAME = "1m_adj.parquet"
LEDGER_NAME = "adjustments.json"


@dataclass(frozen=True)
class CorporateAction:
    kind: str  # Split | Bonus | Dividend | Rights | other
    ex_date: date
    ratio: str | None
    amount: float | None
    price_factor: float | None  # multiply OHLC on bars strictly before ex_date
    volume_factor: float | None  # multiply volume on bars strictly before ex_date
    raw: dict[str, Any]


def parse_ratio(ratio: str | None) -> tuple[float, float] | None:
    """`A:B` → (A, B)."""
    if not ratio or ":" not in ratio:
        return None
    left, right = ratio.split(":", 1)
    try:
        a, b = float(left.strip()), float(right.strip())
    except ValueError:
        return None
    if a <= 0 or b <= 0 or not math.isfinite(a) or not math.isfinite(b):
        return None
    return a, b


def factors_for(kind: str, ratio: str | None) -> tuple[float | None, float | None]:
    """(price_factor, volume_factor) applied to bars before the ex-date.

    Split `A:B` (NSE-style 1:5 = 1 into 5): price *= A/B, volume *= B/A.
    Bonus `A:B` (A bonus per B held): price *= B/(A+B), volume *= (A+B)/B.
    """
    parts = parse_ratio(ratio)
    if parts is None:
        return None, None
    a, b = parts
    kind_l = kind.lower()
    if kind_l == "split":
        return a / b, b / a
    if kind_l == "bonus":
        return b / (a + b), (a + b) / b
    return None, None


def _parse_date_str(val: str) -> date | None:
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_ex_date(raw: dict[str, Any]) -> date | None:
    """Prefer explicit Ex-* date in event_details; fall back to top-level expiry_date."""
    details = raw.get("event_details") or []
    if isinstance(details, list):
        preferred: date | None = None
        for row in details:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").lower()
            if "ex" in name and "date" in name:
                parsed = _parse_date_str(str(row.get("value") or ""))
                if parsed is not None:
                    # Prefer split/bonus ex-dates when several "ex" fields exist
                    if "split" in name or "bonus" in name or "dividend" in name:
                        return parsed
                    preferred = preferred or parsed
        if preferred is not None:
            return preferred
    expiry = raw.get("expiry_date")
    if isinstance(expiry, str) and expiry.strip():
        return _parse_date_str(expiry)
    return None


def parse_corporate_actions(payload: list[dict[str, Any]]) -> list[CorporateAction]:
    out: list[CorporateAction] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("name") or "other")
        ex = _parse_ex_date(row)
        if ex is None:
            continue
        ratio = row.get("ratio")
        ratio_s = None if ratio is None else str(ratio)
        amount = row.get("amount")
        amt = float(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None
        pf, vf = factors_for(kind, ratio_s)
        out.append(
            CorporateAction(
                kind=kind,
                ex_date=ex,
                ratio=ratio_s,
                amount=amt,
                price_factor=pf,
                volume_factor=vf,
                raw=row,
            )
        )
    return sorted(out, key=lambda c: c.ex_date)


def fetch_corporate_actions(client: UpstoxClient, isin: str) -> list[CorporateAction]:
    return parse_corporate_actions(client.corporate_actions(isin))


def cumulative_factors(
    actions: list[CorporateAction], *, as_of: date
) -> list[tuple[date, float, float]]:
    """For each Split/Bonus with factors, the cumulative multiplier for bars before that ex-date.

    Returns [(ex_date, price_mult, vol_mult), ...] ascending. A bar at time t uses the product of
    factors for every action with ex_date > bar_date (i.e. future splits still ahead of the bar).
    """
    applicable = [
        a for a in actions
        if a.price_factor is not None and a.volume_factor is not None and a.ex_date <= as_of
        and a.kind.lower() in ("split", "bonus")
    ]
    applicable.sort(key=lambda a: a.ex_date)
    return [(a.ex_date, float(a.price_factor), float(a.volume_factor)) for a in applicable]


def apply_adjustments(df: pd.DataFrame, actions: list[CorporateAction], *, as_of: date) -> pd.DataFrame:
    """Return a copy of `df` with OHLC/volume adjusted for splits/bonuses on or before `as_of`."""
    if df.empty:
        return df.copy()
    out = df.copy()
    # bar date in IST from unix seconds
    times = pd.to_datetime(out["time"], unit="s", utc=True).dt.tz_convert(IST)
    bar_dates = times.dt.date
    price_mult = pd.Series(1.0, index=out.index)
    vol_mult = pd.Series(1.0, index=out.index)
    for ex_date, pf, vf in cumulative_factors(actions, as_of=as_of):
        mask = bar_dates.map(lambda d, e=ex_date: d < e)
        price_mult = price_mult.where(~mask, price_mult * pf)
        vol_mult = vol_mult.where(~mask, vol_mult * vf)
    for col in ("open", "high", "low", "close"):
        out[col] = out[col].astype(float) * price_mult
    out["volume"] = out["volume"].astype(float) * vol_mult
    return out


def write_identity_adjusted(
    symbol_dir: Path,
    *,
    as_of: date,
    reason: str,
    actions: list[CorporateAction] | None = None,
) -> dict[str, Any]:
    """Copy raw → 1m_adj (Upstox history already adjusted). Record CA inventory without applying."""
    import shutil

    raw_path = symbol_dir / RAW_NAME
    if not raw_path.is_file():
        raise FileNotFoundError(f"no raw candles at {raw_path}")

    def _copy(tmp: Path) -> None:
        shutil.copyfile(raw_path, tmp)

    publish_file(symbol_dir / ADJ_NAME, _copy, accept=lambda p: p.is_file() and p.stat().st_size > 0)
    actions = actions or []
    serializable = {
        "as_of": as_of.isoformat(),
        "source": "identity_copy",
        "raw": RAW_NAME,
        "adjusted": ADJ_NAME,
        "identity_copy": True,
        "reason": reason,
        "applied_splits_bonuses": [],
        "actions": [
            {
                "kind": a.kind,
                "ex_date": a.ex_date.isoformat(),
                "ratio": a.ratio,
                "amount": a.amount,
                "price_factor": a.price_factor,
                "volume_factor": a.volume_factor,
            }
            for a in actions
        ],
    }
    payload = json.dumps(serializable, indent=2, sort_keys=True) + "\n"
    publish_file(
        symbol_dir / LEDGER_NAME,
        lambda p: p.write_text(payload, encoding="utf-8"),
        accept=lambda tmp: tmp.is_file() and tmp.stat().st_size > 0,
    )
    return serializable


def write_adjusted_series(
    symbol_dir: Path,
    actions: list[CorporateAction],
    *,
    as_of: date,
    source: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Read raw 1m.parquet, write 1m_adj.parquet + adjustments.json. Returns the ledger dict.

    Default `apply=False` (Phase B): identity copy — Upstox 1m is already adjusted.
    Pass `apply=True` only when a feed is proven unadjusted.
    """
    if not apply:
        return write_identity_adjusted(
            symbol_dir,
            as_of=as_of,
            reason=(
                "Upstox historical 1m already split/bonus-adjusted; factors not re-applied "
                f"(source={source})"
            ),
            actions=actions,
        )
    raw_path = symbol_dir / RAW_NAME
    if not raw_path.is_file():
        raise FileNotFoundError(f"no raw candles at {raw_path}")
    q = str(raw_path).replace("'", "''")
    con = duckdb.connect()
    try:
        df = con.execute(f"SELECT {', '.join(COLUMNS)} FROM read_parquet('{q}') ORDER BY time").df()
    finally:
        con.close()
    adj = apply_adjustments(df, actions, as_of=as_of)
    write_parquet(adj, symbol_dir / ADJ_NAME)
    applied = [
        {
            "kind": a.kind,
            "ex_date": a.ex_date.isoformat(),
            "ratio": a.ratio,
            "price_factor": a.price_factor,
            "volume_factor": a.volume_factor,
        }
        for a in actions
        if a.kind.lower() in ("split", "bonus") and a.price_factor is not None
    ]
    serializable = {
        "as_of": as_of.isoformat(),
        "source": source,
        "raw": RAW_NAME,
        "adjusted": ADJ_NAME,
        "applied_splits_bonuses": applied,
        "actions": [
            {
                "kind": a.kind,
                "ex_date": a.ex_date.isoformat(),
                "ratio": a.ratio,
                "amount": a.amount,
                "price_factor": a.price_factor,
                "volume_factor": a.volume_factor,
            }
            for a in actions
        ],
        "rows_raw": int(len(df)),
        "rows_adj": int(len(adj)),
    }
    payload = json.dumps(serializable, indent=2, sort_keys=True) + "\n"

    def _write(p: Path) -> None:
        p.write_text(payload, encoding="utf-8")

    publish_file(
        symbol_dir / LEDGER_NAME,
        _write,
        accept=lambda tmp: tmp.is_file() and tmp.stat().st_size > 0,
    )
    return serializable
