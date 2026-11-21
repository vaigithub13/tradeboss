"""Phase B: instrument modes, equity/futures costs, risk sizing, Nifty options regression."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtest.catalog import parse_config, RunRequestError
from app.backtest.instruments import cost_model_for, normalize_mode
from app.backtest.sizing import RiskSizing, SizeResult, stop_distance_from_signal
from app.strategies.price_action import PriceAction


def test_normalize_options_alias() -> None:
    assert normalize_mode("options") == "index_option"
    assert normalize_mode("equity_intraday") == "equity_intraday"


def test_parse_config_accepts_equity_intraday_and_keeps_options() -> None:
    eq = parse_config(
        {
            "strategy": "dd_five_confirm",
            "params": {"mode": "long_only"},
            "symbol": "HDFCBANK",
            "timeframe": "5m",
            "start": "2022-01-03",
            "end": "2022-06-30",
            "sessions": ["normal"],
            "mode": "equity_intraday",
        }
    )
    assert eq["mode"] == "equity_intraday"
    assert eq["risk_rupees"] == 300.0
    assert eq["notional_cap"] == 75_000.0
    assert eq["lot_size"] == 1

    opt = parse_config(
        {
            "strategy": "dd_five_confirm",
            "params": {"mode": "long_only"},
            "symbol": "NIFTY50",
            "timeframe": "3m",
            "start": "2022-01-03",
            "end": "2022-06-30",
            "sessions": ["normal"],
            "mode": "options",
            "slippage_points": 0.2,
        }
    )
    assert opt["mode"] == "options"
    assert "risk_rupees" not in opt


def test_parse_config_rejects_unknown_mode() -> None:
    with pytest.raises(RunRequestError):
        parse_config(
            {
                "strategy": "dd_five_confirm",
                "params": {},
                "symbol": "NIFTY50",
                "timeframe": "5m",
                "start": "2022-01-03",
                "end": "2022-06-30",
                "sessions": ["normal"],
                "mode": "crypto",
            }
        )


def test_equity_intraday_cost_row_charges_brokerage_stt_on_sell() -> None:
    model = cost_model_for("equity_intraday")
    buy = model.leg_cost("BUY", 1000.0, 10, date(2024, 10, 15))
    sell = model.leg_cost("SELL", 1000.0, 10, date(2024, 10, 15))
    assert buy.components["stt"] == Decimal("0.00")
    assert sell.components["stt"] > 0
    assert buy.components["brokerage"] > 0
    assert buy.components["stamp"] > 0
    assert sell.components["stamp"] == Decimal("0.00")


def test_futures_cost_table_loads() -> None:
    model = cost_model_for("index_future")
    leg = model.leg_cost("SELL", 50000.0, 30, date(2025, 1, 15))
    assert leg.total > 0
    assert leg.components["stt"] > 0


def test_risk_sizing_caps_notional_instead_of_skipping() -> None:
    eq = RiskSizing(risk_rupees=300, notional_cap=75_000, lot_size=1)
    r = eq.size(stop_distance=10.0, entry_price=1500.0)
    assert r.skip_reason is None and r.qty_units == 30 and not r.capped  # floor(300/10)
    # Would be 30 shares @ 3000 = 90k > 75k → cap at floor(75000/3000)=25
    r2 = eq.size(stop_distance=10.0, entry_price=3000.0)
    assert r2.skip_reason is None and r2.qty_units == 25 and r2.capped
    assert r2.actual_risk == pytest.approx(250.0)  # below target 300
    # Price alone above cap → qty < 1
    r3 = eq.size(stop_distance=10.0, entry_price=80_000.0)
    assert r3.qty_units == 0 and r3.skip_reason == "qty_below_1"

    fut = RiskSizing(risk_rupees=300, notional_cap=200_000, lot_size=30)
    r4 = fut.size(stop_distance=5.0, entry_price=500.0)
    # floor(300/5)=60 → 2 lots × 30 = 60
    assert r4.skip_reason is None and r4.qty_units == 60


def test_stop_distance_helpers() -> None:
    assert stop_distance_from_signal(entry=100.0, stop=95.0, stop_points=None) == 5.0
    assert stop_distance_from_signal(entry=100.0, stop=None, stop_points=7.0) == 7.0
    assert stop_distance_from_signal(entry=100.0, stop=None, stop_points=None) is None


def test_price_action_defaults_still_points_for_nifty_parity() -> None:
    pa = PriceAction(pattern="morning_star")
    assert pa.level_pts == 10.0
    assert pa.max_width == 150.0
    assert pa.level_atr is None and pa.level_pct is None
    assert pa._level_tol(22000.0, atr=100.0) == 10.0
    pa2 = PriceAction(pattern="morning_star", level_atr=0.15, max_width_atr=2.0)
    assert pa2._level_tol(1500.0, atr=20.0) == pytest.approx(3.0)
    assert pa2._width_cap(1500.0, atr=20.0) == pytest.approx(40.0)


def test_nifty_options_dd_five_regression_trade_count(tmp_path: Path) -> None:
    """Defaults + options mode must still run; trade count frozen for a short known window.

    Uses real NIFTY50 candles when present; otherwise skips. Does not touch paper slots.
    """
    from app.config import settings
    from app.data.store import CandleStore
    from app.backtest.execute import execute_run

    store = CandleStore(settings.candles_dir)
    if "NIFTY50" not in store.symbols():
        pytest.skip("NIFTY50 candles absent")
    cfg = parse_config(
        {
            "strategy": "dd_five_confirm",
            "params": {
                "mode": "long_only",
                "use_wr": True,
                "max_trades_per_day": 10,
                "last_entry": "15:10",
            },
            "symbol": "NIFTY50",
            "timeframe": "3m",
            "start": "2024-01-02",
            "end": "2024-03-28",
            "sessions": ["normal"],
            "mode": "options",
            "strike_offset": 0,
            "slippage_points": 0.2,
            "strike_premium": 250,
            "exit_rule": "premium_trail_10",
            "live_timing": True,
        }
    )
    out = execute_run(cfg, lambda _e: None, hash_data=False)
    trades = out["result"]["trades"]
    # Frozen on this checkout: mechanical regression — count must stay stable across Phase B.
    assert len(trades) == 27, f"Nifty options dd_five trade count changed: {len(trades)}"
    assert out["result"]["summary"]["option"]["net_pnl"] == pytest.approx(10826.42, abs=0.02)
