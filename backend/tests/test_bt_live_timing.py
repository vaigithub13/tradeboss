"""Live timing: the backtest acts when paper does, one minute after the bar closes.

Paper decides a bar when its last minute is exchange-final, about a minute after the bar ends. With `live_timing`
(the default) a market signal fills at the open of the minute after the bar ends, a stop works from that minute,
and a stop level the index touched in that minute fills at that minute's open, flagged `late`. A cancel by the
strategy also takes effect then, so the level it replaces still works through the lag minute.
`live_timing=False` is the old timing: everything works from the bar's end.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.contracts import Signal
from app.backtest.engine import BacktestConfig
from tests.bt_helpers import MON, OHLC, Scripted, buy, fills, full_day, hhmm, kinds, minute_index, run


def day(overrides: dict[tuple[int, int], OHLC]) -> list:
    return full_day(MON, 100.0, overrides={minute_index(h, m): r for (h, m), r in overrides.items()})


def go(plan: dict, overrides: dict[tuple[int, int], OHLC], **kw):  # noqa: ANN201
    return run(Scripted(plan), day(overrides), timeframe="5m", **{"live_timing": True, **kw})


def stop(side: str, price: float, tag: str = "LE") -> Signal:
    return Signal(side, 1, "SL", price, tag=tag)  # type: ignore[arg-type]


# bar 1 is 09:20-09:24 and ends 09:25; the lag minute is 09:25, live timing acts from 09:26
STEPS = {(9, 25): (101, 101, 101, 101), (9, 26): (102, 102, 102, 102)}


def test_live_timing_is_on_by_default_and_in_the_config() -> None:
    assert BacktestConfig().live_timing is True
    res = go({1: [buy()]}, STEPS)
    assert res.config["live_timing"] is True
    off = go({1: [buy()]}, STEPS, live_timing=False)
    assert off.config["live_timing"] is False
    assert res.run_id != off.run_id


def test_a_market_signal_fills_at_the_open_of_the_minute_after_the_bar_closes() -> None:
    res = go({1: [buy()]}, STEPS)
    assert fills(res)[0] == ("09:26", "BUY", 102.0)
    assert hhmm(res.trades[0].entry_time) == "09:26"
    assert res.trades[0].late is False


def test_old_timing_fills_at_the_next_bar_open() -> None:
    res = go({1: [buy()]}, STEPS, live_timing=False)
    assert fills(res)[0] == ("09:25", "BUY", 101.0)


def test_a_stop_not_touched_in_the_lag_minute_fills_at_its_level_when_crossed() -> None:
    res = go({1: [stop("BUY", 105.0)]}, {(9, 28): (100, 106, 100, 105.5)})
    assert fills(res)[0] == ("09:28", "BUY", 105.0)
    assert res.trades[0].late is False and res.counters.get("late_fills", 0) == 0


def test_a_stop_touched_in_the_lag_minute_fills_late_at_the_next_minute_open() -> None:
    # the index crossed 105 at 09:25, before paper knew the level, and is back at 103 at 09:26
    res = go({1: [stop("BUY", 105.0)]}, {(9, 25): (100, 106, 100, 104), (9, 26): (103, 103.5, 102.5, 103)})
    assert fills(res)[0] == ("09:26", "BUY", 103.0)
    (t,) = res.trades
    assert t.late is True and t.entry_at_open is True
    assert res.counters["late_fills"] == 1
    (f,) = [e for e in kinds(res, "fill") if e["side"] == "BUY"]
    assert f["late"] is True


def test_old_timing_fills_a_lag_minute_cross_at_the_level() -> None:
    res = go({1: [stop("BUY", 105.0)]}, {(9, 25): (100, 106, 100, 104), (9, 26): (103, 103.5, 102.5, 103)},
             live_timing=False)
    assert fills(res)[0] == ("09:25", "BUY", 105.0)
    assert res.trades[0].late is False


def test_a_gap_through_the_level_at_the_activation_minute_fills_at_the_open_not_late() -> None:
    res = go({1: [stop("BUY", 105.0)]}, {(9, 26): (107, 108, 106.5, 107)})
    assert fills(res)[0] == ("09:26", "BUY", 107.0)
    assert res.trades[0].late is False and res.trades[0].gap is True


def test_a_replaced_stop_keeps_working_through_the_lag_minute() -> None:
    def rearm(_bar, ctx):  # noqa: ANN001, ANN202
        ctx.cancel_working("LE")
        return [stop("BUY", 110.0)]

    # bar 1 arms 105; bar 2 (09:25-09:29, ends 09:30) replaces it with 110. 09:30 is the lag minute.
    path = {(9, 30): (100, 106, 100, 104)}
    live = go({1: [stop("BUY", 105.0)], 2: rearm}, path)
    assert fills(live)[0] == ("09:30", "BUY", 105.0)
    old = go({1: [stop("BUY", 105.0)], 2: rearm}, path, live_timing=False)
    assert fills(old) == []  # the old level was gone at 09:30 and 110 was never reached


def test_the_replacing_stop_works_from_the_minute_after_the_lag() -> None:
    def rearm(_bar, ctx):  # noqa: ANN001, ANN202
        ctx.cancel_working("LE")
        return [stop("BUY", 103.0)]

    live = go({1: [stop("BUY", 105.0)], 2: rearm}, {(9, 31): (100, 103.5, 100, 103)})
    assert fills(live)[0] == ("09:31", "BUY", 103.0)


def test_a_market_fill_that_would_land_at_the_square_off_is_blocked() -> None:
    # 1m bars: the 15:13 bar ends 15:14, so the fill minute is 15:15, the square-off
    def at_1513(bar, _ctx):  # noqa: ANN001, ANN202
        return [buy()] if hhmm(bar["time"]) == "15:13" else []

    class EveryBar(Scripted):
        def on_bar(self, bar, ctx):  # noqa: ANN001, ANN201
            return at_1513(bar, ctx)

    live = run(EveryBar(), day({}), timeframe="1m", square_off="default", live_timing=True)
    assert fills(live) == []
    assert [u["reason"] for u in kinds(live, "unfilled")] == ["after_square_off"]
    old = run(EveryBar(), day({}), timeframe="1m", square_off="default", live_timing=False)
    assert fills(old)[0][:2] == ("15:14", "BUY")


def test_a_five_minute_source_cannot_show_the_lag_minute_so_live_timing_is_off_and_said() -> None:
    from tests.bt_helpers import day_bars

    bars = day_bars(MON, [(100, 100, 100, 100), (101, 101, 101, 101), (102, 102, 102, 102)], step_min=5)
    res = run(Scripted({0: [buy()]}), bars, base_minutes=5, timeframe="5m", live_timing=True)
    assert fills(res)[0] == ("09:20", "BUY", 101.0)
    assert any("live timing needs 1m source bars" in w for w in res.warnings)


def test_same_bar_close_is_not_delayed() -> None:
    res = go({1: [buy()]}, STEPS, fill_mode="same_bar_close")
    assert fills(res)[0] == ("09:24", "BUY", 100.0)


# ---------------------------------------------------------------- 8 Oct 2026, the stored candles
def test_8_oct_log_xz_fills_at_09_26_as_paper_did() -> None:
    from app.backtest.catalog import build_strategy
    from app.backtest.engine import run_backtest
    from app.backtest.sources import StoreSource
    from app.config import settings
    from app.data.store import CandleStore

    if not (settings.candles_dir / "NIFTY50" / "1m.parquet").exists():
        pytest.skip("no stored NIFTY 1m candles in data/")
    store = CandleStore(settings.candles_dir)
    minutes, _ = store.load("NIFTY50", from_time=1791431100, to_time=1791453600)
    if len(minutes) < 375:
        pytest.skip("8 Oct 2026 is not stored")
    open_at = {int(m["time"]): float(m["open"]) for m in minutes}

    def first_trade(live: bool):  # noqa: ANN202
        cfg = BacktestConfig(timeframe="5m", start="2026-10-08", end="2026-10-08", lot_size=1, live_timing=live)
        return run_backtest(build_strategy({"strategy": "log_xz", "params": {}}), StoreSource(store, "NIFTY50"), cfg).trades[0]

    live = first_trade(True)
    assert (hhmm(live.entry_time), live.direction) == ("09:26", "SHORT")  # paper: decided 09:26:00
    assert live.entry_price == open_at[live.entry_time]
    old = first_trade(False)
    assert hhmm(old.entry_time) == "09:25"
    assert date.fromtimestamp(live.entry_time) == date(2026, 10, 8)


# ---------------------------------------------------------------- the run request
def test_a_run_request_has_live_timing_on_unless_it_asks_for_the_old_timing() -> None:
    from app.backtest.catalog import RunRequestError, parse_config
    from app.backtest.execute import _engine_config
    from app.backtest.walkforward import child_backtest_config

    body = {"strategy": "log_xz", "symbol": "NIFTY50", "timeframe": "5m", "start": "2025-01-01", "mode": "options",
            "sessions": ["normal"]}
    on = parse_config(body)
    assert on["live_timing"] is True and _engine_config(on).live_timing is True
    off = parse_config({**body, "live_timing": False})
    assert off["live_timing"] is False and _engine_config(off).live_timing is False
    with pytest.raises(RunRequestError, match="live_timing"):
        parse_config({**body, "live_timing": "yes"})
    child = child_backtest_config({**off, "strike_offset": 0}, {}, date(2025, 1, 1), date(2025, 2, 1))
    assert child["live_timing"] is False


def test_a_walk_forward_request_carries_live_timing() -> None:
    from app.backtest.walkforward import parse_walk_forward

    body = {"kind": "walk_forward", "strategy": "price_channel", "symbol": "NIFTY50", "timeframe": "5m",
            "start": "2024-10-03", "end": "2026-06-30", "mode": "options"}
    assert parse_walk_forward(body)["live_timing"] is True
    assert parse_walk_forward({**body, "live_timing": False})["live_timing"] is False
