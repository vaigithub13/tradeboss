"""Supertrend flip: go with the trend each time the Supertrend direction changes (chart code)."""

from __future__ import annotations

import math
from typing import Any

from app.backtest.contracts import Signal, Strategy

MODES = ("long_only", "long_short")


class SupertrendFlip(Strategy):
    name = "supertrend_flip"

    def __init__(self, atr_length: int = 10, multiplier: float = 3.0, mode: str = "long_short", lots: int = 1) -> None:
        super().__init__(atr_length=atr_length, multiplier=multiplier, mode=mode, lots=lots)
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if lots < 1:
            raise ValueError("lots must be >= 1")
        self.atr_length, self.multiplier, self.mode, self.lots = atr_length, multiplier, mode, lots

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        st = ctx.indicator("supertrend", atr_length=self.atr_length, multiplier=self.multiplier)
        now, prev = st.last("direction"), st.prev("direction")
        if math.isnan(now) or math.isnan(prev) or now == prev:
            return []
        pos = ctx.position
        out: list[Signal] = []
        if now == -1:  # uptrend (TradingView convention: direction -1 = up)
            if pos.side < 0:
                out.append(Signal("EXIT", pos.lots, tag="st_flip_up"))
            if pos.side <= 0:
                out.append(Signal("BUY", self.lots, tag="st_long"))
        else:
            if pos.side > 0:
                out.append(Signal("EXIT", pos.lots, tag="st_flip_down"))
            if self.mode == "long_short" and pos.side >= 0:
                out.append(Signal("SELL", self.lots, tag="st_short"))
        return out
