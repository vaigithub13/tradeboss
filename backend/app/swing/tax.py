"""Equity capital-gains tax for swing research. Nothing here files a return.

STT is assumed paid. Surcharge is not applied. Cess is applied on the tax.
Losses offset gains in the statutory direction and carry forward inside the
window. The financial year ends on 31 March. A later loss in the same year
reduces tax already booked on an earlier gain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

CENT = Decimal("0.01")
DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_FILE = DATA_DIR / "tax_rates_equity.json"
GRANDFATHER_BEFORE = date(2018, 2, 1)


@dataclass(frozen=True)
class TaxRow:
    effective_from: date
    stcg: Decimal
    ltcg: Decimal
    ltcg_exemption: Decimal
    cess: Decimal
    grandfather: bool
    note: str


class TaxTable:
    def __init__(self, rows: list[TaxRow]) -> None:
        if not rows:
            raise ValueError("tax table has no rows")
        self.rows = sorted(rows, key=lambda row: row.effective_from)

    @classmethod
    def load(cls, path: Path | None = None) -> TaxTable:
        payload = json.loads((path or DEFAULT_FILE).read_text(encoding="utf-8"))
        rows = []
        for item in payload["rows"]:
            rows.append(
                TaxRow(
                    effective_from=date.fromisoformat(item["effective_from"]),
                    stcg=Decimal(str(item["stcg"])),
                    ltcg=Decimal(str(item["ltcg"])),
                    ltcg_exemption=Decimal(str(item["ltcg_exemption"])),
                    cess=Decimal(str(item["cess"])),
                    grandfather=bool(item.get("grandfather")),
                    note=str(item.get("note") or ""),
                )
            )
        return cls(rows)

    def row_for(self, on: date) -> TaxRow:
        found: TaxRow | None = None
        for row in self.rows:
            if row.effective_from <= on:
                found = row
        if found is None:
            raise ValueError(f"no equity tax rates for {on.isoformat()}")
        return found


def anniversary(start: date) -> date:
    """The date twelve months after `start`. 29 February lands on 28 February."""
    try:
        return start.replace(year=start.year + 1)
    except ValueError:
        return date(start.year + 1, 2, 28)


def is_long_term(entry: date, exit_day: date) -> bool:
    """More than twelve months. A sale on the anniversary is still short-term."""
    return exit_day > anniversary(entry)


def grandfather_basis(actual: Decimal, sale: Decimal, fmv: Decimal | None) -> Decimal:
    """Section 112A cost: higher of actual cost and the lower of FMV and the sale price."""
    if fmv is None:
        return actual
    return max(actual, min(fmv, sale))


def _q(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _financial_year(day: date) -> int:
    return day.year if day.month >= 4 else day.year - 1


class TaxLedger:
    """One research account. `on_sale` returns the cash tax for that sale, negative when a refund is due."""

    def __init__(self, table: TaxTable) -> None:
        self.table = table
        self.fy: int | None = None
        self.events: list[tuple[str, Decimal, Decimal]] = []
        self.paid = Decimal("0")
        self.total = Decimal("0")

    def on_sale(self, entry: date, exit_day: date, taxable: Decimal, *, grandfather_basis_used: bool = False) -> Decimal:
        del grandfather_basis_used
        self._roll(exit_day)
        row = self.table.row_for(exit_day)
        long = is_long_term(entry, exit_day)
        factor = (row.ltcg if long else row.stcg) * (Decimal(1) + row.cess)
        self.events.append(("L" if long else "S", taxable, factor))
        due = _year_tax(self.events, self._exemption(exit_day))
        change = due - self.paid
        self.paid = due
        self.total += change
        return change

    def _exemption(self, day: date) -> Decimal:
        opening = date(self.fy if self.fy is not None else _financial_year(day), 4, 1)
        if opening < self.table.rows[0].effective_from:
            opening = self.table.rows[0].effective_from
        return self.table.row_for(opening).ltcg_exemption

    def _roll(self, day: date) -> None:
        fy = _financial_year(day)
        if self.fy is None:
            self.fy = fy
            return
        if fy == self.fy:
            return
        short_left, long_left = _loss_left(self.events, self._exemption(date(self.fy, 4, 1)))
        self.fy = fy
        self.events = []
        self.paid = Decimal("0")
        if short_left > 0:
            self.events.append(("S", -short_left, Decimal("0")))
        if long_left > 0:
            self.events.append(("L", -long_left, Decimal("0")))


def _walk(events: list[tuple[str, Decimal, Decimal]], exemption: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Tax due, unused short-term loss, unused long-term loss."""
    short_gains: list[list[Decimal]] = []
    long_gains: list[list[Decimal]] = []
    short_loss = Decimal("0")
    long_loss = Decimal("0")
    for kind, amount, factor in events:
        if amount < 0:
            if kind == "S":
                short_loss += -amount
            else:
                long_loss += -amount
            continue
        bucket = short_gains if kind == "S" else long_gains
        bucket.append([amount, factor])
    for row in short_gains:
        used = min(short_loss, row[0])
        row[0] -= used
        short_loss -= used
    for row in long_gains:
        used = min(long_loss, row[0])
        row[0] -= used
        long_loss -= used
    for row in long_gains:
        used = min(short_loss, row[0])
        row[0] -= used
        short_loss -= used
    shield = exemption
    for row in long_gains:
        used = min(shield, row[0])
        row[0] -= used
        shield -= used
    tax = Decimal("0")
    for row in short_gains + long_gains:
        tax += row[0] * row[1]
    return _q(tax), short_loss, long_loss


def _year_tax(events: list[tuple[str, Decimal, Decimal]], exemption: Decimal) -> Decimal:
    return _walk(events, exemption)[0]


def _loss_left(events: list[tuple[str, Decimal, Decimal]], exemption: Decimal) -> tuple[Decimal, Decimal]:
    _tax, short_left, long_left = _walk(events, exemption)
    return short_left, long_left
