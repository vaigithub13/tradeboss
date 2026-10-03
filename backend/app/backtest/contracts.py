"""The Strategy / Broker contracts (PROJECT_PLAN.md section 4), used by backtest, paper and live.

Signal.qty is in LOTS (a lot is 1 for equities). Two optional fields extend the original
contract without breaking it (both default to None):

* `stop` / `target` - absolute price levels attached to an entry; at the entry fill they become
  an OCO pair of protective orders (usable as bracket orders by a paper / live broker too);
* `oco` - orders of the same bar that share this label cancel each other when one fills.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

SIDES = ("BUY", "SELL", "EXIT")
ORDER_TYPES = ("MARKET", "LIMIT", "SL")  # SL = stop-market: triggers when price reaches `price`


class LookAheadError(Exception):
    """A strategy tried to read bars (or indicator values) that have not closed yet."""


@dataclass(frozen=True)
class Signal:
    side: str
    qty: int
    type: str = "MARKET"
    price: float | None = None
    tag: str = ""
    stop: float | None = None
    target: float | None = None
    oco: str | None = None

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, got {self.side!r}")
        if self.type not in ORDER_TYPES:
            raise ValueError(f"type must be one of {ORDER_TYPES}, got {self.type!r}")
        if isinstance(self.qty, bool) or not isinstance(self.qty, int) or self.qty < 1:
            raise ValueError(f"qty must be a whole number of lots >= 1, got {self.qty!r}")
        if self.type in ("LIMIT", "SL") and (self.price is None or not self.price > 0):
            raise ValueError(f"{self.type} orders need a positive price")
        if self.type == "MARKET" and self.price is not None:
            raise ValueError("MARKET orders take no price")
        if (self.stop is not None or self.target is not None) and self.side == "EXIT":
            raise ValueError("stop / target are for entries, not EXIT")


class Strategy:
    """Subclass and implement `on_bar`. `params` is what makes two runs comparable (it is hashed)."""

    name: str = "strategy"
    allow_overnight: bool = False  # may a position (and its orders) be carried to the next session?

    def __init__(self, **params: Any) -> None:
        self.params: dict[str, Any] = dict(params)

    def on_start(self, ctx: Any) -> None:
        pass

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        return []

    def on_stop(self, ctx: Any) -> None:
        pass


class Broker(Protocol):
    """Swapped, never rewritten: BacktestBroker here; PaperBroker / UpstoxLiveBroker later."""

    def place(self, signal: Signal, **kw: Any) -> int | None: ...

    def cancel(self, order_id: int, **kw: Any) -> bool: ...

    def positions(self) -> Any: ...
