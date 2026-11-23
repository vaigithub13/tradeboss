"""Paper slot 9: drive ExpiryStraddle from the one live feed. Places no orders.

The desk stays slots 1-7. This slot writes data/paper/slot9/<day>.json as the book's own to_dict().
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.backtest.expiry import load_default_calendar
from app.backtest.lots import lot_size_for_contract, load_default_lot_table
from app.live.model import ist_ms_of_day
from app.paper.expiry_straddle import EXIT_MS, ExpiryStraddle, app_cost_fn
from app.paper.live import AlreadyRunning, PaperError
from app.upstox.instruments import NIFTY_INDEX_KEY, InstrumentIndex

NAME = "Expiry Straddle 0920"
LOTS = 1


def _book_view(book: ExpiryStraddle) -> dict[str, Any]:
    """What the paper panel shows. Charges are summary(app_cost_fn(day))."""
    summary = book.summary(app_cost_fn(book.day))
    return {
        "status": book.status,
        "spot_at_entry": book.spot_at_entry,
        "strike": book.legs[0].strike if book.legs else None,
        "legs": [
            {
                "kind": leg.kind,
                "entry": leg.entry,
                "stop": leg.stop,
                "exit": leg.exit,
                "reason": leg.reason,
                "entry_ms": leg.entry_ms,
                "exit_ms": leg.exit_ms,
            }
            for leg in book.legs
        ],
        "gross": summary["gross_rs"],
        "costs": summary["costs_rs"],
        "net": summary["net_rs"],
    }


def nifty_weekly_keys(index: InstrumentIndex, expiry: date, strike: int) -> dict[str, str]:
    """The expiring weekly Nifty CE and PE at `strike`. Empty when either leg is missing."""
    found: dict[str, str] = {}
    for inst in index.instruments:
        if inst.kind != "option" or inst.underlying_key != NIFTY_INDEX_KEY:
            continue
        if inst.expiry != expiry or inst.instrument_type not in ("CE", "PE"):
            continue
        if inst.strike is None or abs(float(inst.strike) - strike) > 0.01:
            continue
        found.setdefault(inst.instrument_type, inst.key)
    if "CE" not in found or "PE" not in found:
        return {}
    return found


class ExpiryStraddleSlot:
    SLOT = "9"
    STRATEGY = "expiry_straddle"

    def __init__(
        self,
        directory: Path,
        *,
        instruments: Callable[[], InstrumentIndex | None],
        on_wanted: Callable[[], None] | None = None,
    ) -> None:
        self.directory = directory
        self._instruments = instruments
        self._on_wanted = on_wanted
        self.state = "stopped"
        self.book: ExpiryStraddle | None = None
        self._pending: dict[str, str] = {}
        self._strike: int | None = None
        self._spot = 0.0
        self._last: dict[str, float] = {}
        self._exit_minute: int | None = None
        self._saved: str | None = None

    def start(self, day: date) -> dict[str, Any]:
        """Today's book. A second start while this process is already running is refused."""
        if self.state == "running" and self.book is not None and self.book.day == day.isoformat():
            raise AlreadyRunning("a strategy is already running")
        loaded = self._read(day)
        if loaded is None:
            self.book = self._fresh(day)
            self._reset_quotes()
            self._save()
        else:
            self._adopt(ExpiryStraddle.from_dict(loaded))
        self.state = "running"
        if self.wanted_keys():
            self._notify()
        return self.status_body()

    def resume(self, day: date) -> bool:
        """Load today's file after a process restart. True when ticks should keep coming."""
        loaded = self._read(day)
        if loaded is None:
            return False
        self._adopt(ExpiryStraddle.from_dict(loaded))
        if self.book is not None and self.book.status in ("waiting", "open"):
            self.state = "running"
            if self.wanted_keys():
                self._notify()
            return True
        self.state = "stopped"
        return False

    def flush(self) -> None:
        self._save()

    def on_frame(self, epoch_ms: int, *, spot: float | None, quotes: dict[str, float]) -> None:
        """One feed frame: Nifty spot, then the two option LTPs, then the clock."""
        if self.state != "running" or self.book is None:
            return
        ms = ist_ms_of_day(epoch_ms)
        if spot is not None and spot > 0:
            self._spot = float(spot)
            self._try_enter(ms)
        for key, ltp in quotes.items():
            self._on_quote(key, ltp, ms)
        self._on_clock(ms)

    def wanted_keys(self) -> set[str]:
        """Keys for the one connection. Empty on a non-expiry day and once the book is finished."""
        book = self.book
        if self.state != "running" or book is None or book.status not in ("waiting", "open"):
            return set()
        keys = {key for key in self._pending.values() if key}
        for leg in book.legs:
            if leg.open and leg.key:
                keys.add(leg.key)
        return keys

    def status_body(self, now_ms: int = 0) -> dict[str, Any]:
        book = self.book
        return {
            "slot": self.SLOT,
            "name": NAME,
            "state": self.state,
            "strategy": self.STRATEGY,
            "day": None if book is None else book.day,
            "book_status": None if book is None else book.status,
            "book": None if book is None else _book_view(book),
            "last_bar_ms": now_ms or None,
        }

    def weekly_summary(self, any_day: date) -> dict[str, Any]:
        """ISO week of saved slot-9 files, same shape the other slots' week route returns."""
        monday = any_day - timedelta(days=any_day.weekday())
        days: list[dict[str, Any]] = []
        for offset in range(7):
            d = monday + timedelta(days=offset)
            loaded = self._read(d)
            if loaded is None:
                continue
            view = _book_view(ExpiryStraddle.from_dict(loaded))
            days.append({
                "date": d.isoformat(),
                "status": view["status"],
                "trades": 1 if view["status"] == "done" else 0,
                "net": view["net"],
                "gross": view["gross"],
                "costs": view["costs"],
            })
        totals = {
            "trades": sum(row["trades"] for row in days),
            "net": round(sum(row["net"] for row in days), 2),
            "gross": round(sum(row["gross"] for row in days), 2),
            "costs": round(sum(row["costs"] for row in days), 2),
        }
        year, week, _ = any_day.isocalendar()
        return {"week": f"{year}-W{week:02d}", "days": days, "totals": totals, "exits": {}, "excluded": []}

    def _fresh(self, day: date) -> ExpiryStraddle:
        calendar = load_default_calendar()
        expiry = calendar.next_expiry(day, "weekly").date
        try:
            lot = lot_size_for_contract(load_default_lot_table(), "NIFTY", expiry, "weekly")
        except (LookupError, ValueError) as exc:
            raise PaperError(str(exc)) from exc
        book = ExpiryStraddle(day.isoformat(), is_expiry=calendar.is_expiry_day(day), units=lot * LOTS)
        if book.status == "no_trade":
            book.events.append({"ms": 0, "event": "no_trade", "text": "no trade today (not expiry)"})
        return book

    def _adopt(self, book: ExpiryStraddle) -> None:
        self.book = book
        self._reset_quotes()
        for leg in book.legs:
            if leg.key:
                self._pending[leg.kind] = leg.key
        if book.legs:
            self._strike = book.legs[0].strike
        self._saved = json.dumps(book.to_dict(), sort_keys=True)

    def _reset_quotes(self) -> None:
        self._pending = {}
        self._strike = None
        self._spot = 0.0
        self._last = {}
        self._exit_minute = None

    def _try_enter(self, ms: int) -> None:
        book = self.book
        if book is None or not book.wants_entry(ms) or self._spot <= 0:
            return
        strike = book.strike_for(self._spot)
        if not self._ensure_keys(strike):
            return
        ce = self._last.get("CE", 0.0)
        pe = self._last.get("PE", 0.0)
        if ce <= 0 or pe <= 0:
            return
        if book.enter(ms, self._spot, ce, pe, self._pending.get("CE"), self._pending.get("PE")):
            self._save()
            self._notify()

    def _ensure_keys(self, strike: int) -> bool:
        if self._strike == strike and self._pending.get("CE") and self._pending.get("PE"):
            return True
        index = self._instruments()
        book = self.book
        if index is None or book is None:
            return False
        expiry = load_default_calendar().next_expiry(date.fromisoformat(book.day), "weekly").date
        found = nifty_weekly_keys(index, expiry, strike)
        if not found:
            return False
        if found != self._pending:
            self._last.clear()
            self._pending = found
            self._strike = strike
            self._notify()
        return True

    def _on_quote(self, key: str, ltp: float, ms: int) -> None:
        book = self.book
        kind = self._kind(key)
        if book is None or kind is None or ltp <= 0:
            return
        self._last[kind] = float(ltp)
        if book.status == "open":
            book.on_quote(ms, kind, float(ltp))
            self._save()
            if book.status == "done":
                self._notify()
        elif book.status == "waiting":
            self._try_enter(ms)

    def _on_clock(self, ms: int) -> None:
        book = self.book
        if book is None:
            return
        before = book.status
        book.on_tick(ms)
        if book.status != before:
            self._save()
            if book.status == "missed_entry":
                self._notify()
        self._try_enter(ms)
        self._try_exit(ms)

    def _try_exit(self, ms: int) -> None:
        book = self.book
        if book is None or book.status != "open" or ms < EXIT_MS:
            return
        minute = ms // 60_000
        if minute == self._exit_minute:
            return
        self._exit_minute = minute
        book.close_all(ms, {"CE": self._last.get("CE", 0.0), "PE": self._last.get("PE", 0.0)})
        self._save()
        if book.status == "done":
            self._notify()

    def _kind(self, key: str) -> str | None:
        for kind, pending in self._pending.items():
            if pending == key:
                return kind
        if self.book is not None:
            for leg in self.book.legs:
                if leg.key == key:
                    return leg.kind
        return None

    def _notify(self) -> None:
        if self._on_wanted is not None:
            self._on_wanted()

    def _read(self, day: date) -> dict[str, Any] | None:
        path = self.directory / f"{day.isoformat()}.json"
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PaperError(f"slot 9 day file {path.name} cannot be read") from exc
        if not isinstance(loaded, dict) or loaded.get("day") != day.isoformat():
            raise PaperError(f"slot 9 day file {path.name} is not today's book")
        return loaded

    def _save(self) -> None:
        book = self.book
        if book is None:
            return
        blob = json.dumps(book.to_dict(), sort_keys=True)
        if blob == self._saved:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{book.day}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(blob)
        tmp.replace(path)
        self._saved = blob
