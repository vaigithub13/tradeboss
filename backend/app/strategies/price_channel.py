"""SpringPad Price Channel.

`highest(high, length)` and `lowest(low, length)` include the bar that just
closed. Orders start once `close[length]` exists. Both stops are replaced on
every bar inside 09:15-14:50 and stay working after that window. A fill of one
side leaves the other working, so a later touch reverses the position.
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal
from app.strategies.pine_common import PinePort, entry_window


class PriceChannel(PinePort):
    name = "price_channel"

    def __init__(self, length: int = 20, **kw: Any) -> None:
        if isinstance(length, bool) or not isinstance(length, int) or length < 1:
            raise ValueError("length must be a whole number >= 1")
        super().__init__(length=length, **kw)
        self.length = length

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        closing = self.session_exit(bar, ctx)
        if closing is not None:
            return closing
        if len(ctx.bars) <= self.length or not entry_window(int(bar["time"])):
            return []
        window_high = float(ctx.bars.high[-self.length:].max())
        window_low = float(ctx.bars.low[-self.length:].min())
        return self.arm(ctx, [
            self.entry("BUY", window_high + self.tick, "LE"),
            self.entry("SELL", window_low - self.tick, "SE"),
        ])
