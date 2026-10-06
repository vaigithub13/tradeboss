"""PaperSession: the strategy sees closed bars only, signals become fills, the book follows."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.backtest.contracts import Signal
from app.backtest.costs import load_default_cost_table
from app.live.spreads.decode import DepthQuote, Level
from app.options.contract import OptionContract
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.paper.store import load_day, save_day

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 10, 5)


def ist_ms(h: int, m: int) -> int:
    return int(datetime(2026, 10, 5, h, m, tzinfo=IST).timestamp()) * 1000


class Scripted:
    """A strategy whose signals are fixed by closed-bar index. Records what on_bar was shown."""

    name = "scripted"

    def __init__(self, script: dict[int, str]) -> None:
        self.script = script
        self.seen: list[int] = []

    def on_bar(self, bar: dict, ctx) -> list[Signal]:
        i = len(self.seen)
        self.seen.append(int(bar["time"]))
        side = self.script.get(i)
        if side is None:
            return []
        return [Signal(side, 1, "MARKET", tag="LE" if side == "BUY" else "SE")]


def choose(direction: str, spot: float, on: date) -> OptionContract:
    kind = "CE" if direction == "LONG" else "PE"
    return OptionContract(kind=kind, strike=22600.0, expiry=date(2026, 10, 6), cycle="weekly", lot_size=75,
                          symbol=f"NIFTY 22600 {kind} 06 OCT 26", step=50, step_verification="verified")


def key_for(symbol: str) -> str | None:
    return "KEY|" + symbol


def session(strategy, *, model_price=None) -> PaperSession:
    return PaperSession(day=DAY, strategy=strategy, choose=choose, key_for=key_for, quotes=QuoteBook(),
                        cost_table=load_default_cost_table(), model_price=model_price)


def minute(h: int, m: int, close: float) -> dict:
    t = int(datetime(2026, 10, 5, h, m, tzinfo=IST).timestamp())
    return {"time": t, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 10,
            "source": "i1"}


def quote_both(s: PaperSession, now: int, *, ce_bid: float, ce_ask: float, pe_bid: float, pe_ask: float) -> None:
    for kind, bid, ask in (("CE", ce_bid, ce_ask), ("PE", pe_bid, pe_ask)):
        key = key_for(f"NIFTY 22600 {kind} 06 OCT 26")
        s.on_depth([DepthQuote(key, now, (Level(bid, 75, ask, 75),))])


def feed(s: PaperSession, minutes: list[tuple[int, int, float]], quotes_at=None) -> list:
    out = []
    for h, m, close in minutes:
        now = ist_ms(h, m)
        if quotes_at is not None:
            quotes_at(s, now)
        out += s.on_index_minute(minute(h, m, close), now_ms=now)
    return out


def run_minutes(start: tuple[int, int], end: tuple[int, int], close: float = 22600.0) -> list[tuple[int, int, float]]:
    h, m = start
    out = []
    while (h, m) <= end:
        out.append((h, m, close))
        m += 1
        if m == 60:
            h, m = h + 1, 0
    return out


def test_strategy_is_shown_only_closed_bars_in_order() -> None:
    strat = Scripted({})
    s = session(strat)
    feed(s, run_minutes((9, 15), (9, 34)))
    # 09:15..09:34: four 5m bars, each complete when its own fifth minute is final
    assert strat.seen == [ist_ms(9, 15) // 1000, ist_ms(9, 20) // 1000, ist_ms(9, 25) // 1000,
                          ist_ms(9, 30) // 1000]


def test_buy_opens_an_atm_call_at_the_live_ask_when_the_bar_closes() -> None:
    strat = Scripted({0: "BUY"})
    s = session(strat)
    signals = feed(s, run_minutes((9, 15), (9, 21)), quotes_at=lambda sess, now: quote_both(
        sess, now, ce_bid=99.0, ce_ask=100.0, pe_bid=50.0, pe_ask=51.0))
    assert len(signals) == 1
    sig = signals[0]
    assert (sig["side"], sig["status"], sig["symbol"]) == ("BUY", "filled", "NIFTY 22600 CE 06 OCT 26")
    assert sig["fill_source"] == "quote" and sig["index_price"] == 22600.0
    assert sig["decided_at_ms"] == ist_ms(9, 19)  # the 09:15 bar is complete when its fifth minute, 09:19, is final
    pos = s.book.position
    assert pos is not None and pos.direction == "LONG" and pos.entry.price == 100.0


def test_sell_closes_the_call_at_the_bid_and_opens_a_put_at_the_ask() -> None:
    strat = Scripted({0: "BUY", 1: "SELL"})
    s = session(strat)

    def quotes(sess, now):
        quote_both(sess, now, ce_bid=110.0, ce_ask=111.0, pe_bid=60.0, pe_ask=61.0)

    feed(s, run_minutes((9, 15), (9, 31)), quotes_at=quotes)
    assert len(s.book.trades) == 1
    t = s.book.trades[0]
    assert (t["symbol"], t["entry_price"], t["exit_price"]) == ("NIFTY 22600 CE 06 OCT 26", 111.0, 110.0)
    assert t["exit_reason"] == "signal"
    pos = s.book.position
    assert pos is not None and pos.direction == "SHORT" and pos.symbol == "NIFTY 22600 PE 06 OCT 26"
    assert pos.entry.price == 61.0


def test_square_off_at_1515_closes_the_open_position_at_the_bid() -> None:
    """Time-based, like the backtest's 15:15 square-off: the first feed frame at or after 15:15:00 (exchange time)
    closes the position at that moment's bid. No exchange-final minute is needed."""
    strat = Scripted({0: "BUY"})
    s = session(strat)

    def quotes(sess, now):
        quote_both(sess, now, ce_bid=99.0, ce_ask=100.0, pe_bid=50.0, pe_ask=51.0)

    feed(s, run_minutes((9, 15), (15, 13)), quotes_at=quotes)
    quote_both(s, ist_ms(15, 15) - 1000, ce_bid=98.0, ce_ask=98.5, pe_bid=50.0, pe_ask=51.0)
    assert s.on_clock(ist_ms(15, 15) - 1000) is False  # 15:14:59: still open
    assert s.book.position is not None
    quote_both(s, ist_ms(15, 15), ce_bid=97.0, ce_ask=97.5, pe_bid=50.0, pe_ask=51.0)
    assert s.on_clock(ist_ms(15, 15)) is True
    assert s.book.position is None
    t = s.book.trades[0]
    assert t["exit_reason"] == "square_off"
    assert t["exit_price"] == 97.0 and t["exit_source"] == "quote"
    assert t["index_exit_time"] == ist_ms(15, 15) // 1000 and t["exit_at_ms"] == ist_ms(15, 15)
    assert s.on_clock(ist_ms(15, 16)) is False  # nothing left to close


