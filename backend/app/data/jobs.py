"""Background history-sync jobs (one per instrument at a time), with progress for the UI."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal

from app.data.history import SyncProgress, SyncResult
from app.upstox.client import UpstoxAuthError, UpstoxError
from app.upstox.instruments import Instrument
from app.upstox.redact import redact

log = logging.getLogger(__name__)

JobStatus = Literal["running", "done", "error"]
Runner = Callable[[Instrument, date | None, date | None, Callable[[SyncProgress], None]], SyncResult]


@dataclass
class Job:
    id: str
    instrument_key: str
    symbol: str  # folder name of the stored symbol (what the chart selects)
    status: JobStatus = "running"
    windows_total: int = 0
    windows_done: int = 0
    bars_added: int = 0
    message: str = "queued"
    error: str | None = None
    #: the data token was rejected: the UI should show "data token invalid"
    auth_error: bool = False
    started_at: str = ""
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobManager:
    def __init__(
        self,
        runner: Callable[[], Runner | None],
        symbol_dir_of: Callable[[str], str],
        *,
        threaded: bool = True,
        on_auth_error: Callable[[], None] | None = None,
        max_kept: int = 50,
    ) -> None:
        self._runner = runner
        self._symbol_dir_of = symbol_dir_of
        self._threaded = threaded
        self._on_auth_error = on_auth_error
        self._max_kept = max_kept
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest_for(self, instrument_key: str) -> Job | None:
        with self._lock:
            mine = [j for j in self._jobs.values() if j.instrument_key == instrument_key]
            return mine[-1] if mine else None

    def running(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == "running"]

    def start(self, instrument: Instrument, from_date: date | None, to_date: date | None) -> Job:
        """Start a sync; if one is already running for this instrument, return that one."""
        with self._lock:
            for j in self._jobs.values():
                if j.instrument_key == instrument.key and j.status == "running":
                    return j
            job = Job(
                id=uuid.uuid4().hex[:12],
                instrument_key=instrument.key,
                symbol=self._symbol_dir_of(instrument.key),
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            self._jobs[job.id] = job
            while len(self._jobs) > self._max_kept:
                oldest = next((k for k, v in self._jobs.items() if v.status != "running"), None)
                if oldest is None:
                    break
                del self._jobs[oldest]
        if self._threaded:
            threading.Thread(
                target=self._run, args=(job, instrument, from_date, to_date), daemon=True
            ).start()
        else:
            self._run(job, instrument, from_date, to_date)
        return job

    def _finish(self, job: Job, *, error: str | None = None, auth: bool = False) -> None:
        with self._lock:
            job.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            job.status = "error" if error else "done"
            job.error = error
            job.auth_error = auth
            if error:
                job.message = error

    def _run(self, job: Job, instrument: Instrument, from_date: date | None, to_date: date | None) -> None:
        runner = self._runner()
        if runner is None:
            self._finish(
                job,
                error="No data token. Set UPSTOX_ANALYTICS_TOKEN in .env and restart the backend.",
                auth=True,
            )
            return

        def on_progress(p: SyncProgress) -> None:
            with self._lock:
                job.windows_total, job.windows_done = p.windows_total, p.windows_done
                job.bars_added, job.message = p.bars_added, p.message

        try:
            runner(instrument, from_date, to_date, on_progress)
        except UpstoxAuthError as e:
            if self._on_auth_error:
                self._on_auth_error()
            self._finish(job, error=str(e), auth=True)
        except UpstoxError as e:
            self._finish(job, error=str(e))
        except Exception as e:  # never let a worker thread die silently
            log.exception("history sync failed")
            self._finish(job, error=f"internal error: {type(e).__name__}: {redact(e)}")
        else:
            self._finish(job)
