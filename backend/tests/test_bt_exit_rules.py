"""The 1:2 exit rules in the backtest: the run setting, the ATR bracket on the index, the premium exit on the
option overlay, and the trade report with its exit tallies."""

from __future__ import annotations

import pytest

from app.backtest.catalog import RunRequestError, build_strategy, parse_config
from app.backtest.contracts import Signal
from app.backtest.execute import _model, _report_rows
from app.backtest.walkforward import child_backtest_config, parse_walk_forward
from app.exits.rules import ATR_1TO2, PREMIUM_1TO2, WithExits
from tests.bt_helpers import MON, Scripted, fills, full_day, hhmm, kinds, minute_index, run

BASE = {"strategy": "log_xz", "symbol": "NIFTY50", "timeframe": "5m", "start": "2024-10-03", "end": "2026-06-30",
        "mode": "options", "slippage_points": 0.2, "sessions": ["normal", "weekend_full"]}


# ---------------------------------------------------------------- the run setting
def test_exit_rule_is_a_run_setting_for_runs_and_walk_forwards() -> None:
    assert parse_config(BASE)["exit_rule"] is None
    cfg = parse_config({**BASE, "exit_rule": "atr_1to2"})
    assert cfg["exit_rule"] == ATR_1TO2.to_dict()
    s = build_strategy(cfg)
    assert isinstance(s, WithExits) and s.rule == ATR_1TO2 and s.name == "log_xz"
    prem = parse_config({**BASE, "exit_rule": {"kind": "premium", "stop": 0.2, "target": 0.4}})
    assert prem["exit_rule"] == PREMIUM_1TO2.to_dict()
    model = _model(prem)
    assert (model.premium_stop_pct, model.premium_target_pct) == (0.2, 0.4)
    with pytest.raises(RunRequestError, match="exit"):
        parse_config({**BASE, "exit_rule": "nope"})
    with pytest.raises(RunRequestError, match="one exit"):
        parse_config({**BASE, "exit_rule": "premium_1to2", "premium_stop_pct": 0.3})
    with pytest.raises(RunRequestError, match="one exit"):
        parse_config({**BASE, "exit_rule": "atr_1to2", "params": {"use_stop": True}})
    with pytest.raises(RunRequestError, match="options"):
        parse_config({**BASE, "mode": "index", "exit_rule": "premium_1to2"})
    wf = parse_walk_forward({**BASE, "kind": "walk_forward", "exit_rule": "atr_1to2"})
    assert wf["exit_rule"] == ATR_1TO2.to_dict()
    from datetime import date

    child = child_backtest_config({**wf, "strike_offset": 0}, {"z_length": 10}, date(2025, 1, 1), date(2025, 2, 1))
    assert child["exit_rule"] == ATR_1TO2.to_dict()


# ---------------------------------------------------------------- the ATR bracket on the index
def _flat(overrides: dict) -> list:
    return full_day(MON, 100.0, overrides={minute_index(h, m): r for (h, m), r in overrides.items()})


def test_an_atr_bracket_stop_closes_the_index_position_and_the_report_says_stop() -> None:
    # a BUY at bar 1 (09:20 bar) with a 2-point stop and 4-point target
    plan = {1: [Signal("BUY", 1, stop_points=2.0, target_points=4.0)]}
    res = run(Scripted(plan), _flat({(9, 27): (100, 100.5, 97.5, 98)}), timeframe="5m", live_timing=True)
    (t,) = res.trades
    assert hhmm(t.entry_time) == "09:26" and (t.stop_level, t.target_level) == (98.0, 104.0)
    assert t.exit_tag.endswith(":stop") and t.exit_price == 98.0 and hhmm(t.exit_time) == "09:27"
    assert t.signal_time is not None and hhmm(t.signal_time) == "09:25"  # the bar that placed it ended 09:25


def test_a_reversal_cancels_the_old_brackets() -> None:
    """A long's bracket must not survive a reversal and later close a new long at the old level."""
    plan = {
        1: [Signal("BUY", 1, stop_points=3.0, target_points=50.0)],  # long at 09:26 @100: stop 97
        3: [Signal("SELL", 1)],  # reverse at 09:36
        5: [Signal("BUY", 1)],  # long again at 09:46, no bracket
    }
    rows = {(9, 50): (100, 100, 96, 99)}  # touches the first long's old stop (97)
    strat = Scripted(plan)
    strat.pyramiding = 0  # stop-and-reverse, as Price Channel and Log XZ
    res = run(strat, _flat(rows), timeframe="5m", live_timing=True, square_off="default")
    assert [hhmm(t.entry_time) for t in res.trades] == ["09:26", "09:36", "09:46"]
    assert res.trades[2].exit_reason == "square_off"  # not stopped out at 97 by the first long's stop
    assert any(e["kind"] == "order_cancelled" and e.get("reason") == "reversed" for e in res.events)


def test_signal_time_is_the_placing_bars_end_for_a_stop_entry_too() -> None:
    plan = {1: [Signal("BUY", 1, "SL", 101.0, tag="LE")]}
    res = run(Scripted(plan), _flat({(9, 33): (100, 101.5, 100, 101)}), timeframe="5m", live_timing=True)
    (t,) = res.trades
    assert hhmm(t.entry_time) == "09:33" and hhmm(t.signal_time) == "09:25"


# ---------------------------------------------------------------- the trade report
def test_report_rows_map_exit_reasons_and_count_them() -> None:
    from app.backtest.result import Trade

    def trade(tid: int, exit_reason: str, exit_tag: str) -> Trade:
        return Trade(id=tid, direction="LONG", entry_time=1000 + tid, entry_price=22500.0, exit_time=2000 + tid,
                     exit_price=22510.0, lots=1, units=65, lot_size=65, gross_pnl=0, charges={}, charges_total=0,
                     slippage_cost=0, net_pnl=0, exit_reason=exit_reason, entry_tag="LE", exit_tag=exit_tag,
                     gap=False, ambiguous=False, optimistic=False, signal_time=940 + tid, stop_level=22470.0,
                     target_level=22560.0)

    trades = [trade(1, "stop", "LE:stop"), trade(2, "limit", "LE:target"), trade(3, "square_off", "square_off"),
              trade(4, "market", "SE"), trade(5, "stop", "SE")]
    option = {t.id: {"index_trade_id": t.id, "contract": {"symbol": "NIFTY 22500 CE 13 OCT 26"}, "entry_fill": 120.0,
                     "entry_source": "real", "units": 65, "entry_delta": 0.5, "exit_time": t.exit_time,
                     "exit_fill": 110.0, "exit_reason": "index", "net_pnl": -700.0, "index_entry": 22500.0}
              for t in trades}
    option[1]["exit_reason"] = "premium_stop"
    rows, counts = _report_rows(trades, option, rule=ATR_1TO2, step=300)
    assert [r["exit_reason"] for r in rows] == ["stop", "target", "square-off", "reversal", "reversal"]
    assert rows[0]["exit_reason_raw"] == "premium_stop"
    assert rows[0]["signal_time"] == 941 - 300 and rows[0]["index_stop"] == 22470.0
    assert rows[0]["premium_stop"] == 105.0 and rows[0]["r_multiple"] == pytest.approx(-700 / 975, abs=1e-3)
    assert counts == {"target": 1, "stop": 1, "reversal": 2, "square-off": 1, "other": 0}
