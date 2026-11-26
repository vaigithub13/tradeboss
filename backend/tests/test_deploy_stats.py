"""Deployment-check stats: streaks, the worst month, and drawdown length."""

from datetime import datetime, timedelta, timezone

from app.backtest.metrics import TradeRecord
from app.paper.deploy_stats import by_year, longest_drawdown_days, summarise

IST = timezone(timedelta(hours=5, minutes=30))


def _at(day: str, hour: int = 15) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00:00+05:30").timestamp())


def _trade(day: str, net: float, hour: int = 15) -> TradeRecord:
    exit_at = _at(day, hour)
    return TradeRecord(exit_at - 60, exit_at, net)


def test_the_longest_streak_and_the_deepest_streak_can_differ():
    trades = [
        _trade("2024-01-02", -100),
        _trade("2024-01-03", 50),
        _trade("2024-01-04", -10),
        _trade("2024-01-05", -10),
        _trade("2024-01-08", 0),
        _trade("2024-01-09", -5),
    ]
    stats = summarise(trades)
    assert stats["streak"]["longest_trades"] == 2
    assert stats["streak"]["longest_rupees"] == -20
    assert stats["streak"]["deepest_trades"] == 1
    assert stats["streak"]["deepest_rupees"] == -100
    assert stats["wins"] == 1
    assert stats["worst_month"] == {"month": "2024-01", "net": -75.0}


def test_drawdown_days_run_from_the_peak_exit_until_a_new_high():
    trades = [
        _trade("2024-01-02", 100),
        _trade("2024-01-03", -40),
        _trade("2024-01-12", 50),
        _trade("2024-01-15", -10),
    ]
    assert longest_drawdown_days(trades) == 10
    stats = summarise(trades)
    assert stats["dd_days"] == 10
    assert stats["max_dd"] == 40


def test_an_open_drawdown_runs_to_the_last_exit():
    trades = [
        _trade("2024-02-01", 20),
        _trade("2024-02-05", -30),
        _trade("2024-02-20", -5),
    ]
    assert longest_drawdown_days(trades) == 19


def test_years_are_the_exit_year_and_start_their_own_drawdown():
    trades = [
        _trade("2024-12-30", -10),
        _trade("2025-01-02", 30),
        _trade("2025-06-02", -5),
    ]
    years = by_year(trades)
    assert [row["year"] for row in years] == [2024, 2025]
    assert years[0]["trades"] == 1 and years[0]["net"] == -10
    assert years[1]["trades"] == 2 and years[1]["net"] == 25
    assert years[1]["max_dd"] == 5
