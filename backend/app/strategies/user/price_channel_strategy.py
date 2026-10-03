from app.backtest.contracts import Signal, Strategy
from app.strategies.pine_common import PinePort, entry_window, minute_of, TICK


class PriceChannelStrategy(PinePort):
    name = "price_channel_strategy"

    def __init__(
        self,
        length: int = 20,
        lots: int = 1,
        use_target: bool = False,
        use_stop: bool = False,
        target_points: float = 10.0,
        stop_points: float = 7.0,
        tick: float = TICK,
        execution: str = "realistic",
    ) -> None:
        if not isinstance(length, int) or length < 1:
            raise ValueError("length must be an int >= 1")
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
        self.length = length

    def _highest_high(self, ctx) -> float:
        return max(float(b["high"]) for b in ctx.bars[-self.length:])

    def _lowest_low(self, ctx) -> float:
        return min(float(b["low"]) for b in ctx.bars[-self.length:])

    def on_bar(self, bar: dict[str, object], ctx) -> list[Signal]:
        if len(ctx.bars) <= self.length:
            return []

        out: list[Signal] = []

        session = self.session_exit(bar, ctx)
        if session:
            out.extend(session)

        t = int(bar["time"])
        if not entry_window(t, ctx.timeframe_minutes):
            return out

        hh = self._highest_high(ctx)
        ll = self._lowest_low(ctx)

        long_stop = round(hh + self.tick, 2)
        short_stop = round(ll - self.tick, 2)

        if ctx.position.side == 0:
            signals = [
                self.entry("BUY", long_stop, "LE"),
                self.entry("SELL", short_stop, "SE"),
            ]
        else:
            signals = [
                self.entry("BUY", long_stop, "LE"),
                self.entry("SELL", short_stop, "SE"),
            ]

        out.extend(self.arm(ctx, signals))
        return out
