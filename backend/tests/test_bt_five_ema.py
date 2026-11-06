"""5 EMA (Power of Stocks): alert candle off the EMA, stop-entry at its break, stop at its other end, 1:rr target."""

from __future__ import annotations

import pytest

from app.backtest.catalog import parse_config, strategy_catalog
from app.backtest.engine import run_backtest
from app.backtest.sources import ListSource
from app.strategies.five_ema import FiveEma
from tests.bt_helpers import MON, OHLC, cfg, day_bars, fills, flat, hhmm, kinds

# 20 flat bars at 100 (09:15-09:34): EMA(5) = 100. Bar 20 (09:35) = 110/112/108/111 closes with
# EMA = 100 + (111 - 100) / 3 = 103.67 < its low 108, so it is a sell alert candle:
# SELL stop at 108, stop loss 112, target 108 - 3 * 4 = 96.
BASE: list[OHLC] = flat(100, 20)
ALERT: OHLC = (110, 112, 108, 111)


def run(rows: list[OHLC], tf: str = "1m", **params):  # noqa: ANN003, ANN201
    strategy = FiveEma(**params)
    return run_backtest(strategy, ListSource(day_bars(MON, rows), 1), cfg(timeframe=tf, square_off="15:15"))


def test_sell_alert_candle_break_fills_at_its_low_and_hits_the_1_3_target() -> None:
    res = run(BASE + [ALERT, (110, 110, 105, 106), (106, 106, 95, 95)] + flat(95, 5))
    assert fills(res) == [("09:36", "SELL", 108.0), ("09:37", "BUY", 96.0)]
    (t,) = res.trades
    assert (t.direction, t.exit_reason, t.net_pnl) == ("SHORT", "limit", 12.0)  # the target is a limit order


def test_sell_stop_loss_is_the_alert_candle_high() -> None:
    res = run(BASE + [ALERT, (110, 110, 107, 109), (109, 113, 109, 113)] + flat(113, 3))
    assert fills(res) == [("09:36", "SELL", 108.0), ("09:37", "BUY", 112.0)]
    assert res.trades[0].exit_reason == "stop" and res.trades[0].net_pnl == -4.0


def test_a_newer_alert_candle_replaces_the_waiting_order() -> None:
    # 09:36 does not break 108 and is itself off the EMA: the order moves to its low 109 (stop 115)
    res = run(BASE + [ALERT, (111, 115, 109, 114), (110, 110, 104, 104)] + flat(104, 3))
    placed = [(hhmm(e["t"]), e["price"]) for e in kinds(res, "order_placed") if e["tag"] == "5ema_short"]
    assert placed == [("09:36", 108.0), ("09:37", 109.0)]  # placed as each alert candle closes
    assert fills(res)[0] == ("09:37", "SELL", 109.0)
    assert res.trades[0].entry_price == 109.0


def test_the_order_lapses_after_entry_bars_when_no_new_alert_candle_forms() -> None:
    # 09:35 = 102/104/101.5/103: EMA 101 < 101.5 -> SELL stop 101.5. 09:36 = 104/110/102/110 does not break
    # 101.5 and dips into the EMA (104 > its low 102), so it is no new alert candle. 09:37 breaks 101.5.
    rows = BASE + [(102, 104, 101.5, 103), (104, 110, 102, 110), (104, 104, 100, 100)] + flat(100, 3)
    assert run(rows, entry_bars=1).trades == []  # the order lived for one candle only
    assert fills(run(rows, entry_bars=2))[0] == ("09:37", "SELL", 101.5)


def test_buy_alert_candle_is_the_mirror_image() -> None:
    # 09:35 = 90/92/88/89: EMA = 100 - 11/3 = 96.33 > its high 92 -> BUY stop 92, stop loss 88, target 92 + 12 = 104
    res = run(BASE + [(90, 92, 88, 89), (90, 95, 90, 95), (95, 105, 95, 105)] + flat(105, 3), direction="long_only")
    assert fills(res) == [("09:36", "BUY", 92.0), ("09:37", "SELL", 104.0)]
    assert res.trades[0].net_pnl == 12.0


def test_short_only_ignores_buy_alert_candles_and_long_only_ignores_sell_ones() -> None:
    down = BASE + [(90, 92, 88, 89), (90, 95, 90, 95)] + flat(95, 3)
    up = BASE + [ALERT, (110, 110, 105, 106)] + flat(106, 3)
    assert run(down, direction="short_only").trades == []
    assert run(up, direction="long_only").trades == []


def test_max_trades_per_day() -> None:
    rows = BASE + [ALERT, (110, 113, 107, 113)]  # entry 108, stopped at 112
    rows += flat(113, 10) + [(123, 125, 121, 124), (124, 126, 120, 125)] + flat(125, 3)  # a second sell setup
    one = run(rows, max_trades_per_day=1)
    two = run(rows, max_trades_per_day=2)
    assert len(one.trades) == 1
    assert len(two.trades) == 2


def test_rr_sets_the_target() -> None:
    res = run(BASE + [ALERT, (110, 110, 105, 106), (106, 106, 95, 95)] + flat(95, 5), rr=2.0)
    assert fills(res)[1] == ("09:37", "BUY", 100.0)  # 108 - 2 * 4


def test_parameters_are_validated() -> None:
    for bad in ({"direction": "up"}, {"rr": 0}, {"ema_length": 0}, {"entry_bars": 0}, {"max_trades_per_day": 0},
                {"lots": 0}):
        with pytest.raises(ValueError):
            FiveEma(**bad)


def test_listed_in_the_backtest_catalog_with_1_3_default() -> None:
    entry = next(s for s in strategy_catalog() if s["name"] == "five_ema")
    assert entry["params"]["rr"]["default"] == 3.0
    assert entry["params"]["direction"]["choices"] == ["short_only", "long_only", "both"]
    config = parse_config({"strategy": "five_ema", "symbol": "NIFTY50", "timeframe": "5m", "start": "2024-01-01",
                           "end": "2024-02-01", "sessions": ["normal"], "mode": "index"})
    assert config["params"]["direction"] == "short_only"
