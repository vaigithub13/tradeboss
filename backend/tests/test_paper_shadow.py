"""Paper "shadow exit": what a resting exchange order would have done when an exchange-final (I1) option minute
crossed a premium stop or target that none of paper's ticks reached.

Paper triggers on the traded prices the feed sends; the exchange's minute bar also holds trades between those
snapshots. Paper's own exit is kept; the shadow is recorded beside it (the minute, the level, the price: a stop at the
level, or the bar's open when the bar opened through it, as the backtest's gap fill; a target at the level) with its own net. The case is slot 4 on
4 Nov 2026 (Price Channel 20, 1:2 premium): 23900 CE bought at 176.55, stop 141.24; the 10:23 I1 bar's low 140.25,
paper's lowest 10:23 tick 141.80; the first tick at or below the stop at 10:25:23, sold at the bid 143.05. The
fixture contract here has a lot of 75 (the 4 Nov contract had 65); the prices are that day's.
"""

from __future__ import annotations

import pytest

from app.backtest.costs import load_default_cost_table
from app.exits.rules import PREMIUM_1TO2
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.paper.shadow import shadow_exit
from tests import test_paper_session as tps
from tests.test_paper_session import ist_ms, minute, quote_both, run_minutes

CE = "KEY|NIFTY 22600 CE 06 OCT 26"


def make(rule=PREMIUM_1TO2) -> PaperSession:
    return PaperSession(day=tps.DAY, strategy=tps.Scripted({0: "BUY"}), choose=tps.choose, key_for=tps.key_for,
                        quotes=QuoteBook(), cost_table=load_default_cost_table(), exit_rule=rule)


def ce_quote(s: PaperSession, now: int, bid: float, ask: float) -> None:
    quote_both(s, now, ce_bid=bid, ce_ask=ask, pe_bid=50.0, pe_ask=51.0)


def bought_at_176_55(rule=PREMIUM_1TO2) -> PaperSession:
    """The call bought at the ask 176.55 at 09:20:00 (stop 141.24, target 247.17)."""
    s = make(rule)
    for h, m, c in run_minutes((9, 15), (9, 19)):
        ce_quote(s, ist_ms(h, m), 176.3, 176.55)
        s.on_index_minute(minute(h, m, c), now_ms=ist_ms(h, m) + 60_000)
    return s


