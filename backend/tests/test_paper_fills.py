"""Paper fills: live best ask to buy, live best bid to sell, the model only when no quote is fresh."""

from __future__ import annotations

from datetime import date

from app.backtest.costs import load_default_cost_table
from app.live.spreads.decode import DepthQuote, Level
from app.paper.book import PaperBook, Position, fill, summarise
from app.paper.quotes import QuoteBook

DAY = date(2026, 10, 5)
KEY = "NSE_FO|48704"
NOW = 1_791_300_000_000  # ms, arbitrary but fixed


def depth(key: str, ts_ms: int, bid: float, ask: float) -> DepthQuote:
    return DepthQuote(key=key, ts_ms=ts_ms, levels=(Level(bid, 75, ask, 75),))


def book_with(quote: DepthQuote | None) -> QuoteBook:
    qb = QuoteBook()
    if quote is not None:
        qb.on_depth([quote])
    return qb


def test_buy_pays_the_live_ask_and_sell_receives_the_live_bid() -> None:
    qb = book_with(depth(KEY, NOW - 1_000, bid=100.0, ask=101.0))
    buy = fill("BUY", key=KEY, quotes=qb, now_ms=NOW, units=75, day=DAY, cost_table=load_default_cost_table())
    sell = fill("SELL", key=KEY, quotes=qb, now_ms=NOW, units=75, day=DAY, cost_table=load_default_cost_table())
    assert buy is not None and sell is not None
    assert (buy.price, buy.mid, buy.source) == (101.0, 100.5, "quote")
    assert (sell.price, sell.mid, sell.source) == (100.0, 100.5, "quote")
    assert buy.slippage == 0.5 and sell.slippage == 0.5  # both worse than mid by half a point


def test_charges_come_from_the_dated_cost_table() -> None:
    table = load_default_cost_table()
    qb = book_with(depth(KEY, NOW, bid=100.0, ask=101.0))
    buy = fill("BUY", key=KEY, quotes=qb, now_ms=NOW, units=75, day=DAY, cost_table=table)
    sell = fill("SELL", key=KEY, quotes=qb, now_ms=NOW, units=75, day=DAY, cost_table=table)
    assert buy.charges_total == float(table.leg_cost("BUY", 101.0, 75, DAY).total)
    assert sell.charges_total == float(table.leg_cost("SELL", 100.0, 75, DAY).total)
    assert buy.charges["stt"] == 0.0  # STT is on sells only
    assert sell.charges["stamp"] == 0.0  # stamp duty is on buys only


def test_a_stale_quote_is_no_quote() -> None:
    qb = book_with(depth(KEY, NOW - 61_000, bid=100.0, ask=101.0))
    assert qb.at(KEY, NOW) is None


def test_one_sided_book_fills_on_the_side_that_exists_with_no_mid() -> None:
    qb = QuoteBook()
    qb.on_depth([DepthQuote(KEY, NOW, (Level(0.0, 0, 101.0, 75),))])  # no bid
    buy = fill("BUY", key=KEY, quotes=qb, now_ms=NOW, units=75, day=DAY, cost_table=load_default_cost_table())
    assert buy.price == 101.0 and buy.mid is None and buy.slippage is None


def test_no_quote_falls_back_to_the_model_and_is_flagged_modelled() -> None:
    model_calls: list[int] = []

    def model() -> float:
        model_calls.append(1)
        return 95.0

    f = fill("BUY", key=KEY, quotes=QuoteBook(), now_ms=NOW, units=75, day=DAY,
             cost_table=load_default_cost_table(), model_price=model)
    assert f.source == "modelled" and f.price == 95.0
    assert f.mid is None and f.slippage is None  # no real mid, so no slippage figure
    assert model_calls == [1]


def test_no_quote_and_no_model_is_no_fill() -> None:
    assert fill("SELL", key=KEY, quotes=QuoteBook(), now_ms=NOW, units=75, day=DAY,
                cost_table=load_default_cost_table(), model_price=None) is None


def test_quote_is_used_before_the_model_is_asked() -> None:
    qb = book_with(depth(KEY, NOW, bid=100.0, ask=101.0))
    f = fill("BUY", key=KEY, quotes=qb, now_ms=NOW, units=75, day=DAY,
             cost_table=load_default_cost_table(), model_price=lambda: 1 / 0)
    assert f.source == "quote"


def _position(entry_price: float, units: int = 75) -> Position:
    entry = fill("BUY", key=KEY, quotes=book_with(depth(KEY, NOW, entry_price - 0.5, entry_price)),
                 now_ms=NOW, units=units, day=DAY, cost_table=load_default_cost_table())
    return Position(direction="LONG", key=KEY, symbol="NIFTY 22600 CE 06 OCT 26", kind="CE", strike=22600.0,
                    expiry=date(2026, 10, 6), lots=1, units=units, lot_size=units, entry=entry,
                    index_entry_time=1, reason="LE: log XZ crossed above 0")


def test_a_round_trip_nets_gross_minus_both_legs_charges() -> None:
    table = load_default_cost_table()
    book = PaperBook()
    book.open(_position(entry_price=101.0))
    exit_fill = fill("SELL", key=KEY, quotes=book_with(depth(KEY, NOW, 110.0, 110.5)), now_ms=NOW, units=75,
                     day=DAY, cost_table=table)
    trade = book.close(exit_fill, index_exit_time=2, reason="signal")
    assert trade["gross"] == round((110.0 - 101.0) * 75, 2)
    assert trade["charges"] == round(trade["charges_entry"] + trade["charges_exit"], 2)
    assert trade["net"] == round(trade["gross"] - trade["charges"], 2)
    assert book.position is None and book.trades == [trade]


def test_open_position_is_marked_to_the_bid_net_of_estimated_exit_charges() -> None:
    table = load_default_cost_table()
    book = PaperBook()
    book.open(_position(entry_price=101.0))
    mark = book.mark(bid=108.0, cost_table=table, day=DAY)
    assert mark["gross"] == round((108.0 - 101.0) * 75, 2)
    assert mark["net"] == round(mark["gross"] - mark["charges_entry"] - mark["charges_exit_est"], 2)


def test_summary_counts_wins_net_charges_modelled_and_slippage() -> None:
    trades = [
        {"net": 500.0, "gross": 600.0, "charges": 100.0, "entry_source": "quote", "exit_source": "quote",
         "entry_slippage": 0.5, "exit_slippage": 0.5},
        {"net": -200.0, "gross": -100.0, "charges": 100.0, "entry_source": "modelled", "exit_source": "quote",
         "entry_slippage": None, "exit_slippage": 1.0},
    ]
    s = summarise(trades, signals=3, unfilled=1)
    assert s["trades"] == 2 and s["wins"] == 1
    assert s["net"] == 300.0 and s["gross"] == 500.0 and s["charges"] == 200.0
    assert s["modelled_legs"] == 1
    assert s["avg_slippage_points"] == round((0.5 + 0.5 + 1.0) / 3, 4)
    assert s["signals"] == 3 and s["unfilled"] == 1
