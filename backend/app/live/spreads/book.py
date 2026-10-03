"""Average fill of 1 lot and 2 lots walked through the five-level book.

Buy walks the asks, sell walks the bids. The result is points away from the mid
of the best bid and ask (a cost: higher means a worse fill). A side the five
levels cannot fill is None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class _Level(Protocol):
    bid_p: float
    bid_q: int
    ask_p: float
    ask_q: int


@dataclass(frozen=True)
class FillCost:
    buy_1: float | None
    sell_1: float | None
    buy_2: float | None
    sell_2: float | None
    level1_covers_1_lot: bool


def fill_costs(levels: list[_Level] | tuple[_Level, ...], lot: int) -> FillCost:
    if lot < 1:
        raise ValueError("lot must be >= 1")
    if not levels:
        return FillCost(None, None, None, None, False)
    top = levels[0]
    mid = (top.bid_p + top.ask_p) / 2 if top.bid_p > 0 and top.ask_p > 0 else None
    covers = bool(top.bid_p > 0 and top.ask_p > 0 and top.bid_q >= lot and top.ask_q >= lot)
    if mid is None:
        return FillCost(None, None, None, None, False)

    def cost(side: str, lots: int) -> float | None:
        avg = _walk(levels, lot * lots, side)
        if avg is None:
            return None
        return (avg - mid) if side == "buy" else (mid - avg)

    return FillCost(cost("buy", 1), cost("sell", 1), cost("buy", 2), cost("sell", 2), covers)


def _walk(levels: tuple[_Level, ...] | list[_Level], need: int, side: str) -> float | None:
    got = 0
    paid = 0.0
    for lv in levels:
        price = lv.ask_p if side == "buy" else lv.bid_p
        qty = lv.ask_q if side == "buy" else lv.bid_q
        if price <= 0 or qty <= 0:
            break
        take = min(int(qty), need - got)
        paid += take * float(price)
        got += take
        if got >= need:
            return paid / need
    return None
