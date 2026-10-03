"""Metrics (9): each one checked on a hand-written trade list (all after costs)."""

from __future__ import annotations

from app.backtest.metrics import TradeRecord, compute_metrics
from tests.bt_helpers import FRI, MON, THU, TUE, WED, ist

# (day, hh, mm, net pnl after costs)
LIST = [
    (MON, 9, 20, 100.0),
    (MON, 9, 50, -40.0),
    (TUE, 10, 10, -60.0),
    (TUE, 13, 5, 30.0),
    (WED, 9, 35, -20.0),
    (WED, 14, 10, -10.0),
    (THU, 9, 45, 0.0),  # breakeven: neither a win nor a loss, and it ends a losing streak
    (THU, 11, 20, -50.0),
    (FRI, 9, 25, 70.0),
    (FRI, 15, 0, 10.0),
]


def records(rows=LIST) -> list[TradeRecord]:  # noqa: ANN001
    return [TradeRecord(entry_time=ist(*d, h, m), exit_time=ist(*d, h, m) + 600, net_pnl=p) for d, h, m, p in rows]


def test_9_core_numbers_on_a_known_list() -> None:
    m = compute_metrics(records())
    assert m["trades"] == 10 and m["wins"] == 4 and m["losses"] == 5 and m["breakeven"] == 1
    assert m["net_pnl"] == 30.0
    assert m["gross_profit"] == 210.0 and m["gross_loss"] == -180.0
    assert m["win_rate"] == 0.4
    assert m["avg_win"] == 52.5 and m["avg_loss"] == -36.0
    assert m["expectancy"] == 3.0
    assert m["profit_factor"] == 1.1667  # 210 / 180
    assert m["longest_losing_streak"] == 2  # t2,t3 and t5,t6; the breakeven breaks the run before t8


def test_9_max_drawdown_is_measured_on_closed_trade_equity_from_zero() -> None:
    # equity: 100, 60, 0, 30, 10, 0, 0, -50, 20, 30 -> peak 100, trough -50
    assert compute_metrics(records())["max_drawdown"] == 150.0
    assert compute_metrics(records([(MON, 9, 20, -10.0), (MON, 9, 40, -5.0)]))["max_drawdown"] == 15.0  # the start (0) is a peak
    assert compute_metrics(records([(MON, 9, 20, 10.0), (MON, 9, 40, 5.0)]))["max_drawdown"] == 0.0


def test_9_by_weekday_uses_the_entry_day_in_ist_in_calendar_order() -> None:
    by = compute_metrics(records())["pnl_by_weekday"]
    assert list(by) == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert by == {
        "Mon": {"trades": 2, "net_pnl": 60.0},
        "Tue": {"trades": 2, "net_pnl": -30.0},
        "Wed": {"trades": 2, "net_pnl": -30.0},
        "Thu": {"trades": 2, "net_pnl": -50.0},
        "Fri": {"trades": 2, "net_pnl": 80.0},
    }


def test_9_by_time_of_day_uses_30_minute_clock_buckets_of_the_entry_time() -> None:
    by = compute_metrics(records())["pnl_by_time_of_day"]
    assert by == {
        "09:00": {"trades": 2, "net_pnl": 170.0},  # 09:20, 09:25
        "09:30": {"trades": 3, "net_pnl": -60.0},  # 09:35, 09:45, 09:50
        "10:00": {"trades": 1, "net_pnl": -60.0},
        "11:00": {"trades": 1, "net_pnl": -50.0},
        "13:00": {"trades": 1, "net_pnl": 30.0},
        "14:00": {"trades": 1, "net_pnl": -10.0},
        "15:00": {"trades": 1, "net_pnl": 10.0},
    }
    assert list(by) == sorted(by)


def test_9_no_trades_gives_zeros_and_none_never_a_division_error() -> None:
    m = compute_metrics([])
    assert m["trades"] == 0 and m["net_pnl"] == 0.0 and m["max_drawdown"] == 0.0 and m["longest_losing_streak"] == 0
    assert m["win_rate"] is None and m["avg_win"] is None and m["avg_loss"] is None
    assert m["expectancy"] is None and m["profit_factor"] is None
    assert m["pnl_by_weekday"] == {} and m["pnl_by_time_of_day"] == {}


def test_9_all_winners_have_no_profit_factor_and_all_losers_no_average_win() -> None:
    w = compute_metrics(records([(MON, 9, 20, 10.0), (MON, 9, 40, 20.0)]))
    assert w["win_rate"] == 1.0 and w["profit_factor"] is None and w["avg_loss"] is None and w["longest_losing_streak"] == 0
    lo = compute_metrics(records([(MON, 9, 20, -10.0), (MON, 9, 40, -20.0), (MON, 10, 0, -5.0)]))
    assert lo["win_rate"] == 0.0 and lo["avg_win"] is None and lo["profit_factor"] == 0.0
    assert lo["longest_losing_streak"] == 3 and lo["avg_loss"] == -11.6667


def test_9_trades_are_ordered_by_exit_time_for_streaks_and_drawdown() -> None:
    shuffled = list(reversed(records()))
    assert compute_metrics(shuffled) == compute_metrics(records())


def test_9_money_is_rounded_to_the_paisa() -> None:
    m = compute_metrics(records([(MON, 9, 20, 0.1), (MON, 9, 40, 0.2)]))
    assert m["net_pnl"] == 0.3  # not 0.30000000000000004
