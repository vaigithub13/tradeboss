"""Clock thread for similar-pattern snapshots. Not the live-feed event loop.

The local clock only decides when to wake. Each snapshot loads bars that had
closed by that clock and appends a line. It does not rewrite an earlier line.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.data.store import CandleStore
from app.similar.predictions import (
    SNAPSHOT_CLOCKS,
    append_prediction,
    clock_at,
    take_snapshots,
    write_review_if_due,
)

log = logging.getLogger("tradeboss.similar")
IST = ZoneInfo("Asia/Kolkata")
GRACE = timedelta(seconds=45)


def predictions_wanted() -> bool:
    """The clock thread stays off under pytest."""
    return "pytest" not in sys.modules and "PYTEST_CURRENTTEST" not in os.environ


def due_clocks(now: datetime) -> list[str]:
    local = now.astimezone(IST)
    if local.weekday() >= 5:
        return []
    ready = []
    for clock in SNAPSHOT_CLOCKS:
        if local >= clock_at(local.date(), clock) + GRACE:
            ready.append(clock)
    return ready


def catch_up(store: CandleStore, root, now: datetime | None = None) -> int:
    """Append any snapshot whose bar has closed and is not in the log yet."""
    moment = (now or datetime.now(IST)).astimezone(IST)
    wrote = 0
    for clock in due_clocks(moment):
        for record in take_snapshots(store, day=moment.date(), clock=clock):
            if append_prediction(root, record):
                wrote += 1
                log.info(
                    "similar snapshot %s %s %s %s",
                    record["symbol"], record["kind"], record["slot"], record["direction"],
                )
    if moment.hour * 60 + moment.minute >= 15 * 60 + 40:
        write_review_if_due(root, store, now=moment)
    return wrote


class PredictionSchedule:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="similar-predictions", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        store = CandleStore(settings.data_dir / "candles")
        root = settings.data_dir / "similar"
        while not self._stop.is_set():
            try:
                catch_up(store, root)
            except Exception:
                log.exception("similar snapshot pass failed")
            self._stop.wait(30)
