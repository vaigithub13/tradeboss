"""One paper-trading day: closed live bars in, strategy signals out, paper fills and the book updated.

The strategy is the same object a backtest runs (`Strategy.on_bar`), fed only closed bars in order.
The index signal picks the option: BUY -> ATM call, SELL -> ATM put, both on the nearest weekly.
A signal decided at a closed bar is filled at the live quote at that moment (`now_ms`), or the model
if there is no fresh quote. A signal that cannot be filled is recorded as unfilled and opens nothing.
Open positions are squared off at 15:15, like the backtest's default square-off.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import date
from typing import Any

from app.backtest.context import PositionView
from app.backtest.contracts import Signal
from app.backtest.costs import CostTable
from app.live.spreads.decode import DepthQuote
from app.options.contract import OptionContract
from app.paper.bars import ClosedBars
from app.paper.book import Fill, PaperBook, Position, fill, summarise
from app.paper.quotes import QuoteBook
from app.strategies.pine_common import minute_of

SQUARE_OFF_MIN = 15 * 60 + 15
TAG_REASON = {
    "LE": "log XZ crossed above 0: buy",
    "SE": "log XZ crossed below 0: sell",
}


class _Ctx:
    """The fields a strategy's on_bar may read (see app/backtest/context.py StrategyContext)."""

    def __init__(self, timeframe: str, symbol: str, position: PositionView) -> None:
        self.timeframe = timeframe
        self.symbol = symbol
        self.position = position
        self.base_minutes = 1
        self.cash = 0.0
        self.lot_size = 1
        self.open_orders: list[dict[str, Any]] = []
        self.bars = None

    def cancel_working(self, tag: str | None = None) -> int:
        return 0

    def indicator(self, itype: str, **params: Any) -> Any:
        raise NotImplementedError("paper trading takes no indicator views yet")


