"""Every 5 minutes in market hours: read the option chain and append a GEX snapshot.
After the close: run the GEX study (scripts/gex_report.py) once, in a child process so the
backtests never share the GIL with the live engine.

Blocking work runs in worker threads or a subprocess; the asyncio loop only schedules.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time as _time
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.options.time import IST

log = logging.getLogger(__name__)

FIRST_SLOT = time(9, 15)
LAST_SLOT = time(15, 30)
SLOT_MINUTES = 5
#: a slot missed by more than this (sleep, restart) is skipped, not taken late
SLOT_GRACE = timedelta(minutes=3)
REPORT_AT = time(15, 40)
REPORT_UNTIL = time(23, 0)
TICK_SECONDS = 15
REPORT_TIMEOUT_S = 30 * 60
BACKEND_DIR = Path(__file__).resolve().parents[2]


def due_slot(now: datetime, last_slot: datetime | None) -> datetime | None:
    """The 5-minute slot to record now (09:15, 09:20, ... 15:30 IST), or None."""
    local = now.astimezone(IST)
    day_start = local.replace(hour=FIRST_SLOT.hour, minute=FIRST_SLOT.minute, second=0, microsecond=0)
    day_end = local.replace(hour=LAST_SLOT.hour, minute=LAST_SLOT.minute, second=0, microsecond=0)
    if local < day_start or local > day_end + SLOT_GRACE:
        return None
    steps = int((min(local, day_end) - day_start).total_seconds() // (SLOT_MINUTES * 60))
    slot = day_start + timedelta(minutes=SLOT_MINUTES * steps)
    if local - slot > SLOT_GRACE:
        return None
    if last_slot is not None and slot <= last_slot:
        return None
    return slot


def report_due(now: datetime, last_report_day: date | None, has_snapshots: bool) -> bool:
    local = now.astimezone(IST)
    if not has_snapshots or last_report_day == local.date():
        return False
    return REPORT_AT <= local.time() <= REPORT_UNTIL


class GexScheduler:
    def __init__(
        self,
        *,
        record: Callable[[], bool],
        has_snapshots: Callable[[date], bool],
        is_session: Callable[[date], bool],
        clock: Callable[[], float] = _time.time,
    ) -> None:
        self._record = record
        self._has_snapshots = has_snapshots
        self._is_session = is_session
        self._clock = clock
        self._last_slot: datetime | None = None
        self._last_report: date | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="gex-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def tick(self) -> None:
        now = datetime.fromtimestamp(self._clock(), IST)
        if not self._is_session(now.date()):
            return
        slot = due_slot(now, self._last_slot)
        if slot is not None:
            self._last_slot = slot
            try:
                ok = await asyncio.to_thread(self._record)
                log.info("gex snapshot %s: %s", slot.strftime("%H:%M"), "saved" if ok else "skipped")
            except Exception as e:  # never let one bad read stop the recorder
                log.warning("gex snapshot %s failed: %s", slot.strftime("%H:%M"), type(e).__name__)
        if report_due(now, self._last_report, self._has_snapshots(now.date())):
            self._last_report = now.date()
            await self._run_report(now.date())

    async def _run_report(self, day: date) -> None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "scripts.gex_report", "--day", day.isoformat(),
            cwd=str(BACKEND_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=REPORT_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            log.warning("gex report for %s timed out", day)
            return
        tail = (out or b"").decode(errors="replace").strip().splitlines()[-1:] or [""]
        log.info("gex report for %s exited %s: %s", day, proc.returncode, tail[0])

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("gex scheduler tick failed: %s", type(e).__name__)
            await asyncio.sleep(TICK_SECONDS)


def build_scheduler() -> GexScheduler:
    from app.config import settings
    from app.live.session_days import listed_session
    from app.options.gex import compute_gex
    from app.options.gex_history import append_snapshot, snapshot_row
    from app.routes.gex import NIFTY_CHAIN_KEY, default_expiry
    from app.upstox.deps import get_client

    gex_dir = settings.data_dir / "gex"

    def record() -> bool:
        client = get_client()
        if client is None:
            return False
        now = _time.time()
        exp = default_expiry(now)
        rows = client.option_chain(NIFTY_CHAIN_KEY, exp)
        result = compute_gex(rows, now=int(now), expiry=date.fromisoformat(exp))
        if not result["available"]:
            return False
        append_snapshot(gex_dir, snapshot_row(result))
        return True

    def has_snapshots(day: date) -> bool:
        return (gex_dir / f"{day.isoformat()}.jsonl").is_file()

    return GexScheduler(record=record, has_snapshots=has_snapshots, is_session=listed_session)