def i1(h: int, m: int, o: float, hi: float, lo: float, c: float) -> dict:
    return {"time": ist_ms(h, m) // 1000, "open": o, "high": hi, "low": lo, "close": c, "volume": 1, "source": "i1"}


def tick(s: PaperSession, ms: int, price: float, bid: float) -> bool:
    ce_quote(s, ms, bid, bid + 0.25)
    return s.on_option_tick(CE, price, now_ms=ms)


def slot_4_on_4_nov(s: PaperSession) -> None:
    """10:23 ticks 160.65 .. 141.80, then its I1 bar (low 140.25); 10:24 ticks and bar; 10:25:23 a tick at 141.20."""
    t = ist_ms(10, 23)
    for i, price in enumerate((160.65, 150.0, 141.8, 144.9)):
        assert tick(s, t + 1_000 + i * 10_000, price, price - 0.15) is False
    assert s.on_option_minute(CE, i1(10, 23, 161.0, 161.0, 140.25, 144.7), now_ms=ist_ms(10, 24) + 200) is True
    for i, price in enumerate((144.85, 150.85, 143.65, 146.8)):
        assert tick(s, ist_ms(10, 24) + 1_000 + i * 10_000, price, price - 0.15) is False
    assert s.on_option_minute(CE, i1(10, 24, 145.1, 151.85, 143.2, 146.65), now_ms=ist_ms(10, 25) + 200) is False
    assert tick(s, ist_ms(10, 25) + 23_000, 141.2, 143.05) is True


def test_slot_4_on_4_nov_keeps_papers_exit_and_records_the_resting_orders() -> None:
    s = bought_at_176_55()
    p = s.book.position
    assert p is not None and p.entry.price == 176.55 and p.levels["premium_stop"] == 141.24
    slot_4_on_4_nov(s)
    t = s.book.trades[-1]
    assert (t["exit_reason"], t["exit_price"], t["exit_at_ms"]) == ("premium_stop", 143.05, ist_ms(10, 25) + 23_000)
    sh = t["shadow"]
    assert sh["time"] == ist_ms(10, 23) // 1000
    assert (sh["level"], sh["reason"], sh["price"]) == ("premium_stop", "stop", 141.24)
    assert sh["tick_low"] == 141.8 and sh["bar"] == {"open": 161.0, "high": 161.0, "low": 140.25, "close": 144.7}
    exit_charges = float(s.cost_table.leg_cost("SELL", 141.24, t["units"], s.day).total)
    gross = round((141.24 - 176.55) * t["units"], 2)
    assert sh["gross"] == gross
    assert sh["net"] == pytest.approx(round(gross - t["charges_entry"] - exit_charges, 2), abs=0.011)
    assert sh["net"] < t["net"]  # paper's late tick sold higher than the resting stop would have


def test_the_report_row_and_the_summary_carry_both_nets() -> None:
    s = bought_at_176_55()
    slot_4_on_4_nov(s)
    snap = s.snapshot()
    (row,) = snap["report"]
    t = s.book.trades[-1]
    assert row["net"] == t["net"] and row["shadow_net"] == t["shadow"]["net"]
    assert row["shadow"] == {"exit_time": ist_ms(10, 23) // 1000, "exit_premium": 141.24, "exit_reason": "stop",
                             "net": t["shadow"]["net"]}
    summary = snap["summary"]
    assert summary["shadow_net"] == t["shadow"]["net"]
    assert summary["shadow_diff"] == round(t["shadow"]["net"] - t["net"], 2)


def test_without_a_shadow_the_shadow_net_is_papers_net() -> None:
    s = bought_at_176_55()
    now = ist_ms(10, 0)
    assert tick(s, now + 5_000, 150.0, 149.85) is False
    assert s.on_option_minute(CE, i1(10, 0, 152.0, 153.0, 149.5, 150.5), now_ms=now + 60_200) is False
    ce_quote(s, now + 70_000, 150.0, 150.25)
    s.close_now(now + 70_000, "stopped")
    (row,) = s.snapshot()["report"]
    assert row["shadow"] is None and row["shadow_net"] == row["net"]
    assert s.snapshot()["summary"]["shadow_diff"] == 0.0


def test_a_minute_the_ticks_reached_has_no_shadow() -> None:
    s = bought_at_176_55()
    s.quotes = QuoteBook()  # no quote: paper's stop is hit by a tick but cannot sell, so the position stays open
    t = ist_ms(10, 23)
    assert s.on_option_tick(CE, 141.0, now_ms=t + 5_000) is False and s.book.position is not None
    assert s.on_option_minute(CE, i1(10, 23, 161.0, 161.0, 140.25, 144.7), now_ms=t + 60_200) is False
    assert s.book.position.shadow is None


def test_a_minute_that_began_before_the_entry_is_not_read() -> None:
    """A bar that started before paper bought (here the call was bought at 09:20:00) holds trades from before the
    entry; a stop entry inside a minute leaves that minute out the same way."""
    s = bought_at_176_55()
    assert s.book.position.entry.at_ms == ist_ms(9, 20)
    assert s.on_option_minute(CE, i1(9, 19, 176.5, 177.0, 140.0, 176.0), now_ms=ist_ms(9, 20) + 200) is False
    assert s.book.position.shadow is None


def test_a_bar_that_arrives_after_paper_closed_is_still_recorded() -> None:
    """The 10:23 I1 bar reaches paper only after the 10:25:23 exit: the closed trade takes the shadow."""
    s = bought_at_176_55()
    tick(s, ist_ms(10, 23) + 30_000, 141.8, 141.65)
    assert tick(s, ist_ms(10, 25) + 23_000, 141.2, 143.05) is True
    assert s.on_option_minute(CE, i1(10, 23, 161.0, 161.0, 140.25, 144.7), now_ms=ist_ms(10, 25) + 24_000) is True
    assert s.book.trades[-1]["shadow"]["time"] == ist_ms(10, 23) // 1000
    assert s.on_option_minute(CE, i1(10, 25, 146.25, 149.85, 137.4, 142.4), now_ms=ist_ms(10, 26) + 200) is False


def test_only_the_first_crossing_counts() -> None:
    s = bought_at_176_55()
    slot_4_on_4_nov(s)
    assert s.book.trades[-1]["shadow"]["time"] == ist_ms(10, 23) // 1000


def test_no_rule_no_shadow() -> None:
    s = bought_at_176_55(rule=None)
    assert s.on_option_minute(CE, i1(10, 23, 161.0, 161.0, 1.0, 144.7), now_ms=ist_ms(10, 24) + 200) is False


# ---------------------------------------------------------------- the rule on one minute
LEVELS = {"premium_stop": 141.24, "premium_target": 247.17, "index_stop": None, "index_target": None}


def test_a_stop_crossed_inside_the_bar_fills_at_the_level() -> None:
    sh = shadow_exit(LEVELS, {"open": 161.0, "high": 161.0, "low": 140.25, "close": 144.7}, tick_low=141.8,
                     tick_high=160.65)
    assert (sh["level"], sh["reason"], sh["price"]) == ("premium_stop", "stop", 141.24)


def test_a_bar_that_opened_through_the_stop_fills_at_its_open() -> None:
    """As the backtest engine's gap fill (app/exits/rules.py first_hit): the open, not the bar's low."""
    sh = shadow_exit(LEVELS, {"open": 139.0, "high": 142.0, "low": 136.5, "close": 141.0}, tick_low=None,
                     tick_high=None)
    assert (sh["reason"], sh["price"]) == ("stop", 139.0)


def test_a_target_fills_at_the_level() -> None:
    sh = shadow_exit(LEVELS, {"open": 240.0, "high": 249.0, "low": 239.0, "close": 245.0}, tick_low=240.0,
                     tick_high=246.9)
    assert (sh["level"], sh["reason"], sh["price"]) == ("premium_target", "target", 247.17)


def test_a_level_the_ticks_reached_is_not_a_shadow() -> None:
    assert shadow_exit(LEVELS, {"open": 161.0, "high": 161.0, "low": 140.25, "close": 144.7}, tick_low=141.2,
                       tick_high=160.0) is None
    assert shadow_exit(LEVELS, {"open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0}, tick_low=None,
                       tick_high=None) is None