def test_an_exchange_final_minute_after_1515_does_not_square_off_by_itself() -> None:
    """The minute bars no longer decide the square-off; only the clock does (a late I1 would exit late)."""
    strat = Scripted({0: "BUY"})
    s = session(strat)
    feed(s, run_minutes((9, 15), (15, 17)), quotes_at=lambda sess, now: quote_both(
        sess, now, ce_bid=99.0, ce_ask=100.0, pe_bid=50.0, pe_ask=51.0))
    assert s.book.position is not None


def test_no_quote_and_no_model_records_an_unfilled_signal_and_opens_nothing() -> None:
    strat = Scripted({0: "BUY"})
    s = session(strat)
    signals = feed(s, run_minutes((9, 15), (9, 21)))
    assert signals[0]["status"] == "unfilled"
    assert "no quote" in signals[0]["note"]
    assert s.book.position is None and s.book.trades == []


def test_no_quote_uses_the_model_and_is_flagged() -> None:
    strat = Scripted({0: "BUY"})
    s = session(strat, model_price=lambda contract, spot, ts_ms: 95.0)
    signals = feed(s, run_minutes((9, 15), (9, 21)))
    assert signals[0]["status"] == "filled" and signals[0]["fill_source"] == "modelled"
    assert s.book.position.entry.source == "modelled"


def test_contract_not_in_the_instrument_master_is_unfilled() -> None:
    strat = Scripted({0: "BUY"})
    s = PaperSession(day=DAY, strategy=strat, choose=choose, key_for=lambda sym: None, quotes=QuoteBook(),
                     cost_table=load_default_cost_table(), model_price=lambda c, sp, ts: 95.0)
    signals = feed(s, run_minutes((9, 15), (9, 21)))
    assert signals[0]["status"] == "unfilled" and "instrument master" in signals[0]["note"]


def test_a_failed_close_keeps_the_old_position_and_does_not_open_the_new_one() -> None:
    strat = Scripted({0: "BUY", 1: "SELL"})
    s = session(strat)  # no model: the PE can be bought from a quote but the CE cannot be sold without one

    def quotes(sess, now):
        quote_both(sess, now, ce_bid=0.0, ce_ask=100.0, pe_bid=0.0, pe_ask=61.0)

    signals = feed(s, run_minutes((9, 15), (9, 31)), quotes_at=quotes)
    sell = [x for x in signals if x["side"] == "SELL"][0]
    assert sell["status"] == "unfilled"
    assert s.book.position is not None and s.book.position.direction == "LONG"


def test_save_and_load_round_trip_per_day(tmp_path) -> None:
    strat = Scripted({0: "BUY"})
    s = session(strat)
    feed(s, run_minutes((9, 15), (9, 21)), quotes_at=lambda sess, now: quote_both(
        sess, now, ce_bid=99.0, ce_ask=100.0, pe_bid=50.0, pe_ask=51.0))
    payload = s.snapshot()
    save_day(tmp_path, DAY, payload)
    back = load_day(tmp_path, DAY)
    assert back["signals"] == payload["signals"]
    assert back["summary"]["signals"] == 1
    assert load_day(tmp_path, date(2026, 10, 6)) is None


def test_a_signal_on_the_1510_bar_is_not_traded_because_its_fill_would_be_after_the_square_off() -> None:
    """The backtest fills on the next bar and blocks a fill at or after 15:15 (after_square_off). The 15:10 bar
    closes at 15:15, so its signal is recorded and not traded, here as there."""
    strat = Scripted({k: "BUY" for k in range(71, 72)})  # bar 71 = 15:10 (09:15 + 71 x 5 min)
    s = session(strat)
    out = feed(s, run_minutes((9, 15), (15, 14)), quotes_at=lambda sess, now: quote_both(
        sess, now, ce_bid=99.0, ce_ask=100.0, pe_bid=50.0, pe_ask=51.0))
    assert strat.seen[-1] == ist_ms(15, 10) // 1000
    assert [(r["side"], r["status"], r["note"]) for r in out] == [("BUY", "unfilled", "after the 15:15 square-off")]
    assert s.book.position is None
