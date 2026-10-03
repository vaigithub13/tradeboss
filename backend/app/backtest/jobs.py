"""One backtest at a time, off the API thread. Replay uses the stored config."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from app.backtest.catalog import parse_config
from app.backtest.result import canonical
from app.backtest.runs import DIRTY_WARNING, GitState, RunStore

Report = Callable[[dict[str, Any]], None]
Execute = Callable[[dict[str, Any], Report], dict[str, Any]]


class JobBusy(RuntimeError):
    pass


class JobService:
    def __init__(self, store: RunStore, execute: Execute, git_reader: Callable[[], GitState]) -> None:
        self.store = store
        self.execute = execute
        self.git_reader = git_reader
        self._lock = threading.Lock()
        self._active: str | None = None

    def start(self, body: dict[str, Any]) -> str:
        with self._lock:
            if self._active is not None:
                raise JobBusy("a backtest is already running")
            config = parse_config(body)
            run_id = self.store.create(config, self.git_reader())
            self._active = run_id
        threading.Thread(target=self._run, args=(run_id,), daemon=True).start()
        return run_id

    def replay(self, run_id: str) -> dict[str, Any]:
        original = self.store.get(run_id)
        git = self.git_reader()
        payload = self.execute(original["config"], lambda _progress: None)
        same_git = git.commit == original["git_commit"] and not git.dirty and not original["git_dirty"]
        same_data = payload.get("data_hash") == original["data_hash"]
        same_result = canonical(payload.get("result")) == canonical(original["result"])
        reproduced = bool(same_git and same_data and same_result)
        new_id = self.store.create(original["config"], git, parent_id=run_id)
        self.store.finish(new_id, payload, warnings=self._warnings(payload, git), reproduced=reproduced)
        return self.store.get(new_id)

    def _run(self, run_id: str) -> None:
        try:
            self.store.mark_running(run_id)
            row = self.store.get(run_id)
            payload = self.execute(row["config"], lambda progress: self.store.progress(run_id, progress))
            git = GitState(commit=row["git_commit"], dirty=row["git_dirty"])
            self.store.finish(run_id, payload, warnings=self._warnings(payload, git))
        except Exception as exc:  # a failed run is a result, not a crash of the API
            self.store.fail(run_id, f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                if self._active == run_id:
                    self._active = None

    @staticmethod
    def _warnings(payload: dict[str, Any], git: GitState) -> list[str]:
        warnings = [str(item) for item in payload.get("warnings", [])]
        if git.dirty and DIRTY_WARNING not in warnings:
            warnings.append(DIRTY_WARNING)
        return warnings
