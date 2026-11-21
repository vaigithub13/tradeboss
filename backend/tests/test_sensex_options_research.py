"""SENSEX options research wiring: Friday expiry, lot 20, catalog gate (no paper)."""

from __future__ import annotations

from datetime import date

from app.backtest.catalog import OPTIONS_SYMBOLS, RunRequestError, parse_config
from app.backtest.expiry import load_sensex_calendar
from app.backtest.lots import load_default_lot_table, lot_size_for_contract
from app.options.strikes import load_default_step_table
import pytest


def test_sensex_weekly_expiry_is_friday() -> None:
    cal = load_sensex_calendar()
    # A clear Friday week with no holiday: 2026-11-20 is Friday
    exp = cal.weekly_in_week_of(date(2026, 11, 16))  # Monday of that week
    assert exp.date == date(2026, 11, 20)
    assert exp.weekday_rule == "Friday"
    nxt = cal.next_expiry(date(2026, 11, 18), "weekly")
    assert nxt.date == date(2026, 11, 20)


def test_sensex_lot_and_step() -> None:
    lots = load_default_lot_table()
    assert lot_size_for_contract(lots, "SENSEX", date(2026, 11, 20), "weekly") == 20
    assert load_default_step_table().step("SENSEX", date(2026, 11, 20)) == 100


def test_options_mode_allows_sensex_not_random_symbols() -> None:
    assert "SENSEX" in OPTIONS_SYMBOLS
    body = {
        "strategy": "price_channel",
        "params": {"length": 20},
        "symbol": "SENSEX",
        "timeframe": "5m",
        "start": "2024-01-02",
        "end": "2024-01-31",
        "sessions": ["normal"],
        "mode": "options",
        "strike_premium": 800,
        "slippage_points": 0.65,
        "live_timing": True,
        "option_fill": "delta_adjusted",
    }
    cfg = parse_config(body)
    assert cfg["symbol"] == "SENSEX" and cfg["mode"] == "options"
    with pytest.raises(RunRequestError, match="options mode"):
        parse_config({**body, "symbol": "RELIANCE"})
