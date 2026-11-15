"""DD Nifty 5-confirmation strategy: pivots, entries on all four confirmations, Supertrend exit."""

from __future__ import annotations

import pytest

from app.backtest.catalog import build_strategy, parse_config, strategy_catalog
from app.strategies.dd_five_confirm import DdFiveConfirm, classic_pivots
from tests.bt_helpers import MON, TUE, day_bars, fills, run

LOOSE = dict(st_atr=5, st_mult=1.0, rsi_len=5, bb_len=10, bb_mult=2.0)


def prev_day() -> list:
    # a 99..101 day closing at 100: P = 100, R1 = 101, S1 = 99
    rows = [(100.0, 101.0, 99.0, 100.0)] * 375
    return day_bars(MON, rows)


def trend_day(sign: float) -> list:
    rows = []
    price = 100.0
    for i in range(60):  # quiet first hour inside the pivots
        c = 100.0 + (0.2 if i % 2 else -0.2)
        rows.append((price, max(price, c) + 0.1, min(price, c) - 0.1, c))
        price = c
    for _ in range(10):  # a sharp move away: breaks the band, the pivot, lifts / drops RSI, turns the Supertrend
        c = price + sign * 2.0
        rows.append((price, max(price, c) + 0.1, min(price, c) - 0.1, c))
        price = c
    for _ in range(20):
        rows.append((price, price + 0.1, price - 0.1, price))
    for _ in range(20):  # the reversal: the Supertrend flips against the trade
        c = price - sign * 3.0
        rows.append((price, max(price, c) + 0.1, min(price, c) - 0.1, c))
        price = c
    rows += [(price, price + 0.1, price - 0.1, price)] * (375 - len(rows))
    return day_bars(TUE, rows)


def test_classic_pivots() -> None:
    s1, p, r1 = classic_pivots(110.0, 90.0, 100.0)
    assert p == pytest.approx(100.0)
    assert r1 == pytest.approx(110.0)
    assert s1 == pytest.approx(90.0)


def test_long_entry_then_supertrend_exit() -> None:
    res = run(DdFiveConfirm(**LOOSE), prev_day() + trend_day(+1.0), square_off="15:15")
    f = fills(res)
    assert f, "expected a trade"
    assert f[0][1] == "BUY"
    assert any(side == "SELL" for _, side, _ in f[1:]), "the long must be closed by the Supertrend flip"
    assert f[1][0] < "11:00", "the exit comes from the reversal, not the 15:15 square-off"


def test_short_entry_then_supertrend_exit() -> None:
    res = run(DdFiveConfirm(**LOOSE), prev_day() + trend_day(-1.0), square_off="15:15")
    f = fills(res)
    assert f and f[0][1] == "SELL"
    assert any(side == "BUY" for _, side, _ in f[1:])


def test_no_entry_without_rsi_confirmation() -> None:
    res = run(DdFiveConfirm(**{**LOOSE, "rsi_long": 99.99, "rsi_short": 0.01}), prev_day() + trend_day(+1.0),
              square_off="15:15")
    assert fills(res) == []


def test_no_entry_on_the_first_day_without_pivots() -> None:
    res = run(DdFiveConfirm(**LOOSE), trend_day(+1.0), square_off="15:15")
    assert fills(res) == []


def test_long_only_skips_shorts() -> None:
    res = run(DdFiveConfirm(**{**LOOSE, "mode": "long_only"}), prev_day() + trend_day(-1.0), square_off="15:15")
    assert fills(res) == []


def test_last_entry_blocks_late_signals() -> None:
    res = run(DdFiveConfirm(**{**LOOSE, "last_entry": "09:30"}), prev_day() + trend_day(+1.0), square_off="15:15")
    assert fills(res) == []


def test_bad_params() -> None:
    with pytest.raises(ValueError):
        DdFiveConfirm(rsi_long=30, rsi_short=70)
    with pytest.raises(ValueError):
        DdFiveConfirm(mode="both")
    with pytest.raises(ValueError):
        DdFiveConfirm(last_entry="25:00")


def test_in_the_catalog_with_the_video_defaults() -> None:
    entry = next(s for s in strategy_catalog() if s["name"] == "dd_five_confirm")
    names = [p["name"] for p in entry["params"]] if isinstance(entry["params"], list) else list(entry["params"])
    assert {"st_atr", "st_mult", "rsi_len", "rsi_long", "rsi_short", "bb_len", "bb_mult", "mode"} <= set(names)
    s = build_strategy({"strategy": "dd_five_confirm", "params": {}})
    assert isinstance(s, DdFiveConfirm)
    assert (s.st_atr, s.st_mult, s.rsi_len, s.rsi_long, s.rsi_short, s.bb_len, s.bb_mult) == (10, 2.0, 14, 70.0, 30.0,
                                                                                               20, 2.0)


def test_use_pivots_off_trades_on_the_first_day() -> None:
    res = run(DdFiveConfirm(**{**LOOSE, "use_pivots": False}), trend_day(+1.0), square_off="15:15")
    assert fills(res) and fills(res)[0][1] == "BUY"


def test_williams_r_filter_blocks_and_allows() -> None:
    blocked = run(DdFiveConfirm(**{**LOOSE, "use_wr": True, "wr_long": -0.0001}), prev_day() + trend_day(+1.0),
                  square_off="15:15")
    allowed = run(DdFiveConfirm(**{**LOOSE, "use_wr": True}), prev_day() + trend_day(+1.0), square_off="15:15")
    assert fills(blocked) == []
    assert fills(allowed) and fills(allowed)[0][1] == "BUY"


def test_williams_r_value() -> None:
    class Bars:
        high = __import__("numpy").array([10.0, 12.0, 11.0])
        low = __import__("numpy").array([8.0, 9.0, 9.5])
        close = __import__("numpy").array([9.0, 11.0, 10.0])

        def __len__(self) -> int:
            return 3

    class Ctx:
        bars = Bars()

    s = DdFiveConfirm(use_wr=True, wr_len=3)
    assert s.williams_r(Ctx()) == pytest.approx((12.0 - 10.0) / (12.0 - 8.0) * -100.0)


def test_max_trades_per_day() -> None:
    # two up-legs in one day: the second entry is refused with max_trades_per_day=1
    day = trend_day(+1.0)
    res_all = run(DdFiveConfirm(**LOOSE), prev_day() + day, square_off="15:15")
    res_one = run(DdFiveConfirm(**{**LOOSE, "max_trades_per_day": 1}), prev_day() + day, square_off="15:15")
    buys_all = [f for f in fills(res_all) if f[1] == "BUY"]
    buys_one = [f for f in fills(res_one) if f[1] == "BUY"]
    assert len(buys_one) == 1 and len(buys_all) >= 1
