"""Session clock and order helpers shared by the SpringPad Pine ports.

The scripts call strategy.close when time(timeframe.period, "1515-1520") is
set. On a 5-minute or 15-minute chart that call never runs. time() is na
unless a whole bar of the chart resolution fits inside the session, and this
session is only five minutes long. The 15:15 bar ends at 15:20, on the
session boundary, so it does not fit; a 15-minute bar cannot fit at all.
when=na, so the close is never sent. TradingView carries the position
overnight and leaves the last trade open when the loaded range ends.
tv_parity does the same.

The entry session "0915-1450" is hours long, so time() is set and entries
are gated. Stops placed inside it stay working after 14:50 and into the
next session.
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy

EXECUTIONS = ("realistic", "tv_parity")
TICK = 0.05
ENTRY_LO = 9 * 60 + 15
ENTRY_END = 14 * 60 + 50  # exclusive. "0915-1450" does not include a bar that ends at 14:50.
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def minute_of(t: int) -> int:
    return ((t + 19_800) % 86_400) // 60


def timeframe_minutes(timeframe: str) -> int:
    try:
        return TF_MINUTES[timeframe]
    except KeyError:
        raise ValueError(f"timeframe {timeframe!r} is not an intraday chart") from None


def entry_window(t: int, bar_minutes: int) -> bool:
    """True when the whole bar sits inside 09:15-14:50.

    Same rule as the session scanner. The bar starts at `t` and lasts
    `bar_minutes`. It fits only when that start is at or after 09:15 and the
    bar ends strictly before 14:50. The 14:45 bar of a 15-minute chart ends
    at 15:00, so it is outside. A 5-minute bar that ends exactly at 14:50 is
    outside too.
    """
    if bar_minutes < 1:
        return False
    start = minute_of(t)
    return start >= ENTRY_LO and start + bar_minutes < ENTRY_END


class PinePort(Strategy):
    """`realistic` (default) squares off at 15:15 and does not carry overnight.

    `tv_parity` matches what TradingView actually does with these scripts: no
    square-off, positions carried overnight, the last trade left open, and
    stops inside one bar following Pine's OHLC path.
    """

    def __init__(self, *, execution: str = "realistic", lots: int = 1, use_target: bool = False,
                 use_stop: bool = False, target_points: float = 10.0, stop_points: float = 7.0,
                 tick: float = TICK, **params: Any) -> None:
        if execution not in EXECUTIONS:
            raise ValueError(f"execution must be one of {EXECUTIONS}")
        if isinstance(lots, bool) or not isinstance(lots, int) or lots < 1:
            raise ValueError("lots must be a whole number >= 1")
        if tick <= 0 or target_points <= 0 or stop_points <= 0:
            raise ValueError("tick, target_points and stop_points must be positive")
        super().__init__(execution=execution, lots=lots, use_target=bool(use_target), use_stop=bool(use_stop),
                         target_points=float(target_points), stop_points=float(stop_points), tick=float(tick), **params)
        self.execution = execution
        self.lots = lots
        self.use_target = bool(use_target)
        self.use_stop = bool(use_stop)
        self.target_points = float(target_points)
        self.stop_points = float(stop_points)
        self.tick = float(tick)
        self.allow_overnight = execution == "tv_parity"
        # The loaded range ending is not a square-off. TradingView leaves the last trade open.
        self.flatten_at_end = execution != "tv_parity"
        self.bar_path = "pine_ohlc" if execution == "tv_parity" else "1m"
        self.pyramiding = 0

    def session_exit(self, bar: dict[str, Any], ctx: Any) -> list[Signal] | None:
        # time(timeframe.period, "1515-1520") is na on a 5m or 15m chart, so
        # strategy.close(when=et) never runs. The ids match the entries; the
        # session string is what keeps the position open.
        return None

    def arm(self, ctx: Any, signals: list[Signal]) -> list[Signal]:
        """Replace the working order of each tag. Pine's strategy.entry(id) does the same."""
        for sig in signals:
            if sig.tag:
                ctx.cancel_working(sig.tag)
        return signals

    def entry(self, side: str, price: float | None, tag: str) -> Signal:
        extra: dict[str, float] = {}
        if self.use_stop:
            extra["stop_points"] = self.stop_points
        if self.use_target:
            extra["target_points"] = self.target_points
        if price is None:
            return Signal(side, self.lots, "MARKET", tag=tag, **extra)  # type: ignore[arg-type]
        return Signal(side, self.lots, "SL", round(float(price), 2), tag=tag, **extra)  # type: ignore[arg-type]
