"""Four paper strategies on one feed: slots 1-4, each a `PaperRunner` with its own session, position, P&L and day
files. Slot 1 keeps `data/paper/` (earlier days and dry-run marks stay where they were); slot N writes
`data/paper/slotN/`. The feed service talks to the desk as it talked to one runner; the routes pick a slot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from app.data.store import CandleStore
from app.paper.live import PaperError, PaperRunner

SLOTS = ("1", "2", "3", "4")
log = logging.getLogger("tradeboss.paper.desk")


class UnknownSlot(PaperError):
    pass


class PaperDesk:
    def __init__(self, runners: dict[str, PaperRunner]) -> None:
        if tuple(runners) != SLOTS:
            raise ValueError(f"a desk has the slots {SLOTS}")
        self.runners = runners

    @staticmethod
    def slot_dir(base: Path, slot: str) -> Path:
        return base if slot == "1" else base / f"slot{slot}"

    def runner(self, slot: str) -> PaperRunner:
        try:
            return self.runners[slot]
        except KeyError:
            raise UnknownSlot(f"unknown slot {slot!r}: use one of {list(SLOTS)}") from None

    # ------------------------------------------------------------------ the service's view
    @property
    def state(self) -> str:
        return "running" if any(r.state == "running" for r in self.runners.values()) else "stopped"

    @property
    def day(self) -> date | None:
        days = [r.day for r in self.runners.values() if r.day is not None]
        return max(days) if days else None

    def wanted_keys(self) -> set[str]:
        out: set[str] = set()
        for r in self.runners.values():
            out |= r.wanted_keys()
        return out

    def resume_if_running(self, day: date) -> bool:
        resumed = False
        for r in self.runners.values():
            try:
                resumed = r.resume_if_running(day) or resumed
            except PaperError:
                # one slot that cannot resume (a missing snapshot, other settings) must not stop the other
                continue
        return resumed

    def _each(self, what: str, call: Callable[[PaperRunner], Any]) -> None:
        """Run `call` on every slot. A slot that fails is logged and skipped: the other slot and the feed go on."""
        for slot, r in self.runners.items():
            try:
                call(r)
            except Exception:  # noqa: BLE001 - one strategy's failure must not stop the feed or the other slot
                log.exception("paper slot %s failed in %s", slot, what)

    def on_depth(self, raw: bytes) -> None:
        self._each("on_depth", lambda r: r.on_depth(raw))

    def on_clock(self, now_ms: int) -> None:
        self._each("on_clock", lambda r: r.on_clock(now_ms))

    def on_index_tick(self, price: float, *, now_ms: int) -> None:
        self._each("on_index_tick", lambda r: r.on_index_tick(price, now_ms=now_ms))

    def on_option_tick(self, key: str, price: float, *, now_ms: int) -> None:
        self._each("on_option_tick", lambda r: r.on_option_tick(key, price, now_ms=now_ms))

    def on_index_bar(self, bar: dict[str, Any], *, now_ms: int) -> None:
        self._each("on_index_bar", lambda r: r.on_index_bar(bar, now_ms=now_ms))

    def finish_day(self) -> None:
        self._each("finish_day", lambda r: r.finish_day())

    def reconcile_check(self, store: CandleStore, symbol: str, day: date | None = None) -> None:
        self._each("reconcile_check", lambda r: r.reconcile_check(store, symbol, day))

    def flush(self) -> None:
        self._each("flush", lambda r: r.flush())

    def status(self, now_ms: int) -> dict[str, Any]:
        return {"state": self.state, "slots": [{"slot": s, **self.runners[s].status(now_ms)} for s in SLOTS]}
