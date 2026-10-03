"""Bar-by-bar replay matches a normal backtest over the same dates."""

from __future__ import annotations

from app.backtest.sources import ListSource
from app.replay.parity import run_replay
from app.strategies.ema_cross import EmaCrossover
from tests.bt_helpers import MON, TUE, cfg, full_day, run


def _key(trade: object) -> tuple[object, ...]:
    return (
        trade.entry_time,  # type: ignore[attr-defined]
        trade.exit_time,  # type: ignore[attr-defined]
        trade.direction,  # type: ignore[attr-defined]
        trade.entry_price,  # type: ignore[attr-defined]
        trade.exit_price,  # type: ignore[attr-defined]
        trade.net_pnl,  # type: ignore[attr-defined]
        trade.exit_reason,  # type: ignore[attr-defined]
    )


def test_replay_bar_by_bar_matches_the_backtest() -> None:
    candles = full_day(MON, 22000) + full_day(TUE, 22100)
    config = cfg(timeframe="5m", start="2026-01-05", end="2026-01-06", square_off="15:15", warmup_bars=0, lot_size=1)
    full = run(EmaCrossover(fast=3, slow=8), candles, timeframe="5m", start="2026-01-05", end="2026-01-06", square_off="15:15")
    assert full.trades, "the sample needs at least one trade"
    stepped = run_replay(lambda: EmaCrossover(fast=3, slow=8), ListSource(candles), config)
    assert [_key(trade) for trade in stepped] == [_key(trade) for trade in full.trades]
