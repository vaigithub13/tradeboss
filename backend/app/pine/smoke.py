"""A short worker run used by Convert and Approve. The holdout is not touched."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.backtest.contracts import LookAheadError
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.sources import ListSource
from app.pine.isolated import IsolatedStrategy, WorkerError, WorkerTimeout

IST = timezone(timedelta(hours=5, minutes=30))
SMOKE_DAYS = 5


def five_day_bars() -> list[dict]:
    """Five sessions of 5-minute bars, 09:15 through 15:25."""
    bars: list[dict] = []
    for offset in range(SMOKE_DAYS):
        start = datetime(2026, 1, 5 + offset, 9, 15, tzinfo=IST)
        for i in range(75):
            price = 100.0 + i * 0.05
            bars.append({
                "time": int((start + timedelta(minutes=5 * i)).timestamp()),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.25,
                "volume": 1.0,
                "oi": None,
            })
    return bars


def smoke_strategy(path: Path, *, call_timeout: float = 30.0) -> str | None:
    """Run the file in the worker. None when it finishes. The error text when it does not."""
    strategy = IsolatedStrategy(path, call_timeout=call_timeout)
    try:
        run_backtest(
            strategy,
            ListSource(five_day_bars(), base_minutes=5, symbol="NIFTY50"),
            BacktestConfig(timeframe="5m", lot_size=1),
        )
    except (WorkerError, WorkerTimeout, LookAheadError) as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        strategy.close()
    return None
