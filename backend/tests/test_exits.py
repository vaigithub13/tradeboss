"""Exit rules shared by the backtest and paper: 1:2 risk/reward on the option premium or on index ATR(14).

Levels, the hit rule (a gap through a level exits at the open; both levels in one minute = the stop), the
incremental ATR (equal to the chart's ta.atr), the strategy wrapper that puts ATR stops on entries, and the trade
report row (exit reason, R multiple) with its tallies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest.contracts import Signal
from app.exits.rules import (
    ATR_1TO2,
    PREMIUM_1TO2,
    AtrState,
    ExitRule,
    WithExits,
    first_hit,
    index_levels,
    parse_exit_rule,
    premium_levels,
)
from app.exits.report import reason_category, reason_counts, report_row
from app.indicators.volatility import atr


# ---------------------------------------------------------------- rules and levels
def test_the_two_one_to_two_rules() -> None:
    assert (PREMIUM_1TO2.kind, PREMIUM_1TO2.stop, PREMIUM_1TO2.target) == ("premium", 0.20, 0.40)
    assert (ATR_1TO2.kind, ATR_1TO2.stop, ATR_1TO2.target, ATR_1TO2.atr_length) == ("atr", 1.0, 2.0, 14)
    assert parse_exit_rule(None) is None
    assert parse_exit_rule({"kind": "premium", "stop": 0.2, "target": 0.4}) == PREMIUM_1TO2
    assert parse_exit_rule("premium_1to2") == PREMIUM_1TO2 and parse_exit_rule("atr_1to2") == ATR_1TO2
    assert parse_exit_rule(PREMIUM_1TO2.to_dict()) == PREMIUM_1TO2
    for bad in ({"kind": "premium", "stop": 1.2, "target": 0.4}, {"kind": "x", "stop": 1, "target": 2},
                {"kind": "atr", "stop": 0, "target": 2}, "nope", 3):
        with pytest.raises(ValueError):
            parse_exit_rule(bad)


def test_premium_levels_are_fractions_of_the_entry_fill() -> None:
    assert premium_levels(126.25, PREMIUM_1TO2) == (101.0, 176.75)
    assert premium_levels(139.5, PREMIUM_1TO2) == (111.6, 195.3)


def test_index_levels_follow_the_direction() -> None:
    assert index_levels("LONG", 22500.0, 30.0, 60.0) == (22470.0, 22560.0)
    assert index_levels("SHORT", 22521.05, 30.0, 60.0) == (22551.05, 22461.05)


def test_first_hit_long_premium() -> None:
    # (stop, target) = (80, 140) on a bought option
    assert first_hit(80, 140, 100, 120, 90, long=True) is None
    assert first_hit(80, 140, 100, 141, 95, long=True) == ("target", 140)
    assert first_hit(80, 140, 100, 101, 79, long=True) == ("stop", 80)
    assert first_hit(80, 140, 100, 150, 70, long=True) == ("stop", 80)  # both in one minute: the stop
    assert first_hit(80, 140, 75, 76, 70, long=True) == ("stop", 75)  # gapped through: the open
    assert first_hit(80, 140, 145, 150, 144, long=True) == ("target", 145)
    assert first_hit(None, 140, 100, 150, 70, long=True) == ("target", 140)


def test_first_hit_short_index() -> None:
    # a SHORT index trade: stop above, target below
    assert first_hit(22551.05, 22461.05, 22520, 22552, 22500, long=False) == ("stop", 22551.05)
    assert first_hit(22551.05, 22461.05, 22520, 22530, 22460, long=False) == ("target", 22461.05)
    assert first_hit(22551.05, 22461.05, 22560, 22570, 22555, long=False) == ("stop", 22560)


# ---------------------------------------------------------------- ATR
def test_the_incremental_atr_equals_the_charts_ta_atr() -> None:
    rng = np.random.RandomState(7)
    close = 22000 + np.cumsum(rng.normal(0, 8, 300))
    high = close + np.abs(rng.normal(0, 5, 300))
    low = close - np.abs(rng.normal(0, 5, 300))
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    expected = atr(df, 14).to_numpy()
    state = AtrState(14)
    got = [state.update(h, lo, c) for h, lo, c in zip(high, low, close)]
    assert all(g is None for g in got[:13])
    assert np.allclose(np.array(got[13:], dtype=float), expected[13:], rtol=0, atol=1e-9)


class Fixed:
    name = "fixed"
    params = {"x": 1}
    pyramiding = 0

    def __init__(self, plan: dict[int, list[Signal]]) -> None:
        self.plan, self.n = plan, -1

    def on_start(self, ctx) -> None:  # noqa: ANN001
        pass

    def on_stop(self, ctx) -> None:  # noqa: ANN001
        pass

    def on_bar(self, bar, ctx):  # noqa: ANN001, ANN201
        self.n += 1
        return list(self.plan.get(self.n, []))


def test_with_exits_puts_atr_stops_on_entries_only_once_atr_exists() -> None:
    bars = [{"time": i, "open": 100.0, "high": 101.0 + i % 3, "low": 99.0, "close": 100.0, "volume": 0} for i in range(20)]
    plan = {5: [Signal("BUY", 1)], 15: [Signal("SELL", 1, "SL", 95.0, tag="SE")], 16: [Signal("EXIT", 1)]}
    wrapped = WithExits(Fixed(plan), ATR_1TO2)
    out = [wrapped.on_bar(b, None) for b in bars]
    assert out[5][0].stop_points is None  # no ATR yet (bar 13 is the first): the entry goes without a bracket
    sig = out[15][0]
    ref = AtrState(14)
    for b in bars[:16]:
        a = ref.update(b["high"], b["low"], b["close"])
    assert a is not None and sig.stop_points == pytest.approx(a) and sig.target_points == pytest.approx(2 * a)
    assert (sig.side, sig.type, sig.price, sig.tag) == ("SELL", "SL", 95.0, "SE")
    assert out[16][0].stop_points is None  # an EXIT carries no bracket
    assert wrapped.name == "fixed" and wrapped.pyramiding == 0
    assert wrapped.params == {"x": 1, "exit_rule": ATR_1TO2.to_dict()}
    assert wrapped.warnings_for_unarmed == 1


def test_a_premium_rule_does_not_touch_the_signals() -> None:
    wrapped = WithExits(Fixed({0: [Signal("BUY", 1)]}), PREMIUM_1TO2)
    (sig,) = wrapped.on_bar({"time": 0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}, None)
    assert sig.stop_points is None and sig.target_points is None


# ---------------------------------------------------------------- the trade report
def test_reason_categories() -> None:
    for raw, cat in (("premium_target", "target"), ("index_target", "target"), ("target", "target"),
                     ("premium_stop", "stop"), ("index_stop", "stop"), ("stop", "stopped by user"),
                     ("square_off", "square-off"), ("reverse", "reversal"), ("signal", "reversal"),
                     ("reversal", "reversal"), ("session_end", "session end"), ("exit", "exit signal")):
        assert reason_category(raw) == cat, raw


def test_report_row_r_multiple_is_net_over_the_planned_risk() -> None:
    row = report_row(
        signal_time=1, trigger_index=22521.05, fill_time=2, contract="NIFTY 22500 PE 13 OCT 26",
        entry_premium=126.25, entry_source="real", units=65, rule=PREMIUM_1TO2, direction="SHORT",
        index_entry=22520.45, delta=-0.5, exit_time=3, exit_premium=176.75, exit_reason="premium_target", net=3200.0,
    )
    assert (row["premium_stop"], row["premium_target"]) == (101.0, 176.75)
    assert row["risk"] == pytest.approx((126.25 - 101.0) * 65)
    assert row["r_multiple"] == pytest.approx(3200.0 / ((126.25 - 101.0) * 65), abs=1e-3)
    # index levels are estimated through the delta: a put gains when the index falls
    assert row["index_stop"] == pytest.approx(22520.45 + 25.25 / 0.5, abs=0.01)
    assert row["index_target"] == pytest.approx(22520.45 - 50.5 / 0.5, abs=0.01)
    assert row["estimated"] == ["index"] and row["exit_reason"] == "target"


def test_report_row_for_an_atr_trade_estimates_the_premium_levels() -> None:
    row = report_row(
        signal_time=1, trigger_index=22500.0, fill_time=2, contract="NIFTY 22500 CE 13 OCT 26",
        entry_premium=120.0, entry_source="real", units=65, rule=ATR_1TO2, direction="LONG",
        index_entry=22500.0, delta=0.5, index_stop=22470.0, index_target=22560.0,
        exit_time=3, exit_premium=105.0, exit_reason="stop", net=-1100.0,
    )
    assert (row["premium_stop"], row["premium_target"]) == (105.0, 150.0)
    assert row["risk"] == pytest.approx(15.0 * 65) and row["estimated"] == ["premium"]
    assert row["r_multiple"] == pytest.approx(-1100.0 / 975.0, abs=1e-3)


def test_report_row_without_a_rule_has_no_levels_and_no_r() -> None:
    row = report_row(
        signal_time=1, trigger_index=22500.0, fill_time=2, contract="c", entry_premium=100.0, entry_source="modelled",
        units=65, rule=None, direction="LONG", index_entry=22500.0, delta=None,
        exit_time=3, exit_premium=90.0, exit_reason="square_off", net=-700.0,
    )
    assert row["risk"] is None and row["r_multiple"] is None and row["premium_stop"] is None
    assert row["exit_reason"] == "square-off" and row["entry_source"] == "modelled"


def test_reason_counts() -> None:
    rows = [{"exit_reason": r} for r in ("target", "stop", "stop", "reversal", "square-off", "square-off", "square-off")]
    assert reason_counts(rows) == {"target": 1, "stop": 2, "reversal": 1, "square-off": 3, "other": 0}
    assert reason_counts([{"exit_reason": "session end"}, {"exit_reason": "stopped by user"}])["other"] == 2
