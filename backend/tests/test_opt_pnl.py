"""P&L, costs, slippage, settlement (cases 17–22)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.backtest.costs import load_default_cost_table
from app.options.bs import bs_price, snap_premium
from app.options.model import OptionModelConfig
from tests.opt_helpers import bar, estimate, ist, trade

TBL = load_default_cost_table()


def test_17_worked_example_lot_65_on_the_2026_cost_row() -> None:
    buy = TBL.leg_cost("BUY", 100.5, 65, date(2026, 10, 1))
    sell = TBL.leg_cost("SELL", 119.5, 65, date(2026, 10, 1))
    assert buy.components == {
        "brokerage": Decimal("20.00"), "stt": Decimal("0.00"), "exchange": Decimal("2.32"),
        "sebi": Decimal("0.01"), "stamp": Decimal("0.20"), "gst": Decimal("4.02"),
    }
    assert sell.components == {
        "brokerage": Decimal("20.00"), "stt": Decimal("11.65"), "exchange": Decimal("2.76"),
        "sebi": Decimal("0.01"), "stamp": Decimal("0.00"), "gst": Decimal("4.10"),
    }
    assert (buy.total, sell.total) == (Decimal("26.55"), Decimal("38.52"))
    assert (Decimal("119.5") - Decimal("100.5")) * 65 - buy.total - sell.total == Decimal("1169.93")


def test_18_each_leg_uses_its_own_dated_cost_row() -> None:
    # 30 Mar 2026 (rolled to the 7 Apr weekly) still on the 2026-03-01 row (STT 0.10%); 1 Apr uses 0.15%
    entry, exit_ = ist(2026, 3, 30, 15, 20), ist(2026, 4, 1, 10, 0)
    index = [bar(entry - 60, 25010), bar(entry, 25010), bar(exit_, 25010)]
    vix = [bar(entry, 14), bar(exit_, 14)]
    out = estimate(
        [trade(entry=(entry, 25010), exit=(exit_, 25010))], index, vix,
        config=OptionModelConfig(roll_on_expiry_day=True),
    )
    row = out.option.trades[0]
    assert row["charges_buy"]["stt"] == 0.0
    # sell STT at 0.15% of exit fill × 65, not 0.10%
    sell_stt = TBL.leg_cost("SELL", row["exit_fill"], row["units"], date(2026, 4, 1)).components["stt"]
    old_stt = TBL.leg_cost("SELL", row["exit_fill"], row["units"], date(2026, 3, 31)).components["stt"]
    assert Decimal(str(row["charges_sell"]["stt"])) == sell_stt
    assert sell_stt > old_stt


def test_19_premium_never_goes_below_the_tick_floor() -> None:
    # deep OTM put after a huge rally: model premium snaps to 0.05, fill stays 0.05
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 15, 0)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 28000)]
    vix = [bar(fill, 14), bar(ex, 14)]
    out = estimate(
        [trade(direction="SHORT", entry=(fill, 25010), exit=(ex, 28000))], index, vix,
    )
    row = out.option.trades[0]
    assert row["contract"]["kind"] == "PE"
    assert row["exit_premium"] == 0.05 and row["exit_fill"] == 0.05
    # max loss is the entry outlay plus costs, less the 0.05 residue
    assert row["net_pnl"] == round((0.05 - row["entry_fill"]) * row["units"] - row["charges_total"], 2)


def test_20_a_reversal_is_two_option_trades_with_units_fixed_at_each_entry() -> None:
    a0, a1 = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 11, 0)
    b0, b1 = ist(2026, 10, 5, 11, 0), ist(2026, 10, 5, 12, 0)
    index = [bar(a0 - 60, 25010), bar(a0, 25010), bar(a1, 25060), bar(b1, 25020)]
    vix = [bar(t, 14) for t in (a0, a1, b1)]
    out = estimate([
        trade(tid=1, direction="LONG", entry=(a0, 25010), exit=(a1, 25060), lots=1),
        trade(tid=2, direction="SHORT", entry=(b0, 25060), exit=(b1, 25020), lots=2),
    ], index, vix)
    assert len(out.option.trades) == 2
    assert out.option.trades[0]["contract"]["kind"] == "CE" and out.option.trades[0]["units"] == 65
    assert out.option.trades[1]["contract"]["kind"] == "PE" and out.option.trades[1]["units"] == 130
    assert out.option.trades[1]["lots"] == 2


def test_21_held_past_expiry_settles_at_intrinsic_and_warns() -> None:
    # enter Monday 5 Oct (weekly expires Tue 6), "exit" Wednesday — overlay settles Tue 15:30
    entry = ist(2026, 10, 5, 10, 0)
    fake_exit = ist(2026, 10, 7, 10, 0)
    exp_close = ist(2026, 10, 6, 15, 29)
    index = [bar(entry - 60, 25010), bar(entry, 25010), bar(exp_close, 25100), bar(fake_exit, 25200)]
    vix = [bar(entry, 14)]
    out = estimate([trade(entry=(entry, 25010), exit=(fake_exit, 25200))], index, vix)
    row = out.option.trades[0]
    assert "expired_while_held" in row["flags"]
    assert row["exit_premium"] == snap_premium(max(25100 - 25000, 0))
    assert row["charges_sell"] == {}  # not modelled as a sale
    assert any("STT on exercise" in w for w in out.option.warnings)


def test_22_end_to_end_golden_mon_5_oct_2026() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]
    out = estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix)
    row = out.option.trades[0]
    assert row["contract"]["symbol"] == "NIFTY 25000 CE 06 OCT 26"
    assert row["contract"]["lot_size"] == 65 and row["units"] == 65
    raw_in = bs_price("CE", 25010, 25000, 705 / 93750, 0.14)
    raw_out = bs_price("CE", 25060, 25000, 675 / 93750, 0.14)
    assert (row["entry_premium"], row["exit_premium"]) == (snap_premium(raw_in), snap_premium(raw_out))
    assert (row["entry_premium"], row["exit_premium"]) == (126.15, 151.05)
    assert (row["entry_fill"], row["exit_fill"]) == (126.65, 150.55)
    assert row["gross_pnl"] == 1553.50
    assert row["charges_buy"] == {
        "brokerage": 20.0, "stt": 0.0, "exchange": 2.92, "sebi": 0.01, "stamp": 0.25, "gst": 4.13,
    }
    assert row["charges_sell"] == {
        "brokerage": 20.0, "stt": 14.68, "exchange": 3.48, "sebi": 0.01, "stamp": 0.0, "gst": 4.23,
    }
    assert (row["charges_total"], row["net_pnl"]) == (69.71, 1483.79)


def test_an_averaged_position_is_refused() -> None:
    from app.backtest.contracts import Signal
    from app.options.model import OptionOverlayError, overlay_options
    from tests.bt_helpers import MON, day_bars, run
    from tests.bt_helpers import Scripted, buy

    candles = day_bars(MON, [(100, 101, 99, 100)] * 5)
    result = run(Scripted({0: [buy()], 1: [buy()], 2: [Signal("EXIT", 2)]}), candles)
    assert result.trades[0].entry_fills == 2
    assert result.trades[0].exit_fills == 1
    with pytest.raises(OptionOverlayError, match=r"trade 1 has 2 entry fills and 1 exit fills"):
        overlay_options(result, [], [])


def test_option_pnl_above_the_index_move_is_warned() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]

    def rich(model, **kwargs):
        return (10.0 if kwargs["side"] == "BUY" else 500.0), "model"

    from app.options import model as overlay
    original = overlay._taken_premium
    overlay._taken_premium = rich
    try:
        out = estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix)
    finally:
        overlay._taken_premium = original
    warning = next(item for item in out.option.warnings if "exceeds the index move" in item)
    assert "larger than the index move" in warning
    assert "trade 1" in warning
    quiet = estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix)
    assert not any("exceeds the index move" in item for item in quiet.option.warnings)
    loser = estimate(
        [trade(direction="SHORT", entry=(fill, 25010), exit=(ex, 28000))],
        [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 28000)],
        vix,
    )
    assert not any("exceeds the index move" in item for item in loser.option.warnings)


def test_slippage_is_configurable_and_defaults_to_half_a_point() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]
    zero = estimate(
        [trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix,
        config=OptionModelConfig(slippage_points=0.0),
    )
    row = zero.option.trades[0]
    assert row["entry_fill"] == row["entry_premium"] == 126.15
    assert row["exit_fill"] == row["exit_premium"] == 151.05
