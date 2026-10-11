"""H2 (review 2026-10-11): the backend refuses research runs and replays that overlap the fixed holdout.

Only the holdout action runs on the holdout dates, and it records a peek before the engine starts.
A blank End means "through the last stored bar", so with a start before the holdout it overlaps too.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.backtest import execute as execute_module
from app.backtest.catalog import RunRequestError, parse_config
from app.backtest.holdout import HoldoutRead, load_holdout, overlaps_holdout
from app.backtest.jobs import JobService
from app.backtest.runs import GitState, RunStore
from app.backtest.walkforward import parse_holdout

HOLDOUT_START, HOLDOUT_END = load_holdout()
DAY = timedelta(days=1)


def _body(start: str, end: str | None) -> dict[str, Any]:
    return {
        "strategy": "ema_crossover", "params": {"fast": 9, "slow": 21}, "symbol": "NIFTY50", "timeframe": "5m",
        "start": start, "end": end, "sessions": ["normal", "weekend_full"], "mode": "index",
    }


@pytest.mark.parametrize(("start", "end", "overlaps"), [
    (HOLDOUT_START - 90 * DAY, HOLDOUT_START - DAY, False),
    (HOLDOUT_START - 90 * DAY, HOLDOUT_START, True),
    (HOLDOUT_START - 90 * DAY, None, True),  # blank End runs through the last stored bar
    (HOLDOUT_END, HOLDOUT_END, True),
    (HOLDOUT_START + 10 * DAY, HOLDOUT_START + 20 * DAY, True),
    (HOLDOUT_END + DAY, None, False),  # the forward period
    (HOLDOUT_END + DAY, HOLDOUT_END + 30 * DAY, False),
])
def test_overlap_rule(start: date, end: date | None, overlaps: bool) -> None:
    assert overlaps_holdout(start, end) is overlaps


def test_a_research_run_overlapping_the_holdout_is_refused() -> None:
    for end in (None, HOLDOUT_START.isoformat(), (HOLDOUT_END + 5 * DAY).isoformat()):
        with pytest.raises(RunRequestError, match="holdout"):
            parse_config(_body((HOLDOUT_START - 90 * DAY).isoformat(), end))


def test_research_and_forward_runs_are_accepted() -> None:
    assert parse_config(_body("2026-01-01", (HOLDOUT_START - DAY).isoformat()))["end"] == (HOLDOUT_START - DAY).isoformat()
    assert parse_config(_body((HOLDOUT_END + DAY).isoformat(), None))["end"] is None


def test_the_holdout_action_still_runs_on_the_holdout_dates() -> None:
    config = parse_holdout({"strategy": "ema_crossover", "params": {"fast": 9, "slow": 21}})
    assert (config["kind"], config["start"], config["end"]) == ("holdout", HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat())


def test_the_job_service_refuses_an_overlapping_run_before_saving_it(tmp_path) -> None:  # noqa: ANN001
    store = RunStore(tmp_path / "runs.sqlite")
    service = JobService(store, execute=lambda c, _r: pytest.fail("must not run"), git_reader=lambda: GitState("abc", False))
    with pytest.raises(RunRequestError, match="holdout"):
        service.start(_body((HOLDOUT_START - 30 * DAY).isoformat(), None))
    assert store.list_runs() == []


def _saved(store: RunStore, config: dict[str, Any]) -> str:
    run_id = store.create(config, GitState("abc", False))
    store.finish(run_id, {"run_id": "x", "warnings": [], "result": {"summary": {}}, "data_hash": "h"}, warnings=[])
    return run_id


def test_a_replay_of_an_old_run_overlapping_the_holdout_is_refused(tmp_path) -> None:  # noqa: ANN001
    store = RunStore(tmp_path / "runs.sqlite")
    service = JobService(store, execute=lambda c, _r: pytest.fail("must not run"), git_reader=lambda: GitState("abc", False))
    old = {**_body("2024-10-03", None), "kind": None}  # saved before the guard existed
    run_id = _saved(store, old)
    with pytest.raises(RunRequestError, match="holdout"):
        service.replay(run_id)


def test_a_replay_of_a_holdout_run_is_refused(tmp_path) -> None:  # noqa: ANN001
    store = RunStore(tmp_path / "runs.sqlite")
    service = JobService(store, execute=lambda c, _r: pytest.fail("must not run"), git_reader=lambda: GitState("abc", False))
    run_id = _saved(store, parse_holdout({"strategy": "ema_crossover", "params": {"fast": 9, "slow": 21}}))
    with pytest.raises(RunRequestError, match="holdout action"):
        service.replay(run_id)


def test_execute_run_refuses_an_overlapping_config_before_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_module, "run_backtest", lambda *a, **k: pytest.fail("engine must not start"))
    config = {**_body("2024-10-03", None), "kind": None}
    with pytest.raises(HoldoutRead):
        execute_module.execute_run(config, lambda _p: None)


def test_the_holdout_action_counts_a_peek_and_runs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    calls: list[dict[str, Any]] = []
    store = RunStore(tmp_path / "runs.sqlite")
    monkeypatch.setattr(execute_module, "RunStore", lambda _path: store)

    def fake_run(config: dict[str, Any], report: Any, **kw: Any) -> dict[str, Any]:
        calls.append(config)
        return {"result": {}, "warnings": []}

    monkeypatch.setattr(execute_module, "_execute_single", fake_run)
    before = store.holdout_peeks(HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat())
    execute_module.execute_run(parse_holdout({"strategy": "ema_crossover", "params": {"fast": 9, "slow": 21}}), lambda _p: None)
    assert store.holdout_peeks(HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat()) == before + 1
    assert calls and calls[0]["start"] == HOLDOUT_START.isoformat()
