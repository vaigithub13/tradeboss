"""Pine ports: Pivot Extension, Log XZ, Price Channel.

Hand bars only. tv_parity follows Pine's clock and OHLC path. realistic is the
default and uses the engine's 15:15 square-off.
"""

from __future__ import annotations

from datetime import date, datetime

from app.backtest.walkforward import child_backtest_config, default_grid, parse_walk_forward, run_walk_forward
from app.strategies.log_xz import LogXZ
from app.strategies.pivot_extension import PivotExtension
from app.strategies.price_channel import PriceChannel
from tests.bt_helpers import MON, TUE, IST, day_bars, fills, hhmm, kinds, run

TICK = 0.05


def _rows(n: int, price: float = 100.0) -> list[tuple[float, float, float, float]]:
    return [(price, price, price, price)] * n


def _go(strategy, rows, *, step: int, timeframe: str, square_off: str = "15:15"):
    candles = day_bars(MON, rows, start=(9, 15), step_min=step)
    return run(strategy, candles, base_minutes=step, timeframe=timeframe, square_off=square_off)


def _placed(res, kind: str = "order_placed"):
    return [(hhmm(e["t"]), e["side"], e["type"], None if e["price"] is None else round(e["price"], 2))
            for e in kinds(res, kind)]


def _px(row: tuple[str, str, float]) -> tuple[str, str, float]:
    return row[0], row[1], round(row[2], 2)


# ---------------------------------------------------------------- execution modes
def test_15m_tv_parity_carries_overnight_and_realistic_does_not() -> None:
    rows = _rows(25)
    rows[2] = (100, 110, 100, 100)  # channel arms a buy stop above 110
    rows[3] = (100, 111, 100, 105)  # the stop fills; later bars never reach the opposite stop
    rows[24] = (108, 108, 108, 108)  # 15:15
    day2 = day_bars(TUE, [(105, 105, 105, 105)], start=(9, 15), step_min=15)
    candles = day_bars(MON, rows, start=(9, 15), step_min=15) + day2

    realistic = run(PriceChannel(length=2), candles, base_minutes=15, timeframe="15m", square_off="15:15")
    parity = run(
        PriceChannel(length=2, execution="tv_parity"),
        candles, base_minutes=15, timeframe="15m", square_off="15:15",
    )

    (real,) = realistic.trades
    assert real.direction == "LONG"
    assert datetime.fromtimestamp(real.entry_time, IST).date() == date(*MON)
    assert datetime.fromtimestamp(real.exit_time, IST).date() == date(*MON)
    assert hhmm(real.exit_time) == "15:15"
    assert round(real.exit_price, 2) == 108.0
    assert real.exit_reason == "square_off"
    assert realistic.strategy["allow_overnight"] is False

    # The 1515-1520 close never fires, and the data ending does not flatten it.
    assert parity.trades == []
    assert [hhmm(e["t"]) for e in kinds(parity, "fill")] == ["10:00"]
    assert "09:15" not in [hhmm(e["t"]) for e in kinds(parity, "fill") if e["t"] != kinds(parity, "fill")[0]["t"]]
    assert parity.strategy["allow_overnight"] is True
    assert parity.counters["end_of_data_exits"] == 0


def test_tv_parity_ohlc_path_hits_the_far_stop_first_on_a_red_bar() -> None:
    """Buy stop is closer to the open. A red bar still reaches the sell stop first."""
    rows = _rows(4)
    rows[1] = (100, 101.95, 95.05, 100)
    rows[2] = (100, 101.95, 95.05, 100)  # arms buy 102.00 and sell 95.00
    rows[3] = (100, 103, 94, 99)  # red: open → 94 → 103 → 99
    tv = _go(PriceChannel(length=2, execution="tv_parity"), rows, step=15, timeframe="15m")
    real = _go(PriceChannel(length=2), rows, step=15, timeframe="15m")

    assert [_px(f) for f in fills(tv)[:2]] == [("10:00", "SELL", 95.0), ("10:00", "BUY", 102.0)]
    assert tv.trades[0].ambiguous is False
    assert [_px(f) for f in fills(real)[:1]] == [("10:00", "BUY", 102.0)]
    assert real.trades[0].ambiguous is True


# ---------------------------------------------------------------- Pivot Extension
def _pivot_rows() -> list[tuple[float, float, float, float]]:
    return [
        (10, 12, 10, 10),
        (10, 11, 8, 10),
        (10, 13, 10, 10),  # pivot low 8 confirms; no pivot high
        (10, 13, 9.5, 10),  # market long fills; this bar does not confirm a pivot
    ]


def test_faithful_flat_pivot_low_is_a_market_long_and_a_stop_short() -> None:
    res = _go(PivotExtension(left_bars=1, right_bars=1), _pivot_rows(), step=5, timeframe="5m")
    assert _placed(res)[:2] == [("09:30", "BUY", "MARKET", None), ("09:30", "SELL", "SL", 7.95)]
    assert _px(fills(res)[0]) == ("09:30", "BUY", 10.0)
    assert "09:25" not in [t for t, *_ in _placed(res)]


