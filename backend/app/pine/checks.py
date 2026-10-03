"""What a saved user strategy is checked with. The holdout is never run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.sources import ListSource
from app.options.model import OPTIMISTIC_FILL_WARNING
from app.pine.isolated import IsolatedStrategy, WorkerTimeout
from app.pine.report import TV_PARITY_UNVERIFIED

CHECK_START = "2026-04-01"
CHECK_END = "2026-06-30"


def realistic_options_config(strategy: str) -> dict[str, Any]:
    """The automatic realistic run: delta-adjusted fills, one point of slippage."""
    return {
        "strategy": strategy,
        "symbol": "NIFTY50",
        "timeframe": "5m",
        "start": CHECK_START,
        "end": CHECK_END,
        "sessions": ["normal", "weekend_full"],
        "mode": "options",
        "strike_offset": 0,
        "slippage_points": 1.0,
        "option_fill": "delta_adjusted",
    }


def planned_checks(include_walk_forward: bool) -> list[str]:
    checks = ["look_ahead", "determinism", "tv_parity", "realistic"]
    if include_walk_forward:
        checks.append("walk_forward")
    return checks


def summary_card(
    *,
    cost_warnings: list[str],
    option_fill: str,
    overnight_net: float,
    same_day_net: float,
    stop_na: bool,
    include_walk_forward: bool,
) -> dict[str, Any]:
    from app.backtest.execute import _peek_count

    warnings = list(cost_warnings)
    warnings.append("holdout not run")
    if option_fill == "optimistic":
        warnings.append(OPTIMISTIC_FILL_WARNING)
    if stop_na:
        warnings.append(TV_PARITY_UNVERIFIED)
    return {
        "warnings": warnings,
        "option_fill": option_fill,
        "overnight_net": overnight_net,
        "same_day_net": same_day_net,
        "checks": planned_checks(include_walk_forward),
        "holdout": "not run",
        "holdout_peeks": _peek_count(),
    }


def run_smoke(path: Path, bars: list[dict[str, Any]], *, call_timeout: float) -> dict[str, str]:
    strategy = IsolatedStrategy(path, call_timeout=call_timeout)
    try:
        run_backtest(
            strategy, ListSource(bars, base_minutes=5, symbol="NIFTY50"),
            BacktestConfig(timeframe="5m", lot_size=1),
        )
    except WorkerTimeout:
        return {"status": "failed"}
    finally:
        strategy.close()
    return {"status": "done"}
