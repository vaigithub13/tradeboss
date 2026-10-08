"""Exit rules in paper (app/exits/rules.py, the same levels and hit rule as the backtest).

Premium: every traded price of the open contract is checked; a hit sells at the bid. The index position stays (the
backtest overlay leaves the index trade open), so a same-direction entry afterwards is cancelled and an opposite one
reverses. ATR: every index price is checked against the bracket from the index fill; a hit sells the option at the
bid and the index position is flat. No rule: nothing changes (slots 1 and 2).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.contracts import Signal
from app.backtest.costs import load_default_cost_table
from app.exits.rules import ATR_1TO2, PREMIUM_1TO2
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from tests import test_paper_session as tps
from tests.test_paper_session import ist_ms, minute, quote_both, run_minutes
from tests.test_paper_stops import Stops

CE = "KEY|NIFTY 22600 CE 06 OCT 26"
PE = "KEY|NIFTY 22600 PE 06 OCT 26"


def make(strategy, rule) -> PaperSession:
    return PaperSession(day=tps.DAY, strategy=strategy, choose=tps.choose, key_for=tps.key_for, quotes=QuoteBook(),
                        cost_table=load_default_cost_table(), exit_rule=rule)


def quotes(s, now, ce=(99.0, 100.0), pe=(50.0, 51.0)) -> None:
    quote_both(s, now, ce_bid=ce[0], ce_ask=ce[1], pe_bid=pe[0], pe_ask=pe[1])


def bars(s, start, end, close=22600.0) -> list:
    out = []
    for h, m, c in run_minutes(start, end, close):
        quotes(s, ist_ms(h, m))
        out += s.on_index_minute(minute(h, m, c), now_ms=ist_ms(h, m) + 60_000)
    return out


class Fixed:
    """Returns the listed signals at closed-bar index i."""

    name = "fixed"
    params: dict = {}

    def __init__(self, plan: dict[int, list[Signal]]) -> None:
        self.plan, self.seen, self.sides = plan, 0, []

    def on_bar(self, bar, ctx):
        i = self.seen
        self.seen += 1
        self.sides.append(ctx.position.side)
        return list(self.plan.get(i, []))


# ---------------------------------------------------------------- premium
def test_a_premium_target_sells_at_the_bid_and_keeps_the_index_side() -> None:
    s = make(tps.Scripted({0: "BUY"}), PREMIUM_1TO2)
    bars(s, (9, 15), (9, 19))  # BUY at the ask, 100
    p = s.book.position
    assert p is not None and (p.levels["premium_stop"], p.levels["premium_target"]) == (80.0, 140.0)
    now = ist_ms(9, 22)
    quotes(s, now, ce=(139.0, 139.5))
    assert s.on_option_tick(CE, 139.95, now_ms=now) is False  # below the target
    assert s.on_option_tick(PE, 141.0, now_ms=now) is False  # another contract
    assert s.on_option_tick(CE, 140.0, now_ms=now) is True
    t = s.book.trades[-1]
    assert (t["exit_reason"], t["exit_price"], t["exit_at_ms"]) == ("premium_target", 139.0, now)
    assert s.book.position is None and s._side == 1  # the index position is still long, as in the backtest
    (row,) = s.snapshot()["report"]
    assert row["exit_reason"] == "target" and row["premium_stop"] == 80.0 and row["premium_target"] == 140.0
    assert row["r_multiple"] == pytest.approx(t["net"] / (20.0 * 75), abs=1e-3)
    assert s.snapshot()["summary"]["exits"] == {"target": 1, "stop": 0, "reversal": 0, "square-off": 0, "other": 0}


def test_after_a_premium_stop_a_same_direction_entry_is_cancelled_and_an_opposite_one_reverses() -> None:
    strat = Stops({0: [("BUY", 22610.0, "LE"), ("SELL", 22590.0, "SE")], 1: [("BUY", 22620.0, "LE")]})
    s = make(strat, PREMIUM_1TO2)
    bars(s, (9, 15), (9, 19))
    now = ist_ms(9, 20) + 30_000
    quotes(s, now)
    s.on_index_tick(22611.0, now_ms=now)  # LONG, call at 100
    now += 10_000
    quotes(s, now, ce=(78.5, 79.0))
    assert s.on_option_tick(CE, 79.5, now_ms=now) is True  # at or below 80: the premium stop, sold at the bid
    assert s.book.trades[-1]["exit_reason"] == "premium_stop" and s.book.trades[-1]["exit_price"] == 78.5
    bars(s, (9, 20), (9, 24))  # bar 1 re-arms LE at 22620
    now = ist_ms(9, 25) + 30_000
    quotes(s, now)
    assert s.on_index_tick(22621.0, now_ms=now) == []  # still long on the index: cancelled (pyramiding 0)
    assert s.book.position is None
    out = s.on_index_tick(22589.0, now_ms=now + 1000)
    assert len(out) == 1 and out[0]["side"] == "SELL" and s.book.position.direction == "SHORT"


def test_no_rule_means_no_exit_checks() -> None:
    s = make(tps.Scripted({0: "BUY"}), None)
    bars(s, (9, 15), (9, 19))
    now = ist_ms(9, 22)
    quotes(s, now, ce=(10.0, 11.0))
    assert s.on_option_tick(CE, 10.0, now_ms=now) is False and s.book.position is not None
    assert s.book.position.levels is None
    assert s.snapshot()["report"][0:0] == [] and s.snapshot()["summary"]["exits"]["stop"] == 0


# ---------------------------------------------------------------- ATR (index)
def test_an_atr_stop_on_the_index_sells_the_option_and_the_index_side_is_flat() -> None:
    strat = Fixed({0: [Signal("BUY", 1, stop_points=10.0, target_points=20.0)], 2: [Signal("BUY", 1)]})
    s = make(strat, ATR_1TO2)
    bars(s, (9, 15), (9, 19))  # BUY at the 22600 close (no tick after the bar ended): levels 22590 / 22620
    p = s.book.position
    assert (p.levels["index_stop"], p.levels["index_target"]) == (22590.0, 22620.0)
    now = ist_ms(9, 21)
    quotes(s, now, ce=(95.0, 95.5))
    assert s.on_index_tick(22590.5, now_ms=now) == []
    s.on_index_tick(22590.0, now_ms=now + 1000)
    t = s.book.trades[-1]
    assert (t["exit_reason"], t["exit_price"]) == ("index_stop", 95.0) and s._side == 0
    bars(s, (9, 20), (9, 29))
    assert strat.sides[1:3] == [0, 0]  # the strategy sees itself flat after the index stop
    assert s.book.position is not None and s.book.position.levels["index_stop"] is None  # no points: no bracket
    row = s.snapshot()["report"][0]
    assert row["exit_reason"] == "stop" and row["index_stop"] == 22590.0


def test_an_atr_target_on_a_short_index_position() -> None:
    strat = Fixed({0: [Signal("SELL", 1, stop_points=10.0, target_points=20.0)]})
    s = make(strat, ATR_1TO2)
    bars(s, (9, 15), (9, 19))  # SHORT: stop 22610, target 22580
    now = ist_ms(9, 21)
    quotes(s, now, pe=(70.0, 70.5))
    s.on_index_tick(22579.0, now_ms=now)
    assert s.book.trades[-1]["exit_reason"] == "index_target" and s.book.trades[-1]["exit_price"] == 70.0


def test_square_off_and_reversals_still_close_trades() -> None:
    s = make(tps.Scripted({0: "BUY", 1: "SELL"}), PREMIUM_1TO2)
    bars(s, (9, 15), (9, 24))
    assert s.book.trades[-1]["exit_reason"] == "signal" and s.book.position.direction == "SHORT"
    now = ist_ms(15, 15)
    quotes(s, now)
    s.on_clock(now)
    rows = s.snapshot()["report"]
    assert [r["exit_reason"] for r in rows] == ["reversal", "square-off"]


def test_nothing_exits_after_the_square_off_time() -> None:
    s = make(tps.Scripted({0: "BUY"}), PREMIUM_1TO2)
    bars(s, (9, 15), (9, 19))
    s.book.position  # noqa: B018
    now = ist_ms(15, 16)
    quotes(s, now, ce=(10.0, 11.0))
    assert s.on_option_tick(CE, 10.0, now_ms=now) is False


def test_a_restart_keeps_the_levels() -> None:
    s = make(tps.Scripted({0: "BUY"}), PREMIUM_1TO2)
    bars(s, (9, 15), (9, 19))
    saved = s.snapshot()
    again = make(tps.Scripted({}), PREMIUM_1TO2)
    again.restore(saved)
    assert again.book.position.levels == s.book.position.levels
    now = ist_ms(9, 40)
    quotes(again, now, ce=(141.0, 141.5))
    assert again.on_option_tick(CE, 141.0, now_ms=now) is True


# ---------------------------------------------------------------- runner, desk, week
def test_the_exit_rule_travels_in_the_params_and_builds_the_same_strategy_as_the_backtest() -> None:
    from app.exits.rules import WithExits
    from app.paper.live import paper_strategy

    strat, rule = paper_strategy("log_xz", {"exit_rule": "atr_1to2"})
    assert isinstance(strat, WithExits) and rule == ATR_1TO2
    strat, rule = paper_strategy("price_channel", {"length": 20, "exit_rule": "premium_1to2"})
    assert rule == PREMIUM_1TO2 and strat.params["length"] == 20
    strat, rule = paper_strategy("log_xz", {})
    assert rule is None and not isinstance(strat, WithExits)
    with pytest.raises(ValueError):
        paper_strategy("log_xz", {"exit_rule": "nope"})


def test_the_desk_has_four_slots() -> None:
    from pathlib import Path

    from app.paper.desk import SLOTS, PaperDesk

    assert SLOTS == ("1", "2", "3", "4")
    assert PaperDesk.slot_dir(Path("/p"), "1") == Path("/p") and PaperDesk.slot_dir(Path("/p"), "4") == Path("/p/slot4")


def test_the_week_counts_exits(tmp_path) -> None:
    from app.paper.store import save_day, weekly_summary

    base = {"trades": 1, "wins": 1, "gross": 1, "charges": 0, "net": 1, "modelled_legs": 0, "signals": 1, "unfilled": 0}
    save_day(tmp_path, date(2026, 10, 12), {"summary": {**base, "exits": {"target": 1, "stop": 0, "reversal": 2,
                                                                          "square-off": 1, "other": 0}}})
    save_day(tmp_path, date(2026, 10, 13), {"summary": {**base, "exits": {"target": 0, "stop": 3, "reversal": 0,
                                                                          "square-off": 1, "other": 1}}})
    save_day(tmp_path, date(2026, 10, 14), {"summary": base})  # a day from before exits were counted
    week = weekly_summary(tmp_path, date(2026, 10, 14))
    assert week["exits"] == {"target": 1, "stop": 3, "reversal": 2, "square-off": 2, "other": 1}
    assert week["days"][0]["exits"]["reversal"] == 2
