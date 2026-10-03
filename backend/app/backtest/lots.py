"""Lot size by date (critical for P&L). Spec: tests/test_bt_lots.py.

NSE changes a lot size per CONTRACT, not per day: new-lot contracts are listed from an effective
date, but contracts that already exist keep the old lot until they expire (weekly and monthly
cycles switch on different expiry dates). So:

* with the contract known (`expiry` + `cycle`) the answer is exact;
* with only a trade date the answer is exact outside a transition window and `LotSizeAmbiguous`
  inside it (both lots are trading) - never a silent guess;
* a date before the first row, or an unknown underlying, raises `LotSizeUnknown`.

The table lives in `data/lot_sizes.json` and is edited by hand from NSE circulars; every row names the
circular it comes from. Contracts are keyed by (cycle weekly/monthly, expiry date) because at a
changeover weeklies and monthlies switch on different expiries (e.g. Jan 2025: weekly 75, the
30 Jan monthly 25). Known limit: quarterly / half-yearly contracts that were already listed at a
changeover switch on their own end-of-day date (26 Dec 2024, 30 Dec 2025) - not modelled; they are
treated like monthlies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_LOT_FILE = DATA_DIR / "lot_sizes.json"


class LotSizeUnknown(LookupError):
    pass


class LotSizeAmbiguous(LookupError):
    pass


@dataclass(frozen=True)
class LotRow:
    underlying: str
    lot_size: int
    effective_from: date  # first trade date on which contracts with this lot are listed
    first_weekly_expiry: date | None = None  # first weekly expiry with this lot
    first_monthly_expiry: date | None = None  # first monthly expiry with this lot
    source: str = ""

    @property
    def window_end(self) -> date | None:
        if self.first_weekly_expiry is None or self.first_monthly_expiry is None:
            return None
        return max(self.first_weekly_expiry, self.first_monthly_expiry)


def _d(value: Any, name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{name}: {value!r} is not a YYYY-MM-DD date") from exc


class LotSizeTable:
    def __init__(self, rows: list[LotRow]) -> None:
        by: dict[str, list[LotRow]] = {}
        for r in rows:
            by.setdefault(r.underlying, []).append(r)
        self._by: dict[str, list[LotRow]] = {}
        for u, rs in by.items():
            rs = sorted(rs, key=lambda r: r.effective_from)
            for a, b in zip(rs, rs[1:]):
                if a.effective_from == b.effective_from:
                    raise ValueError(f"{u}: duplicate effective_from {a.effective_from}")
            for r in rs:
                if (r.first_weekly_expiry is None) != (r.first_monthly_expiry is None):
                    raise ValueError(
                        f"{u} {r.effective_from}: give both first_weekly_expiry and first_monthly_expiry, or neither"
                    )
            for r in rs[1:]:
                if r.first_weekly_expiry is None or r.first_monthly_expiry is None:
                    raise ValueError(
                        f"{u} {r.effective_from}: a revision must say its first weekly AND first monthly expiry "
                        f"(the circular lists both)"
                    )
            self._by[u] = rs
        self.rows = [r for u in sorted(self._by) for r in self._by[u]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LotSizeTable:
        rows: list[LotRow] = []
        for raw in data.get("rows", []):
            lot = raw["lot_size"]
            if isinstance(lot, bool) or not isinstance(lot, int) or lot < 1:
                raise ValueError(f"lot_size must be a positive whole number, got {lot!r}")
            eff = _d(raw.get("effective_from"), "effective_from")
            if eff is None:
                raise ValueError("effective_from is required")
            rows.append(
                LotRow(
                    underlying=str(raw["underlying"]),
                    lot_size=lot,
                    effective_from=eff,
                    first_weekly_expiry=_d(raw.get("first_weekly_expiry"), "first_weekly_expiry"),
                    first_monthly_expiry=_d(raw.get("first_monthly_expiry"), "first_monthly_expiry"),
                    source=str(raw.get("source", "")),
                )
            )
        return cls(rows)

    @classmethod
    def load(cls, path: Path) -> LotSizeTable:
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "underlying": r.underlying,
                    "lot_size": r.lot_size,
                    "effective_from": r.effective_from.isoformat(),
                    "first_weekly_expiry": None if r.first_weekly_expiry is None else r.first_weekly_expiry.isoformat(),
                    "first_monthly_expiry": None if r.first_monthly_expiry is None else r.first_monthly_expiry.isoformat(),
                    "source": r.source,
                }
                for r in self.rows
            ]
        }

    def lot_size(self, underlying: str, on: date, expiry: date | None = None, cycle: str | None = None) -> int:
        rows = self._by.get(underlying)
        if not rows:
            raise LotSizeUnknown(f"no lot sizes for {underlying!r} in the table")
        if on < rows[0].effective_from:
            raise LotSizeUnknown(f"{underlying}: no lot size known for {on} (table starts {rows[0].effective_from})")
        if expiry is not None:
            if cycle not in ("weekly", "monthly"):
                raise ValueError("cycle must be 'weekly' or 'monthly' when an expiry is given")
            chosen = rows[0]
            for r in rows[1:]:
                first = r.first_weekly_expiry if cycle == "weekly" else r.first_monthly_expiry
                # a contract expiring late enough has the new lot, but only once that lot is in force
                if first is not None and first <= expiry and r.effective_from <= on:
                    chosen = r
            return chosen.lot_size
        current = rows[0]
        for r in rows:
            if r.effective_from <= on:
                current = r
        end = current.window_end
        if end is not None and on < end:
            previous = rows[rows.index(current) - 1]
            raise LotSizeAmbiguous(
                f"{underlying} on {on}: contracts with lot {previous.lot_size} and lot {current.lot_size} are both "
                f"trading (transition until {end}); pass the contract's expiry and cycle to choose"
            )
        return current.lot_size


def lot_size_for_contract(table: LotSizeTable, underlying: str, expiry: date, cycle: str) -> int:
    """The lot of a contract known only by (cycle, expiry date) - no trade date involved.

    Assumes the contract was listed after its lot took effect; for a trade made BEFORE a changeover
    in a contract that already existed, use `table.lot_size(underlying, trade_date, expiry, cycle)`."""
    if cycle not in ("weekly", "monthly"):
        raise ValueError("cycle must be 'weekly' or 'monthly'")
    rows = table._by.get(underlying)
    if not rows:
        raise LotSizeUnknown(f"no lot sizes for {underlying!r} in the table")
    if expiry < rows[0].effective_from:
        raise LotSizeUnknown(f"{underlying}: no lot size known for a contract expiring {expiry} (table starts {rows[0].effective_from})")
    chosen = rows[0]
    for r in rows[1:]:
        first = r.first_weekly_expiry if cycle == "weekly" else r.first_monthly_expiry
        if first is not None and first <= expiry:
            chosen = r
    return chosen.lot_size


def load_default_lot_table() -> LotSizeTable:
    return LotSizeTable.load(DEFAULT_LOT_FILE)
