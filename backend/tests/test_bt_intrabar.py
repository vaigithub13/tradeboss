"""Intrabar ordering (3): which of SL / target was hit first inside a higher-timeframe bar."""

from __future__ import annotations

from app.backtest.contracts import Signal
from tests.bt_helpers import MON, OHLC, Scripted, day_bars, fills, flat, hhmm, ist, run

FLAT: OHLC = (100.0, 100.0, 100.0, 100.0)


def bar1(overrides: dict[int, OHLC], n: int = 15) -> list[OHLC]:
    rows = [FLAT] * n
    for i, r in overrides.items():
        rows[i] = r
    return rows


def bracket(side: str = "BUY", stop: float = 98.0, target: float = 104.0) -> Scripted:
    return Scripted({0: [Signal(side, 1, stop=stop, target=target)]})  # type: ignore[arg-type]


def go(path: dict[int, OHLC], **kw):  # noqa: ANN201
    return run(bracket(**kw.pop("sig", {})), day_bars(MON, flat(100, 15) + bar1(path)), timeframe="15m")


def test_3a_stop_first_then_target_in_the_same_15m_bar() -> None:
    # entry at 09:30 (open of the next 15m bar); the 1m path hits the stop at minute 4, the target at minute 9
    res = go({4: (100, 100.5, 97.9, 98.5), 9: (102, 104.5, 102, 103)})
    (t,) = res.trades
    assert (t.entry_price, t.exit_price, t.net_pnl, t.exit_reason) == (100.0, 98.0, -2.0, "stop")
    assert hhmm(t.exit_time) == "09:34"
    assert t.ambiguous is False and res.counters["ambiguous"] == 0


def test_3a_target_first_then_stop() -> None:
    res = go({4: (100, 104.2, 100, 104), 9: (103, 103, 97.5, 98)})
    (t,) = res.trades
    assert (t.exit_price, t.net_pnl, t.exit_reason) == (104.0, 4.0, "limit")
    assert hhmm(t.exit_time) == "09:34" and t.ambiguous is False


def test_3b_both_touched_inside_the_same_1m_bar_assumes_the_stop_and_counts_it() -> None:
    res = go({6: (100, 104.5, 97.5, 101)})
    (t,) = res.trades
    assert (t.exit_price, t.exit_reason) == (98.0, "stop")
    assert t.ambiguous is True and res.counters["ambiguous"] == 1


def test_3c_five_minute_source_resolves_what_it_can_and_flags_the_rest() -> None:
    # tf 15m from 5m bars: three sub-bars per 15m bar
    def five(sub: list[OHLC]):  # noqa: ANN202
        return day_bars(MON, [FLAT] * 3 + sub, step_min=5)

    both = run(bracket(), five([FLAT, (100, 104.5, 97.5, 100), FLAT]), base_minutes=5, timeframe="15m")
    assert both.trades[0].exit_price == 98.0 and both.trades[0].ambiguous is True
    assert any("5m" in w for w in both.warnings)  # the source resolution is reported

    resolved = run(bracket(), five([FLAT, (100, 104.5, 99, 101), (101, 101, 97.5, 98)]), base_minutes=5, timeframe="15m")
    assert resolved.trades[0].exit_price == 104.0 and resolved.trades[0].ambiguous is False


def test_3c_source_as_coarse_as_the_timeframe() -> None:
    def fifteen(b1: OHLC):  # noqa: ANN202
        return day_bars(MON, [FLAT, b1], step_min=15)

    both = run(bracket(), fifteen((100, 104.5, 97.5, 101)), base_minutes=15, timeframe="15m")
    assert both.trades[0].exit_price == 98.0 and both.trades[0].ambiguous is True
    only_target = run(bracket(), fifteen((100, 104.5, 99, 101)), base_minutes=15, timeframe="15m")
    assert only_target.trades[0].exit_price == 104.0 and only_target.trades[0].ambiguous is False
    only_stop = run(bracket(), fifteen((100, 101, 97.5, 99)), base_minutes=15, timeframe="15m")
    assert only_stop.trades[0].exit_price == 98.0 and only_stop.trades[0].ambiguous is False


def test_3c_missing_one_minute_inside_the_bar_falls_back_to_the_whole_bar() -> None:
    rows = day_bars(MON, flat(100, 15) + bar1({4: (100, 104.5, 100, 104), 9: (103, 103, 97.5, 98)}))
    del rows[15 + 7]  # the 09:37 minute is missing
    res = run(bracket(), rows, timeframe="15m")
    (t,) = res.trades
    assert (t.exit_price, t.ambiguous) == (98.0, True)  # the 1m path would have said "target first"
    assert any("coarse" in w for w in res.warnings)


def test_3d_short_side_is_mirrored() -> None:
    tgt = go({4: (100, 100.1, 95.8, 96)}, sig={"side": "SELL", "stop": 102.0, "target": 96.0})
    (t,) = tgt.trades
    assert (t.direction, t.exit_price, t.net_pnl, t.exit_reason) == ("SHORT", 96.0, 4.0, "limit")
    stp = go({4: (100, 102.2, 100, 101)}, sig={"side": "SELL", "stop": 102.0, "target": 96.0})
    assert (stp.trades[0].exit_price, stp.trades[0].net_pnl, stp.trades[0].exit_reason) == (102.0, -2.0, "stop")
    amb = go({6: (100, 102.5, 95.5, 99)}, sig={"side": "SELL", "stop": 102.0, "target": 96.0})
    assert amb.trades[0].exit_price == 102.0 and amb.trades[0].ambiguous is True


def test_3e_the_entry_bar_counts_and_stops_trigger_on_touch_limits_only_when_through() -> None:
    touch = go({4: (100, 100, 98.0, 99)})  # low == stop exactly: touched
    assert touch.trades[0].exit_price == 98.0
    no_target = go({4: (100, 104.0, 100, 102), 9: (102, 102, 97.9, 98)})  # high == target exactly: not through
    assert no_target.trades[0].exit_price == 98.0 and no_target.trades[0].exit_reason == "stop"
    assert no_target.trades[0].ambiguous is False


def test_3f_bracket_children_are_a_pair_the_other_leg_is_cancelled_at_the_fill() -> None:
    res = go({4: (100, 104.2, 100, 104)})
    cancelled = [e for e in res.events if e["kind"] == "order_cancelled"]
    assert [c["reason"] for c in cancelled] == ["oco"] and hhmm(cancelled[0]["t"]) == "09:34"
    assert [f[1] for f in fills(res)] == ["BUY", "SELL"]


def test_bracket_levels_on_the_wrong_side_are_rejected() -> None:
    res = run(Scripted({0: [Signal("BUY", 1, stop=101.0, target=99.0)]}), day_bars(MON, flat(100, 20)))
    assert fills(res) == [] and res.trades == []
    assert any(e["kind"] == "order_rejected" and e["reason"] == "bad_bracket" for e in res.events)


def test_a_quiet_bar_triggers_nothing_and_the_open_position_is_closed_at_the_end_of_data() -> None:
    res = go({})
    assert res.counters["ambiguous"] == 0
    t = res.trades[0]
    assert t.exit_reason == "end_of_data" and ist(*MON, 9, 44) == t.exit_time
