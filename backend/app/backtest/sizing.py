"""Fixed-₹ risk position sizing for equity/futures backtests.

qty (shares or future units) = floor(risk_rupees / stop_distance).
If notional would exceed the cap, cap qty at floor(notional_cap / price) (risk below target)
instead of skipping. Skip only when qty < 1 after that.
Futures: round down to a whole number of lots (lot_size units each).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any


@dataclass(frozen=True)
class SizeResult:
    qty_units: int
    capped: bool
    target_risk: float
    actual_risk: float
    skip_reason: str | None = None


@dataclass(frozen=True)
class RiskSizing:
    risk_rupees: float = 300.0
    notional_cap: float = 75_000.0
    lot_size: int = 1  # 1 for equity cash; contract lot for futures

    def __post_init__(self) -> None:
        if self.risk_rupees <= 0:
            raise ValueError("risk_rupees must be > 0")
        if self.notional_cap <= 0:
            raise ValueError("notional_cap must be > 0")
        if self.lot_size < 1:
            raise ValueError("lot_size must be >= 1")

    def size(self, *, stop_distance: float, entry_price: float) -> SizeResult:
        """Return sized qty. Cap at floor(cap/price) when over notional; skip only if qty < 1."""
        if stop_distance <= 0 or not (stop_distance == stop_distance):  # NaN
            return SizeResult(0, False, self.risk_rupees, 0.0, "non_positive_stop_distance")
        if entry_price <= 0:
            return SizeResult(0, False, self.risk_rupees, 0.0, "non_positive_entry")

        raw = floor(self.risk_rupees / stop_distance)
        capped = False
        max_by_cap = floor(self.notional_cap / entry_price)
        if max_by_cap < 1:
            return SizeResult(0, False, self.risk_rupees, 0.0, "qty_below_1")

        if raw > max_by_cap:
            raw = max_by_cap
            capped = True

        if self.lot_size > 1:
            lots = floor(raw / self.lot_size)
            qty = lots * self.lot_size
            # If lot rounding dropped us and we were uncapped risk-sized, try max lots under cap
            if qty < 1 and max_by_cap >= self.lot_size:
                qty = floor(max_by_cap / self.lot_size) * self.lot_size
                capped = True
        else:
            qty = raw

        if qty < 1:
            return SizeResult(0, capped, self.risk_rupees, 0.0, "qty_below_1")

        actual_risk = qty * stop_distance
        return SizeResult(qty, capped, self.risk_rupees, float(actual_risk), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_rupees": self.risk_rupees,
            "notional_cap": self.notional_cap,
            "lot_size": self.lot_size,
        }


def stop_distance_from_signal(*, entry: float, stop: float | None, stop_points: float | None) -> float | None:
    if stop_points is not None and stop_points > 0:
        return float(stop_points)
    if stop is not None:
        dist = abs(float(entry) - float(stop))
        return dist if dist > 0 else None
    return None
