"""Paper strategies on one feed: slots 1-7, each a `PaperRunner` with its own session, position, P&L and day
files. Slot 1 keeps `data/paper/` (earlier days and dry-run marks stay where they were); slot N writes
`data/paper/slotN/`. The feed service talks to the desk as it talked to one runner; the routes pick a slot.
Index bars/ticks are routed by the slot's chart symbol (Nifty vs Sensex); option ticks reach every slot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from app.data.store import CandleStore
from app.paper.live import PaperError, PaperRunner
from app.upstox.instruments import NIFTY_INDEX_KEY, SENSEX_INDEX_KEY

SLOTS = ("1", "2", "3", "4", "5", "6", "7")
log = logging.getLogger("tradeboss.paper.desk")

#: chart symbol → feed index key (a slot only consumes its own index)
INDEX_KEY_FOR_SYMBOL = {"NIFTY50": NIFTY_INDEX_KEY, "SENSEX": SENSEX_INDEX_KEY}


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

    def held_keys(self) -> set[str]:
        """Every slot's held contracts: the feed never refuses these."""
        out: set[str] = set()
        for r in self.runners.values():
            out |= r.held_keys()
        return out

    def atm_keys(self) -> set[str]:
        """Every slot's current ATM call and put."""
        out: set[str] = set()
        for r in self.runners.values():
            out |= r.atm_keys()
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

    def on_index_tick(self, price: float, *, now_ms: int, index_key: str | None = None) -> None:
        self._each("on_index_tick", lambda r: self._if_index(r, index_key, lambda: r.on_index_tick(price, now_ms=now_ms)))

    def on_option_tick(self, key: str, price: float, *, now_ms: int) -> None:
        self._each("on_option_tick", lambda r: r.on_option_tick(key, price, now_ms=now_ms))

    def on_option_minute(self, key: str, bar: dict[str, Any], *, now_ms: int) -> None:
        self._each("on_option_minute", lambda r: r.on_option_minute(key, bar, now_ms=now_ms))

    def on_index_bar(self, bar: dict[str, Any], *, now_ms: int, index_key: str | None = None) -> None:
        self._each("on_index_bar", lambda r: self._if_index(r, index_key, lambda: r.on_index_bar(bar, now_ms=now_ms)))

    @staticmethod
    def _slot_index_key(runner: PaperRunner) -> str:
        symbol = "NIFTY50"
        if runner.session is not None:
            symbol = runner.session.symbol
        elif runner.params:
            symbol = str(runner.params.get("symbol") or "NIFTY50")
        return INDEX_KEY_FOR_SYMBOL.get(symbol, NIFTY_INDEX_KEY)

    def _if_index(self, runner: PaperRunner, index_key: str | None, call: Callable[[], Any]) -> Any:
        """Run `call` when the event is for this slot's index (or when the key is omitted = legacy Nifty-only)."""
        if index_key is None or self._slot_index_key(runner) == index_key:
            return call()
        return None

    def finish_day(self) -> None:
        self._each("finish_day", lambda r: r.finish_day())

    def reconcile_check(
        self,
        store: CandleStore,
        symbol: str | None = None,
        day: date | None = None,
        tape: Any = None,
        *,
        tapes: dict[str, Any] | None = None,
    ) -> None:
        """Per-slot EOD check. `symbol` is the legacy single-symbol path; prefer each runner's own chart symbol.
        `tapes` maps chart symbol → option history store (Sensex usually has none → modelled)."""
        for slot, r in self.runners.items():
            try:
                slot_symbol = symbol
                if slot_symbol is None:
                    if r.session is not None:
                        slot_symbol = r.session.symbol
                    else:
                        slot_symbol = str((r.params or {}).get("symbol") or "NIFTY50")
                slot_tape = tape
                if tapes is not None:
                    slot_tape = tapes.get(slot_symbol)
                r.reconcile_check(store, slot_symbol, day, tape=slot_tape)
            except Exception:  # noqa: BLE001
                log.exception("paper slot %s failed in reconcile_check", slot)

    def flush(self) -> None:
        self._each("flush", lambda r: r.flush())

    def status(self, now_ms: int) -> dict[str, Any]:
        return {"state": self.state, "slots": [{"slot": s, **self.runners[s].status(now_ms)} for s in SLOTS]}
