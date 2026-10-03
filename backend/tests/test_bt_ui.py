"""Phase 3c-a: saved runs, replay, the background job, equity, and breakdowns.

These tests describe the public API. They were written before the implementation.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtest.breakdowns import dte_breakdown, flag_breakdown
from app.backtest.curve import equity_and_drawdown
from app.backtest.jobs import JobBusy, JobService
from app.backtest.metrics import TradeRecord, compute_metrics
from app.backtest.result import canonical
from app.backtest.runs import DIRTY_WARNING, GitState, RunStore
from app.routes.backtests import get_jobs, router

IST = timezone(timedelta(hours=5, minutes=30))


def _ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def _config() -> dict:
    return {
        "strategy": "opening_range_breakout",
        "params": {"range_minutes": 15, "lots": 1},
        "symbol": "NIFTY50",
        "timeframe": "5m",
        "start": "2024-10-03",
        "end": None,
        "sessions": ["normal", "weekend_full"],
        "mode": "options",
        "strike_offset": 0,
        "slippage_points": 0.5,
    }


def _payload(config: dict, *, data_hash: str = "hash-1", net: float = 10.0) -> dict:
    return {
        "run_id": "run-index",
        "overlay_run_id": "run-overlay",
        "model_version": "option model v1",
        "cost_rows": [{"effective_from": "2024-10-01", "brokerage_flat": 20}],
        "data_hash": data_hash,
        "warnings": ["strike step is UNVERIFIED"],
        "result": {"config": config, "net": net, "trades": [{"id": 1, "net_pnl": net}]},
    }


def test_save_and_reload_keeps_the_run_identity(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    config = _config()
    git = GitState(commit="abc123", dirty=False)
    run_id = store.create(config, git)
    store.finish(run_id, _payload(config))
    got = store.get(run_id)
    assert got["config"] == config
    assert got["run_id"] == "run-index"
    assert got["overlay_run_id"] == "run-overlay"
    assert got["model_version"] == "option model v1"
    assert got["cost_rows"] == [{"effective_from": "2024-10-01", "brokerage_flat": 20}]
    assert got["data_hash"] == "hash-1"
    assert got["git_commit"] == "abc123"
    assert got["git_dirty"] is False
    assert got["result"]["net"] == 10.0


def test_replay_matches_when_code_and_data_are_unchanged(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    config = _config()
    git = GitState(commit="abc123", dirty=False)
    service = JobService(store, execute=lambda c, _r: _payload(c), git_reader=lambda: git)
    first = store.create(config, git)
    store.finish(first, _payload(config))
    second = service.replay(first)
    assert second["reproduced"] is True
    assert canonical(second["result"]) == canonical(store.get(first)["result"])
    assert second["git_commit"] == "abc123"
    assert second["data_hash"] == "hash-1"


def test_replay_is_not_a_reproduction_when_git_or_data_changes(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    config = _config()
    original_git = GitState(commit="abc123", dirty=False)
    first = store.create(config, original_git)
    store.finish(first, _payload(config))

    moved = {"git": GitState(commit="def456", dirty=False)}
    service = JobService(store, execute=lambda c, _r: _payload(c), git_reader=lambda: moved["git"])
    other_commit = service.replay(first)
    assert other_commit["reproduced"] is False
    assert other_commit["result"]["net"] == 10.0
    assert other_commit["git_commit"] == "def456"

    def changed_hash(c: dict, _r: object) -> dict:
        return _payload(c, data_hash="hash-2")

    moved["git"] = original_git
    service.execute = changed_hash
    other_data = service.replay(first)
    assert other_data["reproduced"] is False
    assert other_data["data_hash"] == "hash-2"
    assert other_data["result"]["net"] == 10.0


def test_a_dirty_tree_is_warned_on_the_saved_result(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    released = threading.Event()

    def execute(config: dict, report) -> dict:  # noqa: ANN001
        report({"phase": "index", "done": 1, "total": 2})
        assert released.wait(5)
        report({"phase": "overlay", "done": 1, "total": 1})
        return _payload(config)

    service = JobService(store, execute=execute, git_reader=lambda: GitState(commit="abc123", dirty=True))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_jobs] = lambda: service
    client = TestClient(app)

    res = client.post("/api/backtests", json=_config())
    assert res.status_code == 202
    job_id = res.json()["id"]
    # The handler has returned while the engine is still inside execute.
    mid = client.get(f"/api/backtests/{job_id}")
    assert mid.status_code == 200
    assert mid.json()["status"] in ("queued", "running")
    assert mid.json()["progress"]["phase"] == "index"

    again = client.post("/api/backtests", json=_config())
    assert again.status_code == 409

    released.set()
    deadline = datetime.now(IST) + timedelta(seconds=5)
    body: dict = {}
    while datetime.now(IST) < deadline:
        body = client.get(f"/api/backtests/{job_id}").json()
        if body["status"] == "done":
            break
    assert body["status"] == "done"
    assert body["progress"]["phase"] == "overlay"
    assert DIRTY_WARNING in body["warnings"]
    assert body["git_dirty"] is True


def test_options_mode_rejects_other_symbols_and_bad_request_fields(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    service = JobService(
        store,
        execute=lambda c, _r: _payload(c),
        git_reader=lambda: GitState(commit="abc123", dirty=False),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_jobs] = lambda: service
    client = TestClient(app)

    other = _config()
    other["symbol"] = "NSE_EQ_INE002A01018"
    assert client.post("/api/backtests", json=other).status_code == 400

    bad_offset = _config()
    bad_offset["strike_offset"] = 2
    assert client.post("/api/backtests", json=bad_offset).status_code == 400

    unknown = _config()
    unknown["params"] = {"range_minutes": 15, "lots": 1, "nope": 1}
    assert client.post("/api/backtests", json=unknown).status_code == 400

    with pytest.raises(JobBusy):
        service._active = "already"  # noqa: SLF001 — the lock is the public rule
        service.start(_config())


def test_equity_trough_equals_max_drawdown() -> None:
    rows = [
        (2026, 1, 5, 9, 20, 100.0),
        (2026, 1, 5, 9, 50, -40.0),
        (2026, 1, 6, 10, 10, -60.0),
        (2026, 1, 6, 13, 5, 30.0),
        (2026, 1, 7, 9, 35, -20.0),
        (2026, 1, 7, 14, 10, -10.0),
        (2026, 1, 8, 9, 45, 0.0),
        (2026, 1, 8, 11, 20, -50.0),
        (2026, 1, 9, 9, 25, 70.0),
        (2026, 1, 9, 15, 0, 10.0),
    ]
    records = [TradeRecord(_ts(*r[:5]), _ts(*r[:5]) + 600, r[5]) for r in rows]
    curve = equity_and_drawdown(records)
    metrics = compute_metrics(records)
    assert curve[-1]["equity"] == metrics["net_pnl"] == 30.0
    assert max(point["drawdown"] for point in curve) == metrics["max_drawdown"] == 150.0
    assert [point["equity"] for point in curve] == [100.0, 60.0, 0.0, 30.0, 10.0, 0.0, 0.0, -50.0, 20.0, 30.0]


def test_dte_buckets_and_event_flags_sum_to_the_option_net() -> None:
    trades = [
        {"dte": 0, "net_pnl": 10.0, "flags": ["event_day"]},
        {"dte": 1, "net_pnl": -4.0, "flags": []},
        {"dte": 3, "net_pnl": 1.0, "flags": ["reaction_day"]},
        {"dte": 6, "net_pnl": 2.25, "flags": ["event_day", "gap"]},
    ]
    total = 9.25
    by_dte = dte_breakdown(trades)
    assert set(by_dte) == {"0", "1", "3-4", "5+"}
    assert sum(block["net_pnl"] for block in by_dte.values()) == total
    assert by_dte["0"] == {"trades": 1, "net_pnl": 10.0}
    assert by_dte["3-4"] == {"trades": 1, "net_pnl": 1.0}
    assert by_dte["5+"] == {"trades": 1, "net_pnl": 2.25}
    flags = flag_breakdown(trades)
    for name in ("event_day", "reaction_day", "gap", "modelled"):
        block = flags[name]
        assert block["flagged"]["net_pnl"] + block["unflagged"]["net_pnl"] == total
    assert flags["event_day"]["flagged"]["trades"] == 2
    assert flags["gap"]["flagged"]["net_pnl"] == 2.25
    assert flags["modelled"]["flagged"]["trades"] == 0
