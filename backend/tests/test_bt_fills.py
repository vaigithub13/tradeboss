"""Fill timing (2) and gaps (4). Spec: Phase 3a test cases approved in chat."""

from __future__ import annotations

import pytest

from app.backtest.costs import Slippage
from tests.bt_helpers import (
    MON,
    TUE,
    Scripted,
    buy,
    day_bars,
    exit_,
    fills,
    flat,
    hhmm,
    ist,
    kinds,
    run,
    sell,
)

# six bars; every open differs from the previous close so a wrong fill price is visible
SIX = [
    (100, 101, 99, 100.5),
    (101, 102, 100, 101.5),
    (102, 103, 101, 102.5),
    (103, 104, 102, 103.5),
    (104, 105, 103, 104.5),
    (105, 106, 104, 105.5),
]


# ---------------------------------------------------------------- 2. fill timing
def test_2a_signal_at_bar_close_fills_at_the_next_bar_open() -> None:
    res = run(Scripted({2: [buy()], 4: [exit_()]}), day_bars(MON, SIX))
    assert fills(res) == [("09:18", "BUY", 103.0), ("09:20", "SELL", 105.0)]
    (t,) = res.trades
    assert (t.entry_price, t.exit_price, t.net_pnl) == (103.0, 105.0, 2.0)
    # the decision is stamped with the bar END (09:17 bar ends 09:18)
    sig = kinds(res, "signal")[0]
    assert hhmm(sig["t"]) == "09:18" and sig["side"] == "BUY"
    assert res.optimistic is False


def test_2b_a_signal_on_the_last_bar_of_a_session_does_not_fill_and_is_not_carried() -> None:
    candles = day_bars(MON, SIX[:5]) + day_bars(TUE, SIX[:5])
    res = run(Scripted({4: [buy()]}), candles)
    assert fills(res) == [] and res.trades == []
    (u,) = kinds(res, "unfilled")
    assert u["reason"] == "no_next_bar"
    assert res.counters["unfilled"] == 1


def test_2c_same_bar_close_is_explicit_and_flagged_optimistic() -> None:
    res = run(Scripted({2: [buy()]}), day_bars(MON, SIX), fill_mode="same_bar_close")
    assert fills(res)[0] == ("09:17", "BUY", 102.5)  # the signal bar's CLOSE
    assert res.optimistic is True
    assert any("same_bar_close" in w for w in res.warnings)
    default = run(Scripted({2: [buy()]}), day_bars(MON, SIX))
    assert default.optimistic is False and not any("same_bar_close" in w for w in default.warnings)


def test_2d_limit_fills_only_when_price_trades_strictly_through_and_never_on_the_placing_bar() -> None:
    rows = [
        (100, 100.5, 99.5, 100),
        (100, 100.5, 98.0, 100),  # signal bar: trades through 99 but the order does not exist yet
        (100, 101, 99.0, 100),  # touches 99 exactly: NOT through -> no fill
        (100, 100, 98.9, 99.5),  # through -> fills at the limit price
        (100, 100, 100, 100),
    ]
    res = run(Scripted({1: [buy(type="LIMIT", price=99.0)]}), day_bars(MON, rows))
    buys = [f for f in fills(res) if f[1] == "BUY"]
    assert buys == [("09:18", "BUY", 99.0)]


def test_2d_limit_gapped_through_fills_at_the_better_open() -> None:
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (98, 99.5, 97.5, 99), (99, 99, 99, 99)]
    res = run(Scripted({1: [buy(type="LIMIT", price=99.0)]}), day_bars(MON, rows))
    assert fills(res)[0] == ("09:17", "BUY", 98.0)


def test_2d_stop_fills_on_touch() -> None:
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (104, 105.0, 103.5, 104.5), (104, 104, 104, 104)]
    res = run(Scripted({1: [buy(type="SL", price=105.0)]}), day_bars(MON, rows))
    assert fills(res)[0] == ("09:17", "BUY", 105.0)


def test_2d_an_order_placed_on_a_bar_ignores_that_bars_own_range() -> None:
    rows = [(100, 100, 100, 100), (100, 100, 98.0, 100), (100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100)]
    res = run(Scripted({1: [buy(type="LIMIT", price=99.0)]}), day_bars(MON, rows))
    assert fills(res) == []


def test_2e_cancel_removes_a_working_order() -> None:
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 98.0, 99), (99, 99, 99, 99)]
    plan = {
        1: [buy(type="LIMIT", price=99.0, tag="x")],
        2: lambda bar, ctx: (ctx.cancel_working(tag="x"), [])[1],
    }
    res = run(Scripted(plan), day_bars(MON, rows))
    assert fills(res) == []
    (c,) = kinds(res, "order_cancelled")
    assert c["reason"] == "cancelled" and hhmm(c["t"]) == "09:18"


def test_2f_slippage_is_adverse_on_market_fills_only() -> None:
    res = run(Scripted({2: [buy()], 4: [exit_()]}), day_bars(MON, SIX), slippage=Slippage.points(0.5))
    assert [f[2] for f in fills(res)] == [103.5, 104.5]
    (t,) = res.trades
    assert t.gross_pnl == 1.0 and t.slippage_cost == 1.0  # 0.5 per leg, 1 unit
    limit = run(Scripted({1: [buy(type="LIMIT", price=99.0)]}), day_bars(MON, [SIX[0], SIX[1], (100, 100, 98.0, 99), SIX[3]]),
                slippage=Slippage.points(0.5))
    assert fills(limit)[0][2] == 99.0  # limits get no slippage


