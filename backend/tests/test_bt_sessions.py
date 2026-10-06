"""Sessions (5): session filter, intraday square-off, no carry unless the strategy allows it."""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.engine import ConfigError
from tests.bt_helpers import (
    MON,
    TUE,
    WED,
    Scripted,
    buy,
    day_bars,
    exit_,
    fills,
    flat,
    full_day,
    hhmm,
    ist,
    kinds,
    minute_index,
    run,
    sell,
)


def test_5a_the_session_filter_decides_which_bars_the_strategy_sees() -> None:
    candles = day_bars(MON, flat(100, 5)) + day_bars(TUE, flat(101, 5)) + day_bars(WED, flat(102, 5))
    labels = {date(2026, 1, 6): "special_short"}

    default = Scripted()
    run(default, candles, labels=labels)
    assert [hhmm(t) for t in default.seen] == ["09:15", "09:16", "09:17", "09:18", "09:19"] * 2
    assert {t // 86400 for t in default.seen} == {ist(*MON, 9, 15) // 86400, ist(*WED, 9, 15) // 86400}

    wide = Scripted()
    run(wide, candles, labels=labels, session_types=("normal", "weekend_full", "special_short"))
    assert len(wide.seen) == 15


def test_5b_square_off_closes_at_the_open_of_the_1505_bar_and_cancels_working_orders() -> None:
    candles = full_day(MON, 100, overrides={minute_index(15, 15): (101, 101, 101, 101)})
    plan = {10: [buy()], 11: [sell(type="SL", price=90.0, tag="sl")]}
    res = run(Scripted(plan), candles, square_off="15:15")
    (t,) = res.trades
    assert (t.exit_reason, t.exit_price, t.net_pnl) == ("square_off", 101.0, 1.0)
    assert hhmm(t.exit_time) == "15:15"
    assert [(c["reason"]) for c in kinds(res, "order_cancelled")] == ["square_off"]


def test_5b_entries_that_would_fill_at_or_after_square_off_are_blocked() -> None:
    res = run(Scripted({minute_index(15, 14): [buy()]}), full_day(MON), square_off="15:15")
    assert fills(res) == [] and res.trades == []
    (u,) = kinds(res, "unfilled")
    assert u["reason"] == "after_square_off" and res.counters["unfilled"] == 1


def test_5b_default_square_off_is_1515_and_can_be_switched_off() -> None:
    candles = full_day(MON, 100, overrides={minute_index(15, 15): (101, 101, 101, 101)})
    default = run(Scripted({10: [buy()]}), candles, square_off="default")
    assert default.trades[0].exit_reason == "square_off" and hhmm(default.trades[0].exit_time) == "15:15"
    off = run(Scripted({10: [buy()]}), candles, square_off=None)
    assert off.trades[0].exit_reason == "session_end"


def test_5c_without_square_off_and_without_carry_the_position_ends_with_the_session() -> None:
    candles = full_day(MON, 100, overrides={minute_index(15, 29): (100, 100.5, 99.8, 100.3)})
    res = run(Scripted({10: [buy()]}), candles, square_off=None)
    (t,) = res.trades
    assert (t.exit_reason, t.exit_price, t.net_pnl) == ("session_end", 100.3, 0.3)
    assert hhmm(t.exit_time) == "15:29"


def test_5d_a_strategy_that_allows_it_carries_the_position_overnight() -> None:
    candles = day_bars(MON, flat(100, 5)) + day_bars(TUE, flat(102, 5))
    res = run(Scripted({0: [buy()], 6: [exit_()]}, allow_overnight=True), candles)
    (t,) = res.trades
    assert t.entry_time == ist(*MON, 9, 16) and t.exit_time == ist(*TUE, 9, 17) and t.net_pnl == 2.0
    assert not [e for e in res.events if e.get("reason") == "session_end"]


def test_5e_working_orders_do_not_survive_the_session_unless_the_strategy_carries() -> None:
    candles = day_bars(MON, flat(100, 5)) + day_bars(TUE, [(100, 100, 98.0, 99)] * 5)
    res = run(Scripted({3: [buy(type="LIMIT", price=99.0)]}), candles)
    assert fills(res) == []
    assert [c["reason"] for c in kinds(res, "order_cancelled")] == ["session_end"]
    kept = run(Scripted({3: [buy(type="LIMIT", price=99.0)]}, allow_overnight=True), candles)
    assert [f[:2] for f in fills(kept)][:1] == [("09:15", "BUY")]


def test_5f_an_open_position_at_the_end_of_data_is_closed_and_flagged() -> None:
    res = run(Scripted({0: [buy()]}, allow_overnight=True), day_bars(MON, flat(100, 5)))
    (t,) = res.trades
    assert t.exit_reason == "end_of_data" and res.counters["end_of_data_exits"] == 1


def test_5g_daily_bars_need_a_strategy_that_allows_carrying() -> None:
    candles = day_bars(MON, flat(100, 3)) + day_bars(TUE, flat(101, 3)) + day_bars(WED, flat(102, 3))
    with pytest.raises(ConfigError):
        run(Scripted(), candles, timeframe="1D")
    res = run(Scripted({0: [buy()], 1: [exit_()]}, allow_overnight=True), candles, timeframe="1D")
    (t,) = res.trades
    assert (t.entry_price, t.exit_price, t.net_pnl) == (101.0, 102.0, 1.0)


def test_start_and_end_dates_limit_trading_but_earlier_bars_warm_the_context_up() -> None:
    candles = day_bars(MON, flat(100, 5)) + day_bars(TUE, flat(101, 5)) + day_bars(WED, flat(102, 5))
    lengths: list[int] = []

    def peek(bar, ctx):  # noqa: ANN001, ANN202
        lengths.append(len(ctx.bars))
        return []

    plan = {3: peek, 4: peek}  # the first three calls are Monday's warm-up bars, then Tuesday's
    strat = Scripted(plan)
    run(strat, candles, start="2026-01-06", end="2026-01-06", warmup_bars=3)
    assert len(strat.seen) == 8 and strat.seen[3] == ist(*TUE, 9, 15) and strat.seen[-1] == ist(*TUE, 9, 19)
    assert lengths[0] == 3 + 1  # 3 warm-up bars from Monday + the first tradable bar
