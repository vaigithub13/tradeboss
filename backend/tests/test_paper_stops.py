"""Stop orders in paper (Price Channel): armed at the bar's close, filled when the live index price crosses the
level, as the backtest fills a stop when a 1m bar trades through it. Re-arming replaces by tag; a fill against
the position reverses it; a fill in the direction already held is cancelled (pyramiding 0, as the broker does)."""

from __future__ import annotations

from app.backtest.contracts import Signal
from app.strategies.price_channel import PriceChannel
from tests import test_paper_session as tps
from tests.test_paper_session import ist_ms, minute, quote_both, run_minutes, session


class Stops:
    """Arms the stops listed for each closed bar index: {i: [(side, price, tag)]}. Records the bars it saw."""

    name = "stops"

    def __init__(self, plan: dict[int, list[tuple[str, float, str]]]) -> None:
        self.plan, self.seen, self.history_lengths = plan, [], []

    def on_bar(self, bar, ctx):
        i = len(self.seen)
        self.seen.append(int(bar["time"]))
        self.history_lengths.append(len(ctx.bars))
        out = []
        for side, price, tag in self.plan.get(i, []):
            ctx.cancel_working(tag)
            out.append(Signal(side, 1, "SL", price, tag=tag))
        return out


def quotes(s, now):
    quote_both(s, now, ce_bid=99.0, ce_ask=100.0, pe_bid=50.0, pe_ask=51.0)


def armed_session(plan) -> tuple:
    strat = Stops(plan)
    s = session(strat)
    for h, m, close in run_minutes((9, 15), (9, 19), 22600.0):  # bar 0 (09:15) closes with the 09:19 minute
        quotes(s, ist_ms(h, m))
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    return strat, s


def tick(s, h: int, m: int, sec: int, price: float) -> list:
    now = ist_ms(h, m) + sec * 1000
    quotes(s, now)
    return s.on_index_tick(price, now_ms=now)


def test_a_stop_is_armed_at_the_close_and_fills_when_the_index_crosses_it() -> None:
    _strat, s = armed_session({0: [("BUY", 22610.0, "LE"), ("SELL", 22590.0, "SE")]})
    assert sorted(o["tag"] for o in s.working.values()) == ["LE", "SE"] and s.signals == []
    assert tick(s, 9, 20, 30, 22605.0) == []
    rec = tick(s, 9, 21, 5, 22611.5)
    assert len(rec) == 1 and (rec[0]["side"], rec[0]["status"], rec[0]["fill_price"]) == ("BUY", "filled", 100.0)
    assert rec[0]["time"] == ist_ms(9, 20) // 1000  # the 5m bar the fill is in
    assert rec[0]["index_price"] == 22611.5 and rec[0]["order_price"] == 22610.0
    assert s.book.position.direction == "LONG" and "LE" not in s.working and "SE" in s.working


def test_the_other_side_reverses_and_the_same_side_again_is_cancelled() -> None:
    _strat, s = armed_session({0: [("BUY", 22610.0, "LE"), ("SELL", 22590.0, "SE")]})
    tick(s, 9, 21, 0, 22612.0)
    rec = tick(s, 9, 22, 0, 22588.0)
    assert rec[0]["side"] == "SELL" and s.book.position.direction == "SHORT"
    assert s.book.trades[0]["exit_price"] == 99.0 and s.book.trades[0]["exit_reason"] == "reverse"


def test_a_fill_in_the_direction_already_held_is_cancelled() -> None:
    strat = Stops({0: [("BUY", 22610.0, "LE")], 1: [("BUY", 22620.0, "LE2")]})
    s = session(strat)
    for h, m, close in run_minutes((9, 15), (9, 19), 22600.0):
        quotes(s, ist_ms(h, m))
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    tick(s, 9, 20, 10, 22611.0)
    for h, m, close in run_minutes((9, 20), (9, 24), 22611.0):
        quotes(s, ist_ms(h, m))
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    assert "LE2" in s.working
    assert tick(s, 9, 26, 0, 22625.0) == [] and "LE2" not in s.working  # cancelled: already long
    assert len(s.book.trades) == 0 and s.book.position.direction == "LONG"


def test_rearming_replaces_the_level_by_tag() -> None:
    strat = Stops({0: [("BUY", 22610.0, "LE")], 1: [("BUY", 22650.0, "LE")]})
    s = session(strat)
    for h, m, close in run_minutes((9, 15), (9, 24), 22600.0):
        quotes(s, ist_ms(h, m))
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    assert s.working["LE"]["price"] == 22650.0
    assert tick(s, 9, 26, 0, 22620.0) == []


def test_no_fill_before_the_order_exists_or_after_the_square_off() -> None:
    strat, s = armed_session({0: [("BUY", 22610.0, "LE")]})
    early = ist_ms(9, 19) + 30_000  # before the bar was decided (the order did not exist yet)
    assert s.on_index_tick(22700.0, now_ms=early) == []
    for h, m, close in run_minutes((9, 20), (15, 13), 22600.0):
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    s.on_clock(ist_ms(15, 15))
    assert s.working == {}  # the square-off cancels working orders
    assert tick(s, 15, 16, 0, 22700.0) == []


