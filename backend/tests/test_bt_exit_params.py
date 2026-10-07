"""Target/stop variants reach the runs: Log XZ's index-point target/stop, and option-premium exits on the overlay."""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.catalog import RunRequestError, build_strategy, parse_config, strategy_catalog
from app.backtest.execute import _model
from app.backtest.walkforward import _expand_grid, child_backtest_config

BASE = {"strategy": "log_xz", "symbol": "NIFTY50", "timeframe": "5m", "start": "2024-10-03", "end": "2026-06-30",
        "mode": "options", "slippage_points": 0.2, "sessions": ["normal", "weekend_full"]}


def test_log_xz_exposes_its_index_point_target_and_stop() -> None:
    params = next(s for s in strategy_catalog() if s["name"] == "log_xz")["params"]
    assert params["use_target"] == {"type": "bool", "default": False}
    assert params["target_points"]["default"] == 10.0 and params["stop_points"]["default"] == 7.0
    cfg = parse_config({**BASE, "params": {"use_target": True, "use_stop": True, "target_points": 30, "stop_points": 20}})
    s = build_strategy(cfg)
    assert (s.use_target, s.use_stop, s.target_points, s.stop_points) == (True, True, 30.0, 20.0)
    with pytest.raises(RunRequestError):
        parse_config({**BASE, "params": {"use_target": "yes"}})


def test_premium_exits_are_run_settings_that_reach_the_option_model() -> None:
    cfg = parse_config({**BASE, "premium_target_pct": 0.3, "premium_stop_pct": 0.2})
    model = _model(cfg)
    assert (model.premium_target_pct, model.premium_stop_pct) == (0.3, 0.2)
    assert _model(parse_config(BASE)).premium_target_pct is None
    with pytest.raises(RunRequestError):
        parse_config({**BASE, "premium_stop_pct": 1.5})


def test_a_walk_forward_grid_carries_premium_exits_to_the_child_run_not_the_strategy() -> None:
    grid = [{"premium_target_pct": 0.5, "premium_stop_pct": 0.3},
            {"use_target": True, "use_stop": True, "target_points": 50.0, "stop_points": 30.0}]
    combos = _expand_grid("log_xz", grid, "5m", 50)
    assert len(combos) == 2 and combos[0]["premium_target_pct"] == 0.5
    wf = {"strategy": "log_xz", "symbol": "NIFTY50", "timeframe": "5m", "sessions": ["normal"],
          "strike_offset": 0, "slippage_points": 0.2, "option_fill": "delta_adjusted"}
    child = child_backtest_config(wf, combos[0], date(2025, 1, 1), date(2025, 6, 30))
    assert child["premium_target_pct"] == 0.5 and child["premium_stop_pct"] == 0.3
    assert "premium_target_pct" not in child["params"]
    build_strategy(child)  # the strategy never sees them
    other = child_backtest_config(wf, combos[1], date(2025, 1, 1), date(2025, 6, 30))
    assert other["params"]["target_points"] == 50.0 and "premium_target_pct" not in other


def test_the_walk_forward_evaluates_premium_combinations_in_every_window() -> None:
    """7 Oct research: the run-time check dropped the premium combos silently (77 of 105 tried)."""
    from app.backtest.walkforward import run_walk_forward

    grid = _expand_grid("log_xz", [{}, {"premium_target_pct": 0.3, "premium_stop_pct": 0.2}], "5m", 50)
    seen: list[dict] = []

    def evaluate(params, start, end):
        seen.append(dict(params))
        return {"net_pnl": -1.0, "max_drawdown": 1.0, "trades": 50, "pnls": [-1.0]}

    spec = {"strategy": "log_xz", "symbol": "NIFTY50", "timeframe": "5m", "sessions": ("normal",), "mode": "options",
            "strike_offset": 0, "slippage_points": 0.2, "start": date(2024, 10, 3), "research_end": date(2025, 6, 30),
            "train_months": 6, "test_months": 2, "step_months": 2, "min_trades": 30, "max_combinations": 50,
            "include_forward": False, "grid": grid}
    result = run_walk_forward(spec, evaluate, peeks=0)
    windows = len(result["windows"])
    assert result["combinations_tried"] == 2 * windows
    assert sum(1 for p in seen if p.get("premium_target_pct") == 0.3) == windows
