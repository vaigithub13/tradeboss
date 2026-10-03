from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal
from app.strategies.pine_common import PinePort, entry_window, TICK, timeframe_minutes


class PriceChannelStrategy(PinePort):
    name = "price_channel_strategy"

    def __init__(
        self,
        length: int = 20,
        execution: str = "realistic",
        lots: int = 1,
        use_target: bool = False,
        use_stop: bool = False,
        target_points: float = 10.0,
        stop_points: float = 7.0,
        tick: float = TICK,
    ) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        super().__init__(
            execution=execution,
            lots=lots,
            use_target=use_target,
            use_stop=use_stop,
            target_points=target_points,
            stop_points=stop_points,
            tick=tick,
            length=length,
        )
        self.length = int(length)

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        if len(ctx.bars) <= self.length:
            return []

        out: list[Signal] = []

        exit_signals = self.session_exit(bar, ctx)
        if exit_signals:
            out.extend(exit_signals)

        bar_minutes = timeframe_minutes(ctx.timeframe)
        if not entry_window(bar["time"], bar_minutes):
            return out

        highs = ctx.bars.high
        lows = ctx.bars.low
        hh = max(highs[-self.length:])
        ll = min(lows[-self.length:])

        long_stop = round(float(hh) + self.tick, 2)
        short_stop = round(float(ll) - self.tick, 2)

        signals = [
            self.entry("BUY", long_stop, "LE"),
            self.entry("SELL", short_stop, "SE"),
        ]
        out.extend(self.arm(ctx, signals))
        return out