def test_the_strategy_reads_the_warm_up_and_the_live_bars() -> None:
    strat = Stops({})
    s = session(strat)
    s.warm([{"time": ist_ms(9, 15) // 1000 - 86_400 + 300 * i, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
             "volume": 0} for i in range(3)])
    for h, m, close in run_minutes((9, 15), (9, 24), 22600.0):
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    assert strat.history_lengths == [1, 2, 3, 4, 5]  # 3 warm-up bars, then 09:15 and 09:20


def test_price_channel_runs_in_paper_and_arms_both_stops() -> None:
    s = session(PriceChannel(length=2))
    for h, m, close in run_minutes((9, 15), (9, 34), 22600.0):
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    assert sorted(o["tag"] for o in s.working.values()) == ["LE", "SE"]
    assert s.working["LE"]["side"] == "BUY" and s.working["LE"]["price"] > s.working["SE"]["price"]


def test_a_level_crossed_while_the_bar_was_being_decided_fills_when_the_stop_is_armed() -> None:
    """5 Oct 10:55: the backtest's sell stop was active from 10:55:00 and the 10:55 minute traded through it. Paper
    knew the level only when the 10:50 bar was decided (~a minute later). The cross in between fills on arming."""
    strat = Stops({0: [("SELL", 22590.0, "SE")]})
    s = session(strat)
    for h, m, close in run_minutes((9, 15), (9, 18), 22600.0):
        quotes(s, ist_ms(h, m))
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    tick(s, 9, 20, 10, 22585.0)  # after the bar's end, before the bar is decided: no order yet
    tick(s, 9, 20, 40, 22596.0)
    quotes(s, ist_ms(9, 20) + 50_000)
    out = s.on_index_minute(minute(9, 19, 22600.0), now_ms=ist_ms(9, 20) + 50_000)  # decided at 09:20:50
    assert len(out) == 1 and out[0]["side"] == "SELL" and out[0]["status"] == "filled"
    assert out[0]["time"] == ist_ms(9, 20) // 1000 and "before the order was armed" in out[0]["note"]
    assert out[0]["index_price"] == 22596.0  # filled at the live price when armed, not at the level


# ---------------------------------------------------------------- ATM is taken at the fill moment
def _recording(s) -> list[float]:
    spots: list[float] = []
    inner = s.choose

    def choose(direction, spot, on):  # noqa: ANN001, ANN202
        spots.append(spot)
        return inner(direction, spot, on)

    s.choose = choose
    return spots


def test_a_market_fill_takes_atm_from_the_live_index_when_it_fills_not_the_bar_close() -> None:
    s = tps.session(tps.Scripted({0: "BUY"}))
    spots = _recording(s)
    for h, m, close in run_minutes((9, 15), (9, 18), 22600.0):
        quotes(s, ist_ms(h, m))
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    tick(s, 9, 20, 30, 22631.0)  # the index after the bar ended, before the 09:19 minute is final
    quotes(s, ist_ms(9, 20) + 50_000)
    s.on_index_minute(minute(9, 19, 22600.0), now_ms=ist_ms(9, 20) + 50_000)  # decided at 09:20:50
    (rec,) = [r for r in s.signals if r["side"] == "BUY"]
    assert spots == [22631.0] and rec["index_price"] == 22631.0


def test_a_market_fill_with_no_tick_since_the_bar_ended_uses_the_bar_close() -> None:
    s = tps.session(tps.Scripted({0: "BUY"}))
    spots = _recording(s)
    for h, m, close in run_minutes((9, 15), (9, 19), 22600.0):
        quotes(s, ist_ms(h, m))
        s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    assert spots == [22600.0]


def test_a_stop_fill_takes_atm_from_its_level_and_a_late_fill_from_the_live_price() -> None:
    _strat, s = armed_session({0: [("BUY", 22610.0, "LE")]})
    spots = _recording(s)
    tick(s, 9, 21, 5, 22626.0)  # crosses 22610 with a jump: the backtest fills at the level, so ATM of 22610
    assert spots == [22610.0]

    strat = Stops({0: [("SELL", 22590.0, "SE")]})
    late = tps.session(strat)
    spots = _recording(late)
    for h, m, close in run_minutes((9, 15), (9, 18), 22600.0):
        quotes(late, ist_ms(h, m))
        late.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m) + 60_000)
    tick(late, 9, 20, 10, 22585.0)
    tick(late, 9, 20, 40, 22596.0)
    quotes(late, ist_ms(9, 20) + 50_000)
    late.on_index_minute(minute(9, 19, 22600.0), now_ms=ist_ms(9, 20) + 50_000)
    assert spots == [22596.0]
