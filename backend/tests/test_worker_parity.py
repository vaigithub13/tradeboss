"""The hand port inside the worker must match the in-process engine on the six Price Channel runs."""

from __future__ import annotations

from pathlib import Path

from app.backtest.catalog import build_strategy
from app.backtest.execute import _peek_count, execute_run
from app.backtest import execute
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


def test_the_hand_port_in_the_worker_matches_in_process_on_six_runs() -> None:
    before = _peek_count()
    real = build_strategy

    def routed(config: dict):
        if config["strategy"] == "worker":
            return IsolatedStrategy(HAND, **dict(config.get("params") or {}))
        return real(config)

    execute.build_strategy = routed
    try:
        for start, end, timeframe, length in RUNS:
            label = f"{start}..{end} {timeframe} L{length}"
            hand = execute_run(_config(start, end, timeframe, length, "price_channel"), lambda _p: None, hash_data=False)
            worker = execute_run(_config(start, end, timeframe, length, "worker"), lambda _p: None, hash_data=False)
            assert _fingerprint(worker) == _fingerprint(hand), label
    finally:
        execute.build_strategy = real
    assert _peek_count() == before == 0
