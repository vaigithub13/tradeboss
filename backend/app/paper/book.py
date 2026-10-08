"""Paper fills, the open paper position, closed trades and the daily summary.

Every paper trade buys an option: a BUY signal buys the ATM call, a SELL signal buys the ATM put
(see app/options/position.py). Exits sell the option. Nothing here sends an order anywhere.

A fill is priced at the live best ask (buy) or best bid (sell). Without a fresh quote it falls back to
the model and is flagged `modelled`; a modelled fill has no real mid, so it has no slippage figure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.backtest.costs import CostTable
from app.paper.quotes import QuoteBook

TICK = 0.05  # the smallest premium we will quote


@dataclass(frozen=True)
class Fill:
    side: str  # BUY | SELL
    key: str
    price: float
    mid: float | None
    source: str  # quote | modelled
    at_ms: int
    units: int
    charges: dict[str, float]
    charges_total: float

    @property
    def slippage(self) -> float | None:
        """Points worse than the mid: above it for a buy, below it for a sell. None without a real mid."""
        if self.mid is None:
            return None
        return round(self.price - self.mid if self.side == "BUY" else self.mid - self.price, 4)


def fill(
    side: str,
    *,
    key: str,
    quotes: QuoteBook,
    now_ms: int,
    units: int,
    day: date,
    cost_table: CostTable,
    model_price: Callable[[], float | None] | None = None,
    model_slippage: float = 0.0,
) -> Fill | None:
    q = quotes.at(key, now_ms)
    if side == "BUY" and q is not None and q.ask is not None:
        price, mid, source = q.ask, _mid(q.bid, q.ask), "quote"
    elif side == "SELL" and q is not None and q.bid is not None:
        price, mid, source = q.bid, _mid(q.bid, q.ask), "quote"
    elif model_price is not None:
        modelled = model_price()
        if modelled is None:
            return None
        against = model_slippage if side == "BUY" else -model_slippage
        price, mid, source = max(float(modelled) + against, TICK), None, "modelled"
    else:
        return None
    leg = cost_table.leg_cost(side, price, units, day)
    return Fill(
        side=side,
        key=key,
        price=price,
        mid=mid,
        source=source,
        at_ms=now_ms,
        units=units,
        charges={k: float(v) for k, v in leg.components.items()},
        charges_total=float(leg.total),
    )


def _mid(bid: float | None, ask: float | None) -> float | None:
    return round((bid + ask) / 2, 4) if bid is not None and ask is not None else None


@dataclass(frozen=True)
class Position:
    direction: str  # LONG (ATM call) | SHORT (ATM put), from the index signal
    key: str
    symbol: str
    kind: str
    strike: float
    expiry: date
    lots: int
    units: int
    lot_size: int
    entry: Fill
    index_entry_time: int
    reason: str
    #: exit-rule levels {index_stop, index_target, premium_stop, premium_target}; None without a rule
    levels: dict[str, float | None] | None = None
    signal_time: int | None = None  # start of the bar whose decision placed the entry
    trigger_index: float | None = None  # the stop level, or the index at a market fill
    index_entry: float | None = None  # the index price the contract was chosen from
    delta: float | None = None  # the model's delta at entry (report estimates)
    source: str = "live"  # live, or replay (decided while catching up from the recording)


class PaperBook:
    def __init__(self) -> None:
        self.position: Position | None = None
        self.trades: list[dict[str, Any]] = []

    def open(self, position: Position) -> None:
        if self.position is not None:
            raise RuntimeError("a paper position is already open")
        self.position = position

    def close(self, exit_fill: Fill, *, index_exit_time: int, reason: str,
              index_exit: float | None = None) -> dict[str, Any]:
        if self.position is None:
            raise RuntimeError("no paper position to close")
        p = self.position
        gross = round((exit_fill.price - p.entry.price) * p.units, 2)
        charges_entry = round(p.entry.charges_total, 2)
        charges_exit = round(exit_fill.charges_total, 2)
        charges = round(charges_entry + charges_exit, 2)
        net = round(gross - charges, 2)
        trade = {
            "direction": p.direction,
            "symbol": p.symbol,
            "key": p.key,
            "kind": p.kind,
            "strike": p.strike,
            "expiry": p.expiry.isoformat(),
            "lots": p.lots,
            "units": p.units,
            "lot_size": p.lot_size,
            "index_entry_time": p.index_entry_time,
            "index_exit_time": index_exit_time,
            "entry_at_ms": p.entry.at_ms,
            "exit_at_ms": exit_fill.at_ms,
            "entry_price": p.entry.price,
            "exit_price": exit_fill.price,
            "entry_mid": p.entry.mid,
            "exit_mid": exit_fill.mid,
            "entry_source": p.entry.source,
            "exit_source": exit_fill.source,
            "entry_slippage": p.entry.slippage,
            "exit_slippage": exit_fill.slippage,
            "gross": gross,
            "charges_entry": charges_entry,
            "charges_exit": charges_exit,
            "charges": charges,
            "net": net,
            "win": net > 0,
            "entry_reason": p.reason,
            "exit_reason": reason,
            "levels": p.levels,
            "signal_time": p.signal_time,
            "trigger_index": p.trigger_index,
            "index_entry": p.index_entry,
            "delta": p.delta,
            "index_exit": index_exit,
            "source": p.source,
        }
        self.trades.append(trade)
        self.position = None
        return trade

    def mark(self, *, bid: float, cost_table: CostTable, day: date) -> dict[str, Any]:
        """Open P&L if the option were sold at `bid` now, after the entry charges and the exit charges."""
        if self.position is None:
            raise RuntimeError("no paper position to mark")
        p = self.position
        gross = round((bid - p.entry.price) * p.units, 2)
        charges_entry = round(p.entry.charges_total, 2)
        charges_exit_est = round(float(cost_table.leg_cost("SELL", bid, p.units, day).total), 2)
        return {
            "bid": bid,
            "gross": gross,
            "charges_entry": charges_entry,
            "charges_exit_est": charges_exit_est,
            "net": round(gross - charges_entry - charges_exit_est, 2),
        }


def summarise(trades: list[dict[str, Any]], *, signals: int, unfilled: int) -> dict[str, Any]:
    legs = [s for t in trades for s in (t["entry_slippage"], t["exit_slippage"]) if s is not None]
    return {
        "trades": len(trades),
        "wins": sum(1 for t in trades if t["net"] > 0),
        "gross": round(sum(t["gross"] for t in trades), 2),
        "charges": round(sum(t["charges"] for t in trades), 2),
        "net": round(sum(t["net"] for t in trades), 2),
        "modelled_legs": sum(1 for t in trades for src in (t["entry_source"], t["exit_source"]) if src == "modelled"),
        "avg_slippage_points": round(sum(legs) / len(legs), 4) if legs else None,
        "signals": signals,
        "unfilled": unfilled,
    }

