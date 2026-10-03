"""A bar-by-bar replay uses the same engine as a normal backtest.

Each cursor keeps only source minutes at or before that time. The finished trade list
over the same dates matches `run_backtest`. A cursor in the middle matches the trades
that have already closed.
"""

from __future__ import annotations

from collections.abc import Callable

from app.backtest.contracts import Strategy
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.result import Trade
from app.backtest.sources import CandleSource
from app.replay.cursor import replay_bars


def _key(trade: Trade) -> tuple[object, ...]:
    return (
        trade.entry_time,
        trade.exit_time,
        trade.direction,
        trade.entry_price,
        trade.exit_price,
        trade.net_pnl,
        trade.exit_reason,
    )


def run_replay(
    factory: Callable[[], Strategy],
    source: CandleSource,
    config: BacktestConfig,
) -> list[Trade]:
    """Step one chart bar at a time. Return the trades at the last cursor."""
    full = run_backtest(factory(), source, config)
    loaded = source.load(None, None, config.session_types)
    if not loaded.candles:
        return []
    last = loaded.candles[-1]["time"]
    bars = replay_bars(loaded.candles, config.timeframe, last, loaded.base_minutes)
    times = [c["time"] for c in loaded.candles]
    cursors: list[int] = []
    for index, bar in enumerate(bars):
        nxt = bars[index + 1]["time"] if index + 1 < len(bars) else last + 1
        inside = [t for t in times if bar["time"] <= t < nxt]
        if inside:
            cursors.append(inside[-1])
    if not cursors:
        return []
    for cursor in cursors:
        partial = run_backtest(factory(), source, config, replay_cursor=cursor)
        got = [_key(trade) for trade in partial.trades]
        expect = [_key(trade) for trade in full.trades if trade.exit_time <= cursor]
        if got != expect:
            raise AssertionError(f"replay at {cursor} diverged from the backtest")
    return list(run_backtest(factory(), source, config, replay_cursor=cursors[-1]).trades)
