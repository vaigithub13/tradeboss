"""Equity intraday charges for a paper fill. Uses the existing dated cost table."""

from __future__ import annotations

from datetime import date

import numpy as np

from app.backtest.costs import Slippage
from app.backtest.instruments import cost_model_for


def slip_points(settings_ticks: float, tick_size: float) -> Slippage:
    return Slippage.points(settings_ticks * tick_size)


def fill_price(side: str, price: float, slippage: Slippage) -> float:
    """`side` is the order side, BUY or SELL. Slippage always worsens the fill."""
    return slippage.apply(side, price)


def order_sides(position_side: str) -> tuple[str, str]:
    if position_side == "long":
        return "BUY", "SELL"
    if position_side == "short":
        return "SELL", "BUY"
    raise ValueError(f"position side must be long or short, got {position_side!r}")


def round_trip(
    *,
    position_side: str,
    entry: float,
    exit_price: float,
    qty: int,
    on: date,
    slippage: Slippage,
) -> dict[str, float]:
    entry_side, exit_side = order_sides(position_side)
    entry_fill = fill_price(entry_side, entry, slippage)
    exit_fill = fill_price(exit_side, exit_price, slippage)
    model = cost_model_for("equity_intraday")
    charges = float(model.leg_cost(entry_side, entry_fill, qty, on).total + model.leg_cost(exit_side, exit_fill, qty, on).total)
    if position_side == "long":
        gross = (exit_fill - entry_fill) * qty
    else:
        gross = (entry_fill - exit_fill) * qty
    return {
        "entry_fill": float(entry_fill),
        "exit_fill": float(exit_fill),
        "gross": float(gross),
        "charges": charges,
        "net": float(gross) - charges,
    }


def fast_exit_costs(position_side: str, prices: np.ndarray, qty: int, on: date) -> np.ndarray:
    """Vector exit-leg charges (rupees) for many prices. Rounding matches the table within a rupee, not the paisa."""
    model = cost_model_for("equity_intraday")
    assert model.table is not None
    row = model.table.row_for(on)
    _entry_side, exit_side = order_sides(position_side)
    turnover = prices * qty
    flat = float(row.rates["brokerage_flat"])
    pct = float(row.rates["brokerage_pct"])
    brokerage = np.minimum(flat, turnover * pct / 100.0)
    stt = turnover * float(row.rates["stt_sell_pct"]) / 100.0 if exit_side == "SELL" else 0.0
    exchange = turnover * float(row.rates["exchange_pct"]) / 100.0
    sebi = turnover * float(row.rates["sebi_per_crore"]) / 10_000_000.0
    stamp = turnover * float(row.rates["stamp_buy_pct"]) / 100.0 if exit_side == "BUY" else 0.0
    extra = np.zeros_like(prices, dtype=float)
    gst_base = brokerage + exchange + sebi
    for item in row.extra:
        if exit_side in item.sides:
            part = turnover * float(item.pct) / 100.0
            extra = extra + part
            if item.gst:
                gst_base = gst_base + part
    gst = gst_base * float(row.rates["gst_pct"]) / 100.0
    return brokerage + stt + exchange + sebi + stamp + gst + extra
