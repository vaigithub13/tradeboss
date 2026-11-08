"""Every 5 minutes in market hours: save the full option chain for nearest AND next expiry.

Independent of which expiry the Options tab is showing. Same slot rules as the GEX recorder
(09:15 … 15:30 IST, 3-minute grace, regular trading days only).
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.options.time import IST

log = logging.getLogger(__name__)

FIRST_SLOT = time(9, 15)
LAST_SLOT = time(15, 30)
SLOT_MINUTES = 5
SLOT_GRACE = timedelta(minutes=3)
TICK_SECONDS = 15


def due_slot(now: datetime, last_slot: datetime | None) -> datetime | None:
    """The 5-minute slot to record now (09:15 … 15:30 IST), or None."""
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


class ChainScheduler:
    def __init__(
        self,
        *,
        record: Callable[[], bool],
        is_session: Callable[[date], bool],
        clock: Callable[[], float] = _time.time,
    ) -> None:
        self._record = record
        self._is_session = is_session
        self._clock = clock
        self._last_slot: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="options-chain-scheduler")

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
        if slot is None:
            return
        self._last_slot = slot
        try:
            ok = await asyncio.to_thread(self._record)
            log.info("options snapshot %s: %s", slot.strftime("%H:%M"), "saved" if ok else "skipped")
        except Exception as e:
            log.warning("options snapshot %s failed: %s", slot.strftime("%H:%M"), type(e).__name__)

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("options scheduler tick failed: %s", type(e).__name__)
            await asyncio.sleep(TICK_SECONDS)


def build_scheduler() -> ChainScheduler:
    from app.ai.chain import NIFTY_CHAIN_KEY
    from app.backtest.expiry import load_default_calendar
    from app.config import settings
    from app.options.chain_history import append_snapshot, snapshot_row
    from app.options.chain_view import build_chain_view
    from app.routes.options import default_expiry, next_expiry_after
    from app.upstox.deps import get_client

    options_dir: Path = settings.data_dir / "options"

    def record() -> bool:
        client = get_client()
        if client is None:
            return False
        now = _time.time()
        nearest = default_expiry(now)
        targets = [nearest, next_expiry_after(nearest)]
        saved = False
        for exp in targets:
            rows = client.option_chain(NIFTY_CHAIN_KEY, exp)
            view = build_chain_view(rows, now=int(now), expiry=exp, status="LIVE")
            if not view["available"]:
                continue
            append_snapshot(options_dir, snapshot_row(view))
            saved = True
        return saved

    return ChainScheduler(record=record, is_session=load_default_calendar().is_trading_day)
