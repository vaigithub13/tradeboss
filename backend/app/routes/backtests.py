"""Backtest jobs: start, poll, list, open, replay."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.backtest.catalog import RunRequestError, strategy_catalog
from app.backtest.execute import execute_run
from app.backtest.gitstate import read_git_state
from app.backtest.jobs import JobBusy, JobService
from app.backtest.runs import RunNotFound, RunStore
from app.config import settings

router = APIRouter(prefix="/api")

_jobs: JobService | None = None


def default_jobs() -> JobService:
    store = RunStore(settings.data_dir / "backtests.sqlite")
    return JobService(store, execute=execute_run, git_reader=read_git_state)


def get_jobs() -> JobService:
    global _jobs
    if _jobs is None:
        _jobs = default_jobs()
    return _jobs


def _public(row: dict[str, Any], *, full: bool) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "config": row["config"],
        "run_id": row["run_id"],
        "overlay_run_id": row["overlay_run_id"],
        "model_version": row["model_version"],
        "cost_rows": row["cost_rows"],
        "data_hash": row["data_hash"],
        "git_commit": row["git_commit"],
        "git_dirty": row["git_dirty"],
        "warnings": row["warnings"],
        "error": row["error"],
        "progress": row["progress"],
        "reproduced": row["reproduced"],
        "parent_id": row["parent_id"],
    }
    if full:
        out["result"] = row["result"]
    else:
        summary = (row["result"] or {}).get("summary") if isinstance(row["result"], dict) else None
        out["summary"] = summary
    return out


@router.get("/backtests/strategies")
def list_strategies() -> dict[str, Any]:
    return {"strategies": strategy_catalog()}


@router.post("/backtests", status_code=202)
def start_backtest(body: dict[str, Any], jobs: JobService = Depends(get_jobs)) -> dict[str, str]:
    try:
        job_id = jobs.start(body)
    except RunRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job_id}


@router.get("/backtests")
def list_backtests(jobs: JobService = Depends(get_jobs)) -> dict[str, Any]:
    return {"runs": [_public(row, full=False) for row in jobs.store.list_runs()]}


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str, jobs: JobService = Depends(get_jobs)) -> dict[str, Any]:
    try:
        return _public(jobs.store.get(run_id), full=True)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/backtests/{run_id}/replay")
def replay_backtest(run_id: str, jobs: JobService = Depends(get_jobs)) -> dict[str, Any]:
    try:
        return _public(jobs.replay(run_id), full=True)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
