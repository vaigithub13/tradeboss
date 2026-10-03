"""SQLite store for backtest runs. One file, `data/backtests.sqlite`."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
DIRTY_WARNING = "code not committed: may not reproduce"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    run_id TEXT,
    overlay_run_id TEXT,
    model_version TEXT,
    cost_rows_json TEXT NOT NULL,
    data_hash TEXT,
    git_commit TEXT NOT NULL,
    git_dirty INTEGER NOT NULL,
    result_json TEXT,
    warnings_json TEXT NOT NULL,
    error TEXT,
    progress_json TEXT NOT NULL,
    reproduced INTEGER,
    parent_id TEXT
)
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _loads(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


@dataclass(frozen=True)
class GitState:
    commit: str
    dirty: bool


class RunNotFound(KeyError):
    pass


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)
        self._db.commit()

    def create(self, config: dict[str, Any], git: GitState, parent_id: str | None = None) -> str:
        run_id = uuid.uuid4().hex
        now = datetime.now(IST).isoformat(timespec="seconds")
        with self._lock:
            self._db.execute(
                """INSERT INTO runs (
                    id, created_at, status, config_json, cost_rows_json, git_commit, git_dirty,
                    warnings_json, progress_json, parent_id
                ) VALUES (?, ?, 'queued', ?, '[]', ?, ?, '[]', ?, ?)""",
                (run_id, now, _dumps(config), git.commit, int(git.dirty), _dumps({"phase": "index", "done": 0, "total": 0}), parent_id),
            )
            self._db.commit()
        return run_id

    def mark_running(self, run_id: str) -> None:
        self._status(run_id, "running")

    def progress(self, run_id: str, progress: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET progress_json = ? WHERE id = ?", (_dumps(progress), run_id))
            self._db.commit()

    def finish(
        self,
        run_id: str,
        payload: dict[str, Any],
        *,
        warnings: list[str] | None = None,
        reproduced: bool | None = None,
    ) -> None:
        noted = list(payload["warnings"] if warnings is None else warnings)
        with self._lock:
            self._db.execute(
                """UPDATE runs SET status = 'done', run_id = ?, overlay_run_id = ?, model_version = ?,
                   cost_rows_json = ?, data_hash = ?, result_json = ?, warnings_json = ?, error = NULL,
                   reproduced = ? WHERE id = ?""",
                (
                    payload.get("run_id"),
                    payload.get("overlay_run_id"),
                    payload.get("model_version"),
                    _dumps(payload.get("cost_rows", [])),
                    payload.get("data_hash"),
                    _dumps(payload.get("result")),
                    _dumps(noted),
                    None if reproduced is None else int(reproduced),
                    run_id,
                ),
            )
            self._db.commit()

    def fail(self, run_id: str, message: str) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET status = 'failed', error = ? WHERE id = ?", (message, run_id))
            self._db.commit()

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(run_id)
        return _row_dict(row)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM runs ORDER BY created_at DESC, id DESC").fetchall()
        return [_row_dict(row) for row in rows]

    def _status(self, run_id: str, status: str) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
            self._db.commit()


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    reproduced = row["reproduced"]
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "config": _loads(row["config_json"], {}),
        "run_id": row["run_id"],
        "overlay_run_id": row["overlay_run_id"],
        "model_version": row["model_version"],
        "cost_rows": _loads(row["cost_rows_json"], []),
        "data_hash": row["data_hash"],
        "git_commit": row["git_commit"],
        "git_dirty": bool(row["git_dirty"]),
        "result": _loads(row["result_json"], None),
        "warnings": _loads(row["warnings_json"], []),
        "error": row["error"],
        "progress": _loads(row["progress_json"], {}),
        "reproduced": None if reproduced is None else bool(reproduced),
        "parent_id": row["parent_id"],
    }
