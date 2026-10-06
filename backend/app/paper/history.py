"""The stored bars a paper session warms on, from the same window the backtest uses."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.costs import get_cost_model
from app.backtest.engine import BacktestConfig, warm_bars
from app.backtest.sources import StoreSource
from app.data.sessions import DEFAULT_INCLUDED_SESSION_TYPES
from app.data.store import CandleStore

IST = timezone(timedelta(hours=5, minutes=30))


def stored_warmup(store: CandleStore, symbol: str, day: date, bar_minutes: int = 5) -> list[dict[str, Any]]:
    """The bars before `day` that a backtest of that single day would warm on (the last `warmup_bars`)."""
    cfg = BacktestConfig(
        timeframe=f"{bar_minutes}m",
        start=day.isoformat(),
        end=day.isoformat(),
        session_types=tuple(DEFAULT_INCLUDED_SESSION_TYPES),
        underlying=None,
        lot_table=None,
        lot_size=1,
        contract=None,
        cost_model=get_cost_model("zero"),
    )
    return warm_bars(cfg, StoreSource(store, symbol))
