"""Pick the option contract a long/short index signal would have traded."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from app.backtest.expiry import ExpiryCalendar
from app.backtest.lots import LotSizeTable, lot_size_for_contract
from app.options.strikes import StrikeStepTable, pick_strike

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


@dataclass(frozen=True)
class OptionContract:
    kind: str
    strike: float
    expiry: date
    cycle: str
    lot_size: int
    symbol: str
    step: int
    step_verification: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        return d


def contract_symbol(underlying: str, strike: float, kind: str, expiry: date) -> str:
    """`NIFTY 25000 CE 06 OCT 26` — same shape as the instrument master."""
    return f"{underlying} {strike:g} {kind} {expiry.day:02d} {MONTHS[expiry.month - 1]} {expiry.year % 100:02d}"


def choose_contract(
    direction: str,
    spot: float,
    on: date,
    *,
    calendar: ExpiryCalendar,
    lots: LotSizeTable,
    steps: StrikeStepTable,
    underlying: str = "NIFTY",
    offset: int = 0,
    cycle: str = "weekly",
    roll_on_expiry_day: bool = False,
) -> OptionContract:
    """ATM / ATM±1 CE (long) or PE (short) of the nearest expiry on/after `on`."""
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    kind = "CE" if direction == "LONG" else "PE"
    row = steps.row_for(underlying, on)
    strike = pick_strike(kind, spot, row.step, offset)
    exp = calendar.next_expiry(on, cycle, skip_expiry_day=roll_on_expiry_day)
    lot = lot_size_for_contract(lots, underlying, exp.date, exp.kind)
    return OptionContract(
        kind=kind,
        strike=strike,
        expiry=exp.date,
        cycle=exp.kind,
        lot_size=lot,
        symbol=contract_symbol(underlying, strike, kind, exp.date),
        step=row.step,
        step_verification=row.verification,
    )
