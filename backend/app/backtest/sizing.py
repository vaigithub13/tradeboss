"""Fixed-₹ risk position sizing for equity/futures backtests (Phase B).

qty (shares or future units) = floor(risk_rupees / stop_distance).
Futures: round down to a whole number of lots (lot_size units each).
Skip when qty < 1 or notional (entry × qty) > notional_cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any


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

    def size(self, *, stop_distance: float, entry_price: float) -> tuple[int, str | None]:
        """Return (qty_units, skip_reason). qty_units is shares or future units (lot_size multiples)."""
        if stop_distance <= 0 or not (stop_distance == stop_distance):  # NaN
            return 0, "non_positive_stop_distance"
        if entry_price <= 0:
            return 0, "non_positive_entry"
        raw = floor(self.risk_rupees / stop_distance)
        if self.lot_size > 1:
            lots = floor(raw / self.lot_size)
            qty = lots * self.lot_size
        else:
            qty = raw
        if qty < 1:
            return 0, "qty_below_1"
        notional = entry_price * qty
        if notional > self.notional_cap:
            return 0, f"notional_{notional:.0f}_above_cap_{self.notional_cap:.0f}"
        return qty, None

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
