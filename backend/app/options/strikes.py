"""Strike grid: the step between strikes (dated, unverified before real data) and ATM / ITM / OTM picking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_STEPS_FILE = DATA_DIR / "strike_steps.json"
KINDS = ("CE", "PE")


class StrikeStepUnknown(LookupError):
    pass


@dataclass(frozen=True)
class StepRow:
    underlying: str
    step: int
    effective_from: date
    verification: str = "unverified"
    source: str = ""


class StrikeStepTable:
    def __init__(self, rows: list[StepRow]) -> None:
        self.rows = sorted(rows, key=lambda r: (r.underlying, r.effective_from))
        for a, b in zip(self.rows, self.rows[1:]):
            if a.underlying == b.underlying and a.effective_from == b.effective_from:
                raise ValueError(f"duplicate effective_from {a.effective_from} for {a.underlying}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrikeStepTable:
        rows = []
        for raw in data.get("rows", []):
            step = raw["step"]
            if isinstance(step, bool) or not isinstance(step, int) or step < 1:
                raise ValueError(f"step must be a positive whole number, got {step!r}")
            ver = str(raw.get("verification", "unverified"))
            if ver not in ("verified", "unverified"):
                raise ValueError("verification must be 'verified' or 'unverified'")
            rows.append(StepRow(str(raw["underlying"]), step, date.fromisoformat(str(raw["effective_from"])), ver,
                                str(raw.get("source", ""))))
        return cls(rows)

    @classmethod
    def load(cls, path: Path) -> StrikeStepTable:
        return cls.from_dict(json.loads(path.read_text()))

    def row_for(self, underlying: str, on: date) -> StepRow:
        found = [r for r in self.rows if r.underlying == underlying and r.effective_from <= on]
        if not found:
            raise StrikeStepUnknown(f"no strike step known for {underlying} on {on} (data/strike_steps.json starts later)")
        return found[-1]

    def step(self, underlying: str, on: date) -> int:
        return self.row_for(underlying, on).step


def load_default_step_table() -> StrikeStepTable:
    return StrikeStepTable.load(DEFAULT_STEPS_FILE)


def atm_strike(spot: float, step: int) -> float:
    """The strike nearest to `spot`; exactly halfway rounds UP (22,325 with step 50 -> 22,350)."""
    q = (Decimal(str(spot)) / Decimal(step)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(q * step)


def pick_strike(kind: str, spot: float, step: int, offset: int = 0) -> float:
    """ATM, one strike out of the money (+1) or in the money (-1). OTM is higher for a CE, lower for a PE."""
    if kind not in KINDS:
        raise ValueError(f"kind must be CE or PE, got {kind!r}")
    if offset not in (-1, 0, 1):
        raise ValueError("strike offset must be -1 (one strike in the money), 0 (ATM) or +1 (one strike out of the money)")
    atm = atm_strike(spot, step)
    return atm + offset * step if kind == "CE" else atm - offset * step
