"""Costed backtest ranking and the Saturday staleness rule."""

from __future__ import annotations

from datetime import date, timedelta

from app.scanner.backtest import backtest_symbol, rank_pairs, summarize
from app.scanner.config import ScannerSettings
from app.scanner.service import _last_saturday
from tests.scanner_bars import bar
from tests.test_scanner_book import _orb_day


def test_backtest_charges_and_ranking() -> None:
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(8)]
    days = [d for d in days if d.weekday() < 5]
    bars = []
    for day in days:
        bars.extend(_orb_day(day))
    settings = ScannerSettings(max_capital_per_trade=10_000_000, max_loss_per_trade=500)
    trades = backtest_symbol("INFY", bars, settings)
    assert trades
    assert all(t["charges"] > 0 for t in trades)
    assert all(t["net"] < t["gross"] for t in trades)
    summary = summarize(trades)
    assert summary["trades"] == len(trades)
    assert summary["net"] == sum(t["net"] for t in trades)
    ranked = rank_pairs({"INFY": bars, "TCS": bars}, settings)
    orb = [row for row in ranked if row["setup"] == "orb15"]
    assert orb and orb[0]["trades"] >= 1
    assert orb[0]["net"] != 0


def test_last_saturday() -> None:
    assert _last_saturday(date(2026, 1, 7)) == date(2026, 1, 3)  # Wednesday -> previous Saturday
    assert _last_saturday(date(2026, 1, 3)) == date(2026, 1, 3)


def test_a_losing_stop_is_a_loss() -> None:
    day = date(2026, 1, 5)
    bars = [
        bar(day, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(day, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
        bar(day, 9, 30, 100.8, 101.8, 100.6, 101.6, 5000),
        bar(day, 9, 35, 101.4, 101.5, 100.2, 100.4, 1000),
    ]
    # volume average needs 20 prior bars
    from tests.scanner_bars import flat_session

    prior = flat_session(date(2026, 1, 2), 100, 1000)
    trades = backtest_symbol("WIPRO", prior + bars, ScannerSettings(max_capital_per_trade=10_000_000))
    assert trades
    assert trades[0]["reason"] == "stop"
    assert trades[0]["r"] == -1
