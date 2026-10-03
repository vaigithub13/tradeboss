"""One stored depth row: the best quote, the spread, and the lot-sized fill costs."""

from __future__ import annotations

from datetime import date

from app.backtest.lots import load_default_lot_table, lot_size_for_contract
from app.live.spreads.book import fill_costs
from app.live.spreads.decode import Level

COLUMNS = [
    "ts_ms", "instrument_key", "expiry", "strike", "kind",
    "bid_p", "bid_q", "ask_p", "ask_q", "spread", "nifty_ltp", "lot_size",
    "buy_1", "sell_1", "buy_2", "sell_2", "level1_covers_1_lot",
]


def quote_row(
    *,
    key: str,
    ts_ms: int,
    levels: list[Level] | tuple[Level, ...],
    expiry: date,
    strike: float,
    kind: str,
    nifty_ltp: float,
    lot: int | None = None,
    cycle: str = "weekly",
) -> dict | None:
    if not any(lv.bid_p > 0 or lv.ask_p > 0 for lv in levels):
        return None
    if lot is None:
        lot = lot_size_for_contract(load_default_lot_table(), "NIFTY", expiry, cycle)
    top = levels[0]
    spread = (top.ask_p - top.bid_p) if top.bid_p > 0 and top.ask_p > 0 else None
    cost = fill_costs(levels, lot)
    return {
        "ts_ms": int(ts_ms),
        "instrument_key": key,
        "expiry": expiry.isoformat(),
        "strike": float(strike),
        "kind": kind,
        "bid_p": top.bid_p if top.bid_p > 0 else None,
        "bid_q": int(top.bid_q),
        "ask_p": top.ask_p if top.ask_p > 0 else None,
        "ask_q": int(top.ask_q),
        "spread": spread,
        "nifty_ltp": float(nifty_ltp),
        "lot_size": int(lot),
        "buy_1": cost.buy_1,
        "sell_1": cost.sell_1,
        "buy_2": cost.buy_2,
        "sell_2": cost.sell_2,
        "level1_covers_1_lot": cost.level1_covers_1_lot,
    }
