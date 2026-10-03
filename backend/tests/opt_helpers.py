"""Builders for Phase 3b tests. No network, no token."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.costs import get_cost_model
from app.backtest.expiry import load_default_calendar
from app.backtest.lots import load_default_lot_table
from app.backtest.result import BacktestResult, Trade
from app.options.model import OptionModelConfig, overlay_options
from app.options.strikes import load_default_step_table

IST = timezone(timedelta(hours=5, minutes=30))
CAL = load_default_calendar()
LOTS = load_default_lot_table()
STEPS = load_default_step_table()
COSTS = get_cost_model("options")


def ist(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def bar(t: int, price: float, volume: float = 100.0) -> dict[str, Any]:
    return {"time": t, "open": price, "high": price, "low": price, "close": price, "volume": volume, "oi": None}


def trade(
    *,
    tid: int = 1,
    direction: str = "LONG",
    entry: tuple[int, float],
    exit: tuple[int, float],
    lots: int = 1,
    lot_size: int = 65,
    gap: bool = False,
    optimistic: bool = False,
) -> Trade:
    units = lots * lot_size
    return Trade(
        id=tid, direction=direction,
        entry_time=entry[0], entry_price=entry[1],
        exit_time=exit[0], exit_price=exit[1],
        lots=lots, units=units, lot_size=lot_size,
        gross_pnl=0.0, charges={}, charges_total=0.0, slippage_cost=0.0, net_pnl=0.0,
        exit_reason="market", entry_tag="", exit_tag="",
        gap=gap, ambiguous=False, optimistic=optimistic,
    )


def bare_result(trades: list[Trade], *, optimistic: bool = False, run_id: str = "idx") -> BacktestResult:
    return BacktestResult(
        trades=trades, events=[], metrics={}, counters={}, warnings=[],
        optimistic=optimistic, run_id=run_id,
        config={"fill_mode": "same_bar_close" if optimistic else "next_open"},
        strategy={"name": "scripted"}, data={},
    )


def estimate(trades: list[Trade], index: list[dict[str, Any]], vix: list[dict[str, Any]], **kw: Any):
    cfg = kw.pop("config", OptionModelConfig())
    return overlay_options(
        bare_result(trades, optimistic=kw.pop("optimistic", False), run_id=kw.pop("run_id", "idx")),
        index, vix, config=cfg, calendar=CAL, lots=LOTS, steps=STEPS, costs=kw.pop("costs", COSTS),
        events=kw.pop("events", None),
        tape=kw.pop("tape", None),
    )
