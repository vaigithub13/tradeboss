"""EMA crossover: act when the fast EMA crosses the slow EMA (the chart's own EMA code)."""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy

MODES = ("long_only", "long_short")


class EmaCrossover(Strategy):
    name = "ema_crossover"

    def __init__(self, fast: int = 9, slow: int = 21, mode: str = "long_short", lots: int = 1, source: str = "close") -> None:
        super().__init__(fast=fast, slow=slow, mode=mode, lots=lots, source=source)
        if not 1 <= fast < slow:
            raise ValueError("fast must be smaller than slow (both >= 1)")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if lots < 1:
            raise ValueError("lots must be >= 1")
        self.fast, self.slow, self.mode, self.lots, self.source = fast, slow, mode, lots, source

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        if len(ctx.bars) <= self.slow:
            return []
        f = ctx.indicator("ema", length=self.fast, source=self.source)["ema"]
        s = ctx.indicator("ema", length=self.slow, source=self.source)["ema"]
        d_now, d_prev = f[-1] - s[-1], f[-2] - s[-2]
        up = d_prev <= 0 < d_now
        down = d_prev >= 0 > d_now
        if not (up or down):
            return []
        pos = ctx.position
        out: list[Signal] = []
        if up:
            if pos.side < 0:
                out.append(Signal("EXIT", pos.lots, tag="ema_cross_up"))
            if pos.side <= 0:
                out.append(Signal("BUY", self.lots, tag="ema_long"))
        else:
            if pos.side > 0:
                out.append(Signal("EXIT", pos.lots, tag="ema_cross_down"))
            if self.mode == "long_short" and pos.side >= 0:
                out.append(Signal("SELL", self.lots, tag="ema_short"))
        return out