def test_tv_parity_leaves_a_missing_stop_working_instead_of_sending_a_market_order() -> None:
    res = _go(
        PivotExtension(left_bars=1, right_bars=1, execution="tv_parity"),
        _pivot_rows(), step=5, timeframe="5m",
    )
    assert _placed(res) == [("09:30", "SELL", "SL", 7.95)]
    assert fills(res) == []


def test_faithful_ignores_a_pivot_high_while_flat() -> None:
    rows = [(4, 5, 4, 4), (4, 9, 4, 4), (4, 6, 4, 4)]
    res = _go(PivotExtension(left_bars=1, right_bars=1), rows, step=5, timeframe="5m")
    assert _placed(res) == []
    assert fills(res) == []


def test_faithful_in_a_position_ignores_a_pivot_low_and_reverses_on_a_pivot_high() -> None:
    quiet = _pivot_rows() + [
        (10, 12, 8.5, 10),
        (10, 13, 9, 10),  # pivot low 8.5 only, already long: no new orders
        (10, 13, 9.2, 10),  # not the last bar, so the session-end flatten has not run yet
    ]
    held = _go(PivotExtension(left_bars=1, right_bars=1), quiet, step=5, timeframe="5m")
    assert [t for t, *_ in _placed(held)] == ["09:30", "09:30"]

    reverse = _pivot_rows() + [
        (10, 12, 9.5, 10),
        (10, 20, 9.5, 10),
        (10, 15, 9.5, 10),  # pivot high 20 confirms
        (11, 11, 11, 11),
    ]
    res = _go(PivotExtension(left_bars=1, right_bars=1), reverse, step=5, timeframe="5m")
    assert ("09:50", "SELL", "MARKET", None) in _placed(res)
    assert ("09:50", "BUY", "SL", 20.05) in _placed(res)
    assert _px(fills(res)[1]) == ("09:50", "SELL", 11.0)
    assert res.trades[0].direction == "LONG"
    assert round(res.trades[0].exit_price, 2) == 11.0


def test_carried_pivots_keeps_the_last_confirmed_pivots_on_a_later_bar() -> None:
    rows = _pivot_rows() + [
        (10, 12, 9.5, 10),
        (10, 20, 9.5, 10),
        (10, 15, 9.5, 10),  # pivot high 20
        (10, 16, 9.6, 10),  # no new pivot
    ]
    carried = _go(PivotExtension(left_bars=1, right_bars=1, variant="carried_pivots"), rows, step=5, timeframe="5m")
    faithful = _go(PivotExtension(left_bars=1, right_bars=1), rows, step=5, timeframe="5m")
    assert ("09:55", "BUY", "SL", 20.05) in _placed(carried)
    assert ("09:55", "SELL", "SL", 7.95) in _placed(carried)
    assert all(typ == "SL" for _, _, typ, _ in _placed(carried))
    assert "09:55" not in [t for t, *_ in _placed(faithful)]
    assert "carried_pivots" in PivotExtension(variant="carried_pivots").params["variant"]


# ---------------------------------------------------------------- Log XZ
def test_log_xz_buys_when_the_previous_value_is_zero_and_ignores_the_current_close() -> None:
    closes = [100.0] * 30
    closes[20] = 140.0
    rows = [(c, c, c, c) for c in closes]
    rows[22] = (111, 111, 111, 111)
    res = _go(LogXZ(z_length=4), rows, step=5, timeframe="5m")
    assert _px(fills(res)[0]) == ("11:05", "BUY", 111.0)
    assert LogXZ().params["ma"] == "rma"

    alt = [c for c in closes]
    alt[21] = 50.0  # the signal bar. XZ does not use it; a current-bar formula would not buy
    alt_rows = [(c, c, c, c) for c in alt]
    alt_rows[22] = (111, 111, 111, 111)
    other = _go(LogXZ(z_length=4), alt_rows, step=5, timeframe="5m")
    assert _px(fills(other)[0]) == ("11:05", "BUY", 111.0)

    later = [(c, c, c, c) for c in closes]
    later[25] = (90, 90, 90, 90)  # the bar after XZ crosses back under 0
    reversed_ = _go(LogXZ(z_length=4), later, step=5, timeframe="5m")
    assert ("11:20", "SELL", 90.0) in [_px(f) for f in fills(reversed_)]


def test_log_xz_ema_branch_uses_the_sma_seed() -> None:
    closes = [
        96.3436, 103.4743, 102.6377, 97.5507, 99.9544, 99.4949, 101.5159, 102.8872, 95.9386, 95.2835,
        103.3577, 99.3277, 102.6228, 95.0211, 99.4539, 102.2154, 97.2876, 104.4527,
    ]
    rows = [(c, max(c, c), min(c, c), c) for c in closes]
    res = _go(LogXZ(z_length=4, ma="ema"), rows, step=5, timeframe="5m")
    buys = [f for f in fills(res) if f[1] == "BUY"]
    assert _px(buys[0])[0] == "10:20"  # Pine SMA-seeded EMA. The first-value seed buys at 09:55.


