"""5 EMA context tags: gap_zone, range_shift, against_structure. They label trades and change none of them.

Tags ride on the entry tag after a "|" (`5ema_short|gap_zone,range_shift`); an untagged trade keeps the plain tag.
Every fixture below is worked out by hand. EMA(5) has alpha 1/3; 20 flat bars at a price put it on that price
(to within 0.01) and flat bars have no range, so they are never alert candles.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.backtest.breakdowns import tag_breakdown
from app.backtest.catalog import strategy_catalog
from app.backtest.engine import run_backtest
from app.backtest.execute import _view
from app.backtest.sources import ListSource
from app.data.candle import Candle
from app.strategies.five_ema import FiveEma
from tests.bt_helpers import FRI, MON, OHLC, THU, TUE, WED, cfg, day_bars, fills, flat, ist, kinds, random_days

# ---------------------------------------------------------------- gap_zone


def run_days(days: list[tuple[tuple[int, int, int], list[OHLC]]], **params: Any):  # noqa: ANN201
    candles = [c for day, rows in days for c in day_bars(day, rows)]
    return run_backtest(FiveEma(**params), ListSource(candles, 1), cfg(timeframe="1m", square_off="15:15"))


# MON 100 (high 100). TUE opens at 120: TUE's low so far 120 > MON's high 100, a gap up with edges 100 and 120.
# TUE 09:35 = 130/132/128/131: EMA 120 + 11/3 = 123.67 < 128, a sell alert. Entry 128 is 8 from the 120 edge.
GAP_UP = [(MON, flat(100, 20)), (TUE, flat(120, 20) + [(130, 132, 128, 131), (130, 130, 125, 126)] + flat(126, 3))]


def test_entry_near_todays_gap_edge_is_tagged_gap_zone() -> None:
    res = run_days(GAP_UP, gap_buffer=10.0)
    assert fills(res)[0] == ("09:36", "SELL", 128.0)
    assert res.trades[0].entry_tag == "5ema_short|gap_zone"
    assert run_days(GAP_UP).trades[0].entry_tag == "5ema_short|gap_zone"  # default buffer 30


def test_entry_farther_than_gap_buffer_is_not_tagged() -> None:
    assert run_days(GAP_UP, gap_buffer=5.0).trades[0].entry_tag == "5ema_short"


def _wed(first: OHLC) -> list[OHLC]:
    # WED 09:35 = 107/108/106/107.5: EMA 104 + 3.5/3 = 105.17 < 106, a sell alert at 106 (6 from the 100 edge)
    return [first] + flat(104, 19) + [(107, 108, 106, 107.5), (106, 106, 103, 103)] + flat(103, 3)


def test_an_earlier_days_gap_holds_until_price_trades_through_all_of_it() -> None:
    # TUE's gap up (100-120) is complete at TUE's close. WED trades down into it to 104 (partly filled): still open.
    held = run_days([(MON, flat(100, 20)), (TUE, flat(120, 20)), (WED, _wed((120, 120, 104, 104)))], gap_buffer=7.0)
    assert fills(held)[0] == ("09:36", "SELL", 106.0)
    assert held.trades[0].entry_tag == "5ema_short|gap_zone"
    # WED's first bar reaches 99, through the far edge 100: the gap is filled and tags nothing after
    filled = run_days([(MON, flat(100, 20)), (TUE, flat(120, 20)), (WED, _wed((120, 120, 99, 104)))], gap_buffer=7.0)
    assert fills(filled)[0] == ("09:36", "SELL", 106.0)
    assert filled.trades[0].entry_tag == "5ema_short"


def test_gap_down_is_the_mirror_for_buys() -> None:
    # TUE at 80: high 80 < MON's low 100, a gap down (edges 80 and 100). TUE 09:35 = 70/72/68/69: EMA 80 - 11/3
    # = 76.33 > 72, a buy alert at 72 (8 from the 80 edge).
    days = [(MON, flat(100, 20)), (TUE, flat(80, 20) + [(70, 72, 68, 69), (70, 75, 70, 75)] + flat(75, 3))]
    res = run_days(days, direction="long_only", gap_buffer=10.0)
    assert fills(res)[0] == ("09:36", "BUY", 72.0)
    assert res.trades[0].entry_tag == "5ema_long|gap_zone"


# ---------------------------------------------------------------- swing structure (swing_bars=1 unless said)
# 09:35-09:41 (bars 20-26) after 20 flat bars at 100. Pivot = high (low) strictly beyond the swing_bars bars on each
# side, confirmed when the last of them closes. EMA after each bar: 100.33 101.22 101.48 102.65 103.10 103.07 103.38;
# no bar is a sell alert (lowest margin: bar 24's low 103 under 103.10).
#   bar 20 low 99  -> swing low 99   (confirmed at bar 21)
#   bar 21 high 104 -> swing high 104 (bar 22)
#   bar 23 closes 105 > 104          -> the structure breaks up: bias up (higher high)
#   bar 23 high 106 -> swing high 106 (bar 24); bar 25 low 102 -> swing low 102 (bar 26)
UP: list[OHLC] = flat(100, 20) + [
    (100, 102, 99, 101), (101, 104, 100, 103), (103, 103, 101, 102), (102, 106, 102, 105),
    (105, 105, 103, 104), (104, 104, 102, 103), (103, 104, 103, 104),
]
BREAK_DOWN: OHLC = (104, 104, 100, 101)  # bar 27 closes 101 < swing low 102 after higher highs: range shift down
# bar 28: EMA 102.59 + (105.5 - 102.59) / 3 = 103.56 < 104, a sell alert at 104 (stop 107); close 105.5 < 106
SHIFT = UP + [BREAK_DOWN, (104, 107, 104, 105.5)]


def run1(rows: list[OHLC], **params: Any):  # noqa: ANN201
    return run_days([(MON, rows)], **{"swing_bars": 1, **params})


def mirror(rows: list[OHLC]) -> list[OHLC]:
    """The same chart upside down around 100: highs become lows. EMA and swings mirror with it."""
    return [(200 - o, 200 - lo, 200 - h, 200 - c) for o, h, lo, c in rows]


def test_first_sell_after_higher_highs_break_their_last_swing_low_is_range_shift() -> None:
    res = run1(SHIFT + [(104, 104, 101, 101)] + flat(101, 3))
    assert fills(res)[0] == ("09:44", "SELL", 104.0)
    assert res.trades[0].entry_tag == "5ema_short|range_shift"


def test_first_buy_after_lower_lows_break_their_last_swing_high_is_range_shift() -> None:
    res = run1(mirror(SHIFT + [(104, 104, 101, 101)] + flat(101, 3)), direction="long_only")
    assert fills(res)[0] == ("09:44", "BUY", 96.0)
    assert res.trades[0].entry_tag == "5ema_long|range_shift"


def test_a_sell_while_the_bias_is_up_is_against_structure() -> None:
    # bar 27 dips to 102.5, above the swing low 102: no break, the bias stays up. Bar 28 = 105/108/105/107:
    # EMA 103.25 + 3.75/3 = 104.50 < 105, a sell alert at 105.
    res = run1(UP + [(104, 104, 102.5, 103), (105, 108, 105, 107), (105, 105, 102, 102)] + flat(102, 3))
    assert fills(res)[0] == ("09:44", "SELL", 105.0)
    assert res.trades[0].entry_tag == "5ema_short|against_structure"


def test_a_replacing_alert_candle_keeps_range_shift() -> None:
    # bar 29 = 105/106/104.5/105 does not reach 104 and is itself an alert (EMA 104.04 < 104.5): the order moves
    rows = SHIFT + [(105, 106, 104.5, 105), (104.5, 104.5, 103, 103)] + flat(103, 3)
    res = run1(rows)
    placed = [(e["price"], e["tag"]) for e in kinds(res, "order_placed") if not e["reduce_only"]]
    assert placed == [(104.0, "5ema_short|range_shift"), (104.5, "5ema_short|range_shift")]
    assert fills(res)[0] == ("09:45", "SELL", 104.5)
    assert res.trades[0].entry_tag == "5ema_short|range_shift"


def test_range_shift_goes_to_the_first_signal_only_even_when_it_lapses() -> None:
    # bar 29 = 105/106/104.2/105.8 misses 104 and is no alert (EMA 104.31 > 104.2): bar 28's order lapses.
    # bar 30 = 106/106.5/105.8/106 is a new alert (EMA 104.87 < 105.8), a new signal: no range_shift.
    rows = SHIFT + [(105, 106, 104.2, 105.8), (106, 106.5, 105.8, 106), (105.8, 105.8, 104, 104)] + flat(104, 3)
    res = run1(rows)
    assert fills(res)[0] == ("09:46", "SELL", 105.8)
    assert res.trades[0].entry_tag == "5ema_short"


def test_swing_bars_sets_the_pivot_width() -> None:
    # with 2 bars a side: bar 25's low 102 ties bar 23's, so it is no swing low and nothing breaks; bias none
    res = run1(SHIFT + [(104, 104, 101, 101)] + flat(101, 3), swing_bars=2)
    assert res.trades[0].entry_tag == "5ema_short"


def test_tags_do_not_change_the_trades() -> None:
    rows = SHIFT + [(105, 106, 104.5, 105), (104.5, 104.5, 103, 103)] + flat(103, 3)
    tagged, plain = run1(rows), run1(rows, swing_bars=50, gap_buffer=0.0)
    assert plain.trades[0].entry_tag == "5ema_short"
    assert fills(tagged) == fills(plain)
    assert [t.net_pnl for t in tagged.trades] == [t.net_pnl for t in plain.trades]


def test_tag_parameters_are_validated_and_listed() -> None:
    for bad in ({"gap_buffer": -1.0}, {"swing_bars": 0}):
        with pytest.raises(ValueError):
            FiveEma(**bad)
    entry = next(s for s in strategy_catalog() if s["name"] == "five_ema")
    assert entry["params"]["gap_buffer"]["default"] == 30.0
    assert entry["params"]["swing_bars"]["default"] == 5


# ---------------------------------------------------------------- no look-ahead, on data that has daily gaps
DAYS = [MON, TUE, WED, THU, FRI]
T = ist(*WED, 11, 0)


def gapped(seed: int, start: float, vol: float) -> list[Candle]:
    """Random sessions, every other day moved up 150 points, so that daily gaps up and down form and fill."""
    candles = random_days(DAYS, seed=seed, start_price=start, vol=vol)
    first = candles[0]["time"]
    out: list[Candle] = []
    for c in candles:
        shift = 150.0 * ((c["time"] - first) // 86_400 % 2)
        out.append({**c, "open": c["open"] + shift, "high": c["high"] + shift, "low": c["low"] + shift,
                    "close": c["close"] + shift})
    return out


def _events(candles: list[Candle], tf: str) -> list[dict[str, Any]]:
    res = run_backtest(FiveEma(direction="both", swing_bars=2, gap_buffer=60.0), ListSource(candles, 1),
                       cfg(timeframe=tf, square_off="15:15", warmup_bars=0))
    # at T: the decisions taken on the bars that ended at T, not the bracket orders of a fill in the minute from T
    return [e for e in res.events
            if e["t"] < T or (e["t"] == T and e["kind"] in ("signal", "order_placed", "order_rejected")
                              and not e.get("reduce_only"))]


@pytest.mark.parametrize("tf", ["5m", "15m"])
def test_tags_use_closed_bars_only(tf: str) -> None:
    base = gapped(7, 22000.0, 6.0)
    alt = {c["time"]: c for c in gapped(99, 23500.0, 40.0)}
    scrambled = [alt[c["time"]] if c["time"] >= T else c for c in base]
    a, b = _events(base, tf), _events(scrambled, tf)
    assert a == b
    tags = {t for e in a if e["kind"] == "order_placed" for t in e["tag"].partition("|")[2].split(",") if t}
    assert {"gap_zone"} <= tags and tags & {"range_shift", "against_structure"}, tags  # not vacuous


# ---------------------------------------------------------------- the breakdown


def test_tag_breakdown_gives_the_summary_stats_per_tag() -> None:
    rows = [
        {"entry_tag": "5ema_short|gap_zone", "net_pnl": 30.0, "r": 3.0},
        {"entry_tag": "5ema_short|gap_zone,range_shift", "net_pnl": -10.0, "r": -1.0},
        {"entry_tag": "5ema_short", "net_pnl": -10.0, "r": -1.0},
        {"entry_tag": "5ema_short", "net_pnl": 20.0, "r": 2.0},
        {"entry_tag": "5ema_short|range_shift", "net_pnl": 0.0, "r": None},
    ]
    out = tag_breakdown(rows)
    assert list(out) == ["untagged", "gap_zone", "range_shift"]
    assert out["untagged"] == {"trades": 2, "wins": 1, "win_rate": 0.5, "avg_r": 0.5, "profit_factor": 2.0,
                               "net_pnl": 10.0}
    assert out["gap_zone"] == {"trades": 2, "wins": 1, "win_rate": 0.5, "avg_r": 1.0, "profit_factor": 3.0,
                               "net_pnl": 20.0}
    # a trade with no R (no stop) still counts; one with tags in two groups counts in both
    assert out["range_shift"] == {"trades": 2, "wins": 0, "win_rate": 0.0, "avg_r": -1.0, "profit_factor": 0.0,
                                  "net_pnl": -10.0}


def test_tag_breakdown_is_empty_when_no_trade_carries_a_tag() -> None:
    assert tag_breakdown([{"entry_tag": "orb_long", "net_pnl": 5.0, "r": 1.0}]) == {}
    assert tag_breakdown([]) == {}


def test_the_run_view_carries_the_tag_breakdown_with_r_from_the_bracket_stop() -> None:
    res = run_days(GAP_UP)  # entry 128, stop 132 (risk 4); square-off at 126 nets +2: R 0.5
    view = _view(res, None, {"mode": "index", "timeframe": "1m", "exit_rule": None})
    assert view["breakdowns"]["tags"]["gap_zone"]["trades"] == 1
    assert view["breakdowns"]["tags"]["gap_zone"]["avg_r"] == pytest.approx(res.trades[0].net_pnl / 4.0)