def test_2f_percentage_slippage() -> None:
    res = run(Scripted({2: [buy()], 4: [exit_()]}), day_bars(MON, SIX), slippage=Slippage.pct(0.1))
    assert [round(f[2], 4) for f in fills(res)] == [103.103, 104.895]


# ---------------------------------------------------------------- 4. gaps
def test_4a_stop_gapped_through_fills_at_the_open_not_the_stop_price() -> None:
    rows = [(100, 100, 100, 100), (100, 101, 99.5, 100.5), (95, 96, 94, 95.5), (95.5, 96, 95, 95.5)]
    res = run(Scripted({0: [buy()], 1: [sell(type="SL", price=98.0, tag="sl")]}), day_bars(MON, rows))
    assert fills(res) == [("09:16", "BUY", 100.0), ("09:17", "SELL", 95.0)]
    (t,) = res.trades
    assert t.exit_reason == "stop" and t.gap is True and t.net_pnl == -5.0
    assert res.counters["gaps"] == 1


def test_4a_gap_that_opens_inside_a_higher_timeframe_bar() -> None:
    # three 5m bars of 1m data; the 3rd minute-of-bar-3 opens at 95 after a 100.8 close
    rows = [(101, 101, 101, 101)] * 10 + [
        (101, 101, 100.5, 101),
        (101, 101, 100.2, 100.8),
        (95, 96, 94.5, 95.5),
        (95.5, 96, 95, 95.5),
        (95.5, 96, 95, 95.5),
    ]
    res = run(Scripted({0: [buy()], 1: [sell(type="SL", price=100.0)]}), day_bars(MON, rows), timeframe="5m")
    assert fills(res) == [("09:20", "BUY", 101.0), ("09:27", "SELL", 95.0)]
    assert res.trades[0].gap is True and res.trades[0].net_pnl == -6.0


def test_4b_overnight_gap_on_a_carried_position_fills_at_the_next_sessions_open() -> None:
    day1 = day_bars(MON, flat(100, 5))
    day2 = day_bars(TUE, [(90, 91, 89, 90)] * 3)
    res = run(Scripted({0: [buy()], 1: [sell(type="SL", price=98.0)]}, allow_overnight=True), day1 + day2)
    (t,) = res.trades
    assert t.exit_time == ist(*TUE, 9, 15) and t.exit_price == 90.0 and t.gap is True and t.net_pnl == -10.0


def test_4c_short_stop_gapped_up_fills_at_the_open() -> None:
    rows = [(100, 100, 100, 100), (100, 100.5, 99.5, 100), (105, 106, 104, 105.5), (105.5, 106, 105, 105.5)]
    res = run(Scripted({0: [sell()], 1: [buy(type="SL", price=102.0)]}), day_bars(MON, rows))
    (t,) = res.trades
    assert t.direction == "SHORT" and t.exit_price == 105.0 and t.net_pnl == -5.0 and t.gap is True


def test_a_stop_touched_inside_the_bar_is_not_priced_as_the_open() -> None:
    rows = [(100, 100, 100, 100), (100, 103, 99, 101), (101, 101, 101, 101)]
    res = run(Scripted({0: [buy(type="SL", price=102.0)]}), day_bars(MON, rows))
    (t,) = res.trades
    assert t.entry_price == 102.0 and t.entry_at_open is False and t.gap is False


def test_4d_stop_entry_gapped_through_fills_at_the_open() -> None:
    rows = [(100, 100, 100, 100), (108, 109, 107, 108.5), (108.5, 109, 108, 108.5)]
    res = run(Scripted({0: [buy(type="SL", price=105.0)]}), day_bars(MON, rows))
    assert fills(res)[0] == ("09:16", "BUY", 108.0)
    assert kinds(res, "fill")[0]["gap"] is True
    assert res.trades[0].entry_at_open is True


def test_4e_limit_target_gapped_up_fills_at_the_better_open() -> None:
    rows = [(100, 100, 100, 100), (100, 101, 99.5, 100.5), (115, 116, 114, 115.5), (115.5, 116, 115, 115.5)]
    res = run(Scripted({0: [buy()], 1: [sell(type="LIMIT", price=110.0)]}), day_bars(MON, rows))
    (t,) = res.trades
    assert t.exit_price == 115.0 and t.net_pnl == 15.0 and t.exit_reason == "limit" and t.gap is True


def test_4f_slippage_is_applied_after_the_gap_open() -> None:
    rows = [(100, 100, 100, 100), (100, 101, 99.5, 100.5), (95, 96, 94, 95.5), (95.5, 96, 95, 95.5)]
    res = run(Scripted({0: [buy()], 1: [sell(type="SL", price=98.0)]}), day_bars(MON, rows), slippage=Slippage.points(0.5))
    assert [f[2] for f in fills(res)] == [100.5, 94.5]
    assert res.trades[0].net_pnl == -6.0


@pytest.mark.parametrize("bad", [{"timeframe": "7m"}, {"fill_mode": "whenever"}, {"square_off": "25:61"}])
def test_config_is_validated(bad: dict[str, str]) -> None:
    from app.backtest.engine import ConfigError

    with pytest.raises(ConfigError):
        run(Scripted(), day_bars(MON, SIX), **bad)
