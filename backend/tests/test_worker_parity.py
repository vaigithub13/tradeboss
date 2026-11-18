"""The hand port inside the worker must match the in-process engine: on the committed paper day always, and on the
six Price Channel research runs when data/ holds the history."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.backtest.catalog import build_strategy
from app.backtest.execute import execute_run
from app.backtest import execute
from app.backtest.holdout import load_holdout
from app.backtest.runs import RunStore
from app.config import settings
from app.pine.isolated import IsolatedStrategy

HAND = Path(__file__).resolve().parents[1] / "app" / "strategies" / "price_channel.py"
RUNS = (
    ("2026-04-01", "2026-06-30", "5m", 20),
    ("2024-10-03", "2026-06-30", "5m", 20),
    ("2026-04-01", "2026-06-30", "15m", 20),
    ("2026-04-01", "2026-06-30", "15m", 40),
    ("2024-10-03", "2026-06-30", "15m", 20),
    ("2024-10-03", "2026-06-30", "15m", 40),
)


def _config(start: str, end: str, timeframe: str, length: int, strategy: str) -> dict:
    return {
        "strategy": strategy,
        "params": {"length": length, "execution": "realistic", "lots": 1},
        "symbol": "NIFTY50",
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "sessions": ["normal", "weekend_full"],
        "mode": "options",
        "strike_offset": 0,
        "slippage_points": 1.0,
        "option_fill": "delta_adjusted",
    }


def _fingerprint(payload: dict) -> tuple:
    view = payload["result"]
    rows = []
    for row in view["trades"]:
        option = row.get("option") or {}
        rows.append((
            row["direction"], row["entry_time"], row["exit_time"],
            row["entry_price"], row["exit_price"], row["lots"], row["net_pnl"],
            option.get("net_pnl"), option.get("entry_premium"), option.get("exit_premium"),
        ))
    option = view["summary"]["option"]
    return (
        view["summary"]["index"]["net_pnl"],
        view["summary"]["index"]["trades"],
        None if option is None else option["net_pnl"],
        tuple(rows),
    )


def test_the_hand_port_in_the_worker_matches_in_process_on_the_committed_day() -> None:
    """tests/fixtures/paper_day (9 Oct 2026): the same trades, prices and option nets in the worker as in process."""
    from app.backtest.engine import run_backtest
    from app.backtest.execute import _engine_config, _load_underlyings, _model
    from app.backtest.sources import StoreSource
    from app.data.store import CandleStore
    from app.options.history import OptionHistoryStore
    from app.options.model import overlay_options

    fixture = Path(__file__).parent / "fixtures" / "paper_day"
    store, tape = CandleStore(fixture / "candles"), OptionHistoryStore(fixture / "options")
    day = date(2026, 10, 9).isoformat()

    def run(strategy) -> tuple:  # noqa: ANN001
        config = {**_config(day, day, "5m", 20, "price_channel"), "slippage_points": 0.2}
        result = run_backtest(strategy, StoreSource(store, "NIFTY50"), _engine_config(config))
        nifty, vix = _load_underlyings(config, store=store)
        option = overlay_options(result, nifty, vix, config=_model(config), tape=tape)
        trades = [(t.direction, t.entry_time, t.exit_time, t.entry_price, t.exit_price, t.net_pnl) for t in result.trades]
        nets = [(row["contract"]["symbol"], row["entry_fill"], row["exit_fill"], row["net_pnl"])
                for row in option.option.trades]
        return trades, nets

    params = {"length": 20, "execution": "realistic", "lots": 1}
    hand = run(build_strategy({"strategy": "price_channel", "params": params}))
    worker = run(IsolatedStrategy(HAND, **params))
    assert hand == worker and hand[0]


@pytest.mark.skipif(not (settings.candles_dir / "NIFTY50" / "1m.parquet").exists(),
                    reason="no stored NIFTY 1m history in data/ (the committed-day test above still runs)")
def test_the_hand_port_in_the_worker_matches_in_process_on_six_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Parity on research windows. Peek audit uses a temp sqlite — never data/backtests.sqlite."""
    store = RunStore(tmp_path / "backtests.sqlite")
    monkeypatch.setattr(execute, "RunStore", lambda _path: store)
    start, end = load_holdout()
    before = store.holdout_peeks(start.isoformat(), end.isoformat())
    assert before == 0

    real = build_strategy

    def routed(config: dict):
        if config["strategy"] == "worker":
            return IsolatedStrategy(HAND, **dict(config.get("params") or {}))
        return real(config)

    execute.build_strategy = routed
    try:
        for start_s, end_s, timeframe, length in RUNS:
            label = f"{start_s}..{end_s} {timeframe} L{length}"
            hand = execute_run(_config(start_s, end_s, timeframe, length, "price_channel"), lambda _p: None, hash_data=False)
            worker = execute_run(_config(start_s, end_s, timeframe, length, "worker"), lambda _p: None, hash_data=False)
            assert _fingerprint(worker) == _fingerprint(hand), label
    finally:
        execute.build_strategy = real
    assert store.holdout_peeks(start.isoformat(), end.isoformat()) == before == 0
