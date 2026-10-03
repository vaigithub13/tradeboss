"""SpringPad Pivot Extension.

`faithful` follows the Pine script. A pivot is known only on the bar that confirms
it (`right_bars` after the pivot bar), and only if it is strictly beyond both
sides. While flat, orders update when a pivot low confirms. While in a position,
they update when a pivot high confirms. A side with no pivot on that bar is a
market order (`stop=na`).

`carried_pivots` is Vaibhav's research variant, not the Pine script. It rests
stops on the most recent confirmed pivot high and pivot low, carried forward.
Walk-forward counts those combinations in the same total as the faithful ones.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.backtest.contracts import Signal
from app.strategies.pine_common import PinePort, entry_window

VARIANTS = ("faithful", "carried_pivots")


def confirmed_pivot(values: np.ndarray, left: int, right: int, *, high: bool) -> float | None:
    """Pine pivothigh / pivotlow: the value `right` bars ago, confirmed on this bar."""
    n = len(values)
    if n < left + right + 1:
        return None
    i = n - 1 - right
    price = float(values[i])
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        other = float(values[j])
        if (other >= price) if high else (other <= price):
            return None
    return price


class PivotExtension(PinePort):
    name = "pivot_extension"

    def __init__(self, left_bars: int = 4, right_bars: int = 2, variant: str = "faithful", **kw: Any) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}")
        if isinstance(left_bars, bool) or isinstance(right_bars, bool):
            raise ValueError("left_bars and right_bars must be whole numbers >= 1")
        if left_bars < 1 or right_bars < 1:
            raise ValueError("left_bars and right_bars must be >= 1")
        super().__init__(left_bars=left_bars, right_bars=right_bars, variant=variant, **kw)
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.variant = variant
        self.last_ph: float | None = None
        self.last_pl: float | None = None

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        closing = self.session_exit(bar, ctx)
        if closing is not None:
            return closing
        ph = confirmed_pivot(ctx.bars.high, self.left_bars, self.right_bars, high=True)
        pl = confirmed_pivot(ctx.bars.low, self.left_bars, self.right_bars, high=False)
        if self.variant == "carried_pivots":
            return self._carried(bar, ctx, ph, pl)
        if not entry_window(int(bar["time"])):
            return []
        flat = ctx.position.is_flat
        if not ((flat and pl is not None) or (not flat and ph is not None)):
            return []
        return self.arm(ctx, [
            self.entry("BUY", None if ph is None else ph + self.tick, "LE"),
            self.entry("SELL", None if pl is None else pl - self.tick, "SE"),
        ])

    def _carried(self, bar: dict[str, Any], ctx: Any, ph: float | None, pl: float | None) -> list[Signal]:
        if ph is not None:
            self.last_ph = ph
        if pl is not None:
            self.last_pl = pl
        if not entry_window(int(bar["time"])) or (self.last_ph is None and self.last_pl is None):
            return []
        signals: list[Signal] = []
        if self.last_ph is not None:
            signals.append(self.entry("BUY", self.last_ph + self.tick, "LE"))
        if self.last_pl is not None:
            signals.append(self.entry("SELL", self.last_pl - self.tick, "SE"))
        return self.arm(ctx, signals)