class PaperSession:
    def __init__(
        self,
        *,
        day: date,
        strategy: Any,
        choose: Callable[[str, float, date], OptionContract],
        key_for: Callable[[str], str | None],
        quotes: QuoteBook,
        cost_table: CostTable,
        model_price: Callable[[OptionContract, float, int], float] | None = None,
        bar_minutes: int = 5,
        lots: int = 1,
        symbol: str = "NIFTY50",
    ) -> None:
        if lots < 1:
            raise ValueError("lots must be >= 1")
        self.day = day
        self.strategy = strategy
        self.choose = choose
        self.key_for = key_for
        self.quotes = quotes
        self.cost_table = cost_table
        self.model_price = model_price
        self.lots = lots
        self.symbol = symbol
        self.timeframe = f"{bar_minutes}m"
        self.bars = ClosedBars(bar_minutes)
        self.book = PaperBook()
        self.signals: list[dict[str, Any]] = []
        self.shown: list[dict[str, Any]] = []  # the closed bars the strategy saw, live
        self.warm_bars = 0
        self._side = 0  # the index position as the strategy sees it: +1 long, -1 short
        self._spot: float | None = None
        self._contract_of: dict[str, OptionContract] = {}

    # ---------------------------------------------------------------- input
    def warm(self, bars: Iterable[dict[str, Any]]) -> None:
        """Run earlier closed bars through the strategy so its indicators are warm. Nothing is traded."""
        for bar in bars:
            self._step(bar)
            self.warm_bars += 1

    def on_depth(self, quotes: Iterable[DepthQuote]) -> None:
        self.quotes.on_depth(quotes)

    def on_index_minute(self, minute: dict[str, Any], *, now_ms: int) -> list[dict[str, Any]]:
        """Feed one live 1-minute index bar. `now_ms` is the feed's current time. Returns the signal records."""
        out: list[dict[str, Any]] = []
        self._spot = float(minute["close"])
        if minute_of(int(minute["time"])) >= SQUARE_OFF_MIN and self.book.position is not None:
            self._square_off(now_ms)
        for bar in self.bars.on_minute(minute):
            out += self._on_closed_bar(bar, now_ms)
        return out

    def end_of_day(self, *, now_ms: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for bar in self.bars.end_of_day():
            out += self._on_closed_bar(bar, now_ms)
        return out

    # ------------------------------------------------------------ decisions
    def _on_closed_bar(self, bar: dict[str, Any], now_ms: int) -> list[dict[str, Any]]:
        self.shown.append(bar)
        out = []
        for sig in self._step(bar):
            out.append(self._act(sig, bar, now_ms))
        return out

    def _step(self, bar: dict[str, Any]) -> list[Signal]:
        ctx = _Ctx(self.timeframe, self.symbol,
                   PositionView(side=self._side, lots=1 if self._side else 0, units=0, avg_price=0.0))
        signals = list(self.strategy.on_bar(bar, ctx))
        for sig in signals:
            self._side = {"BUY": 1, "SELL": -1, "EXIT": 0}[sig.side]
        return signals

    def _act(self, sig: Signal, bar: dict[str, Any], now_ms: int) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "time": int(bar["time"]),
            "decided_at_ms": now_ms,
            "side": sig.side,
            "index_price": float(bar["close"]),
            "reason": TAG_REASON.get(sig.tag, sig.tag),
            "symbol": None,
            "key": None,
            "status": "unfilled",
            "fill_source": None,
            "fill_price": None,
            "note": "",
        }
        self.signals.append(rec)
        if sig.side == "EXIT":
            rec["status"] = "exit"
            if self.book.position is not None:
                self._square_off(now_ms, reason="exit")
                rec["status"] = "filled"
            return rec
        if minute_of(int(bar["time"])) >= SQUARE_OFF_MIN:
            rec["note"] = "after the 15:15 square-off"
            return rec
        direction = "LONG" if sig.side == "BUY" else "SHORT"
        if self.book.position is not None and self.book.position.direction == direction:
            rec["status"] = "skipped"
            rec["note"] = "already in that position"
            return rec

        spot = float(bar["close"])
        contract = self.choose(direction, spot, self.day)
        key = self.key_for(contract.symbol)
        if key is None:
            rec["note"] = "contract is not in the instrument master"
            return rec
        units = self.lots * contract.lot_size
        entry = self._fill("BUY", key, contract, units, spot, now_ms)
        if entry is None:
            rec["note"] = "no quote and no model price for the contract"
            return rec
        if self.book.position is not None:
            exit_fill = self._fill("SELL", self.book.position.key, self._contract_of[self.book.position.key],
                                   self.book.position.units, spot, now_ms)
            if exit_fill is None:
                rec["note"] = "could not close the open position: no quote and no model price"
                return rec
            self.book.close(exit_fill, index_exit_time=int(bar["time"]), reason="signal")
        self._contract_of[key] = contract
        self.book.open(Position(
            direction=direction, key=key, symbol=contract.symbol, kind=contract.kind, strike=contract.strike,
            expiry=contract.expiry, lots=self.lots, units=units, lot_size=contract.lot_size, entry=entry,
            index_entry_time=int(bar["time"]), reason=rec["reason"],
        ))
        rec.update(status="filled", symbol=contract.symbol, key=key, fill_source=entry.source,
                   fill_price=entry.price)
        return rec

    def _square_off(self, now_ms: int, reason: str = "square_off") -> None:
        p = self.book.position
        if p is None:
            return
        contract = self._contract_of[p.key]
        exit_fill = self._fill("SELL", p.key, contract, p.units, self._spot or p.entry.price, now_ms)
        if exit_fill is None:
            self.signals.append({
                "time": None, "decided_at_ms": now_ms, "side": "EXIT", "index_price": self._spot,
                "reason": "square-off", "symbol": p.symbol, "key": p.key, "status": "unfilled",
                "fill_source": None, "fill_price": None, "note": "square-off: no quote and no model price",
            })
            return
        self.book.close(exit_fill, index_exit_time=now_ms // 1000, reason=reason)

    def _fill(self, side: str, key: str, contract: OptionContract, units: int, spot: float,
              now_ms: int) -> Fill | None:
        model = None
        if self.model_price is not None:
            model = lambda: self.model_price(contract, spot, now_ms)  # noqa: E731
        return fill(side, key=key, quotes=self.quotes, now_ms=now_ms, units=units, day=self.day,
                    cost_table=self.cost_table, model_price=model)

    # ---------------------------------------------------------------- output
    def mark(self, now_ms: int) -> dict[str, Any] | None:
        """Live P&L of the open paper position, marked at the live bid. None when there is no position or no bid."""
        p = self.book.position
        if p is None:
            return None
        q = self.quotes.at(p.key, now_ms)
        if q is None or q.bid is None:
            return {"symbol": p.symbol, "source": None, "gross": None, "net": None}
        m = self.book.mark(bid=q.bid, cost_table=self.cost_table, day=self.day)
        return {"symbol": p.symbol, "source": "quote", **m}

    def live_bars(self) -> dict[int, dict[str, Any]]:
        return {int(b["time"]): b for b in self.shown}

    def snapshot(self) -> dict[str, Any]:
        entries = [s for s in self.signals if s["side"] in ("BUY", "SELL")]
        unfilled = sum(1 for s in self.signals if s["status"] == "unfilled")
        p = self.book.position
        return {
            "strategy": {"name": getattr(self.strategy, "name", "?"), "params": dict(getattr(self.strategy, "params", {}))},
            "timeframe": self.timeframe,
            "lots": self.lots,
            "signals": self.signals,
            "trades": self.book.trades,
            "open": None if p is None else {**asdict(p), "expiry": p.expiry.isoformat(), "entry": asdict(p.entry)},
            "summary": summarise(self.book.trades, signals=len(entries), unfilled=unfilled),
            "closed_bars": len(self.shown),
            "warm_bars": self.warm_bars,
            "incomplete_bars": sorted(self.bars.incomplete),
            "pre_open_ignored": self.bars.pre_open_ignored,
        }