# ---------------------------------------------------------------- Price Channel
def test_price_channel_stop_includes_the_current_bar_and_reverses() -> None:
    rows = _rows(4)
    rows[0] = (10, 10, 9, 10)
    rows[1] = (10, 12, 9, 10)
    rows[2] = (10, 11, 9, 10)
    rows[3] = (10, 15, 9, 10)  # length 3, first arm: highest high is this bar
    armed = _go(PriceChannel(length=3), rows, step=5, timeframe="5m")
    assert _placed(armed)[0] == ("09:35", "BUY", "SL", round(15 + TICK, 2))
    assert all(not str(e["tag"]).endswith((":stop", ":target")) for e in kinds(armed, "order_placed"))

    rev = _rows(5)
    rev[2] = (100, 110, 90, 100)
    rev[3] = (100, 112, 100, 105)
    rev[4] = (100, 100, 89, 95)
    res = _go(PriceChannel(length=2), rev, step=5, timeframe="5m")
    assert res.trades[0].direction == "LONG"
    assert round(res.trades[0].entry_price, 2) == 110.05
    assert round(res.trades[0].exit_price, 2) == 89.95
    assert res.trades[0].exit_reason == "stop"
    assert res.trades[1].direction == "SHORT"
    assert round(res.trades[1].entry_price, 2) == 89.95


def test_a_working_stop_still_fills_after_the_entry_window_closes() -> None:
    # 14:40, 14:45, 14:50 arms, 14:55 is outside 09:15–14:50 and still fills the resting stop
    rows = [
        (100, 100, 100, 100),
        (100, 100, 100, 100),
        (100, 110, 100, 100),
        (105, 112, 105, 108),
    ]
    candles = day_bars(MON, rows, start=(14, 40), step_min=5)
    res = run(PriceChannel(length=2), candles, base_minutes=5, timeframe="5m", square_off="15:15")
    assert _px(fills(res)[0]) == ("14:55", "BUY", 110.05)
    assert "15:00" not in [hhmm(e["t"]) for e in kinds(res, "order_placed")]


def test_target_and_stop_follow_the_ohlc_path_when_both_are_on() -> None:
    closes = [100.0] * 23
    closes[20] = 140.0
    rows = [(c, c, c, c) for c in closes]
    rows[22] = (100, 112, 90, 105)  # green: target 110 is reached before stop 93
    tv = _go(LogXZ(z_length=4, execution="tv_parity", use_target=True, use_stop=True), rows, step=5, timeframe="5m")
    real = _go(LogXZ(z_length=4, use_target=True, use_stop=True), rows, step=5, timeframe="5m")
    assert tv.trades[0].exit_reason == "limit"
    assert round(tv.trades[0].exit_price, 2) == 110.0
    assert tv.trades[0].ambiguous is False
    assert real.trades[0].exit_reason == "stop"
    assert round(real.trades[0].exit_price, 2) == 93.0
    assert real.trades[0].ambiguous is True


# ---------------------------------------------------------------- walk-forward grid
def test_timeframe_and_carried_pivots_count_in_the_walk_forward_total() -> None:
    pivot = default_grid("pivot_extension")
    assert {row["variant"] for row in pivot} == {"faithful", "carried_pivots"}
    assert {row["timeframe"] for row in pivot} == {"5m", "15m"}
    assert sum(row["variant"] == "carried_pivots" for row in pivot) == len(pivot) // 2

    xz = default_grid("log_xz")
    assert {row["timeframe"] for row in xz} == {"5m", "15m"}
    assert {row["z_length"] for row in xz} == {10, 14}
    channel = default_grid("price_channel")
    assert {row["timeframe"] for row in channel} == {"5m", "15m"}
    assert {row["length"] for row in channel} == {20, 40}

    seen: list[tuple[str, str]] = []

    def evaluate(params, start, end):
        seen.append((params["variant"], params["timeframe"]))
        return {"net_pnl": -1.0, "max_drawdown": 1.0, "trades": 1, "pnls": [-1.0]}

    spec = parse_walk_forward({
        "strategy": "pivot_extension",
        "symbol": "NIFTY50",
        "timeframe": "5m",
        "start": "2024-10-03",
        "end": "2025-08-02",
        "sessions": ["normal", "weekend_full"],
        "include_forward": False,
    })
    result = run_walk_forward(spec, evaluate)
    assert result["combinations_tried"] == len(seen)
    assert set(seen) == {("faithful", "5m"), ("faithful", "15m"), ("carried_pivots", "5m"), ("carried_pivots", "15m")}
    child = child_backtest_config(
        {"strategy": "pivot_extension", "timeframe": "5m", "symbol": "NIFTY50", "sessions": ["normal"],
         "strike_offset": 0, "slippage_points": 1.0},
        {"variant": "carried_pivots", "timeframe": "15m", "lots": 1},
        date(2024, 10, 3),
        date(2024, 12, 1),
    )
    assert child["timeframe"] == "15m"
    assert child["params"] == {"variant": "carried_pivots", "lots": 1}


def test_ports_reject_an_unknown_execution_mode() -> None:
    for cls in (PivotExtension, LogXZ, PriceChannel):
        try:
            cls(execution="live")
        except ValueError:
            continue
        raise AssertionError(cls)
