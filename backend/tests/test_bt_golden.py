"""Golden test (6): 30 hand-made 1m bars, a 3-rule strategy, trades and totals checked by hand.

Monday 2026-01-05, bar k starts at 09:15 + (k-1) min, lot size 10, 1 lot, zero costs / slippage.
  entry : flat and C[k] > C[k-1] > C[k-2]      -> BUY 1  (fills at the next open)
  stop  : once long, place SELL stop at entry-3 (working from the NEXT bar)
  exit  : long and C[k] < C[k-1] < C[k-2]      -> EXIT   (fills at the next open)
"""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy
from tests.bt_helpers import MON, OHLC, day_bars, hhmm, run

BARS: list[OHLC] = [
    (100, 100.5, 99.5, 100),  # 1
    (100, 101.2, 99.8, 101),  # 2
    (101, 102.3, 100.8, 102),  # 3   BUY signal
    (102, 103, 101.5, 103),  # 4   fill 102.0, SL 99.0 placed
    (103, 104, 102.5, 104),  # 5
    (104, 104.5, 102, 102.5),  # 6
    (102.5, 103, 101, 101.5),  # 7   EXIT signal
    (101.5, 102, 100.5, 101),  # 8   exit fill 101.5, SL cancelled
    (101, 102.5, 100.8, 102),  # 9
    (102, 103.5, 101.9, 103),  # 10  BUY signal
    (103.2, 104, 102.8, 103.8),  # 11  fill 103.2, SL 100.2 placed
    (103.8, 104.5, 103.5, 104.5),  # 12
    (104.5, 105, 104.2, 104.8),  # 13
    (104.8, 105.2, 104.6, 105),  # 14
    (99, 99.5, 98, 98.5),  # 15  gap below the stop: fills at the OPEN 99.0
    (98.5, 99.5, 98.2, 99.5),  # 16
    (99.5, 100.5, 99.3, 100.2),  # 17  BUY signal
    (100.2, 101, 99.9, 100.8),  # 18  fill 100.2, SL 97.2 placed
    (100.8, 102, 100.5, 101.8),  # 19
    (101.8, 103, 101.5, 102.9),  # 20
    (102.9, 104, 102.7, 103.9),  # 21
    (103.9, 105, 103.8, 104.9),  # 22
    (104.9, 106, 104.7, 105.8),  # 23
    (105.8, 106.5, 105.5, 106.2),  # 24
    (106.2, 107, 106, 106.8),  # 25
    (106.8, 107.2, 106.1, 106.3),  # 26
    (106.3, 106.4, 105.2, 105.4),  # 27  EXIT signal
    (105.4, 105.9, 104.9, 105),  # 28  exit fill 105.4
    (105, 105.6, 104.8, 105.5),  # 29
    (105.5, 106.2, 105.4, 106),  # 30  BUY signal, but there is no next bar
]


class Golden(Strategy):
    name = "golden"

    def __init__(self) -> None:
        super().__init__()
        self.sl_placed = False

    def on_bar(self, bar: Any, ctx: Any) -> list[Signal]:
        c = ctx.bars.close
        n = len(c)
        if ctx.position.lots == 0:
            self.sl_placed = False
            if n >= 3 and c[-1] > c[-2] > c[-3]:
                return [Signal("BUY", 1, tag="entry")]
            return []
        out: list[Signal] = []
        if not self.sl_placed:
            out.append(Signal("SELL", 1, "SL", round(ctx.position.avg_price - 3, 2), tag="sl"))
            self.sl_placed = True
        if n >= 3 and c[-1] < c[-2] < c[-3]:
            out.append(Signal("EXIT", 1, tag="exit"))
        return out


def golden():  # noqa: ANN201
    return run(Golden(), day_bars(MON, BARS), lot_size=10)


def test_6_trades_and_pnl_match_the_hand_calculation() -> None:
    res = golden()
    got = [(hhmm(t.entry_time), t.entry_price, hhmm(t.exit_time), t.exit_price, t.net_pnl, t.exit_reason) for t in res.trades]
    assert got == [
        ("09:18", 102.0, "09:22", 101.5, -5.0, "market"),
        ("09:25", 103.2, "09:29", 99.0, -42.0, "stop"),
        ("09:32", 100.2, "09:42", 105.4, 52.0, "market"),
    ]
    assert [t.gap for t in res.trades] == [False, True, False]
    assert all(t.lots == 1 and t.units == 10 and t.lot_size == 10 and t.charges_total == 0.0 for t in res.trades)


def test_6_order_log_is_exactly_what_the_hand_calculation_says() -> None:
    res = golden()
    placed = [(hhmm(e["t"]), e["side"], e["type"], e["price"], e["tag"]) for e in res.events if e["kind"] == "order_placed"]
    assert placed == [
        ("09:18", "BUY", "MARKET", None, "entry"),
        ("09:19", "SELL", "SL", 99.0, "sl"),
        ("09:22", "SELL", "MARKET", None, "exit"),
        ("09:25", "BUY", "MARKET", None, "entry"),
        ("09:26", "SELL", "SL", 100.2, "sl"),
        ("09:32", "BUY", "MARKET", None, "entry"),
        ("09:33", "SELL", "SL", 97.2, "sl"),
        ("09:42", "SELL", "MARKET", None, "exit"),
        ("09:45", "BUY", "MARKET", None, "entry"),
    ]
    fills = [(hhmm(e["t"]), e["side"], e["price"]) for e in res.events if e["kind"] == "fill"]
    assert fills == [
        ("09:18", "BUY", 102.0),
        ("09:22", "SELL", 101.5),
        ("09:25", "BUY", 103.2),
        ("09:29", "SELL", 99.0),
        ("09:32", "BUY", 100.2),
        ("09:42", "SELL", 105.4),
    ]
    cancelled = [(hhmm(e["t"]), e["reason"]) for e in res.events if e["kind"] == "order_cancelled"]
    assert cancelled == [("09:22", "position_closed"), ("09:42", "position_closed")]
    unfilled = [(hhmm(e["t"]), e["reason"]) for e in res.events if e["kind"] == "unfilled"]
    assert unfilled == [("09:45", "no_next_bar")]


def test_6_totals_match_the_hand_calculation() -> None:
    m = golden().metrics
    assert m["trades"] == 3 and m["wins"] == 1 and m["losses"] == 2
    assert m["net_pnl"] == 5.0
    assert m["win_rate"] == 0.3333
    assert m["avg_win"] == 52.0 and m["avg_loss"] == -23.5
    assert m["expectancy"] == 1.6667
    assert m["profit_factor"] == 1.1064  # 52 / 47
    assert m["max_drawdown"] == 47.0  # equity 0 -> -5 -> -47 -> +5
    assert m["longest_losing_streak"] == 2
    assert m["pnl_by_weekday"] == {"Mon": {"trades": 3, "net_pnl": 5.0}}
    assert m["pnl_by_time_of_day"] == {"09:00": {"trades": 2, "net_pnl": -47.0}, "09:30": {"trades": 1, "net_pnl": 52.0}}


def test_6_counters() -> None:
    c = golden().counters
    assert (c["unfilled"], c["gaps"], c["ambiguous"]) == (1, 1, 0)
