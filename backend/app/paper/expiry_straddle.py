"""Expiry-day 09:20 ATM short straddle (paper only, never places an order).

Research: data/validation/strategy_research_2026-11-23/README.md (build PF 1.60, validate PF 2.20, forward PF 2.80).

Rules
  * Runs only on a Nifty weekly expiry day (the caller passes `is_expiry`, from ExpiryCalendar.is_expiry_day).
  * Entry window 09:20:00-09:21:59 IST: ATM strike = round(spot / step) * step; paper-SELL 1 lot ATM CE and
    1 lot ATM PE of the expiring weekly at LTP - slip. A start after 09:21:59 does NOT enter (the research
    entered at 09:20 only), the day is recorded as `missed_entry`.
  * Each leg has its own stop: when the leg's LTP >= entry * 1.30 it is paper-bought back at LTP + slip.
    No re-entry.
  * At 15:15:00 IST every open leg is paper-bought back at LTP + slip.
  * P&L = sum((entry - exit) * units) - costs; costs come from an injected callable (the app passes the
    dated CostTable), so this module has no app imports and is fully unit-testable.

State is plain data (`to_dict` / `from_dict`) so a restart mid-day resumes the same legs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

ENTRY_MS = (9 * 60 + 20) * 60_000          # 09:20:00 IST, ms of day
ENTRY_LAST_MS = (9 * 60 + 21) * 60_000 + 59_999  # entries accepted up to 09:21:59.999
EXIT_MS = (15 * 60 + 15) * 60_000          # 15:15:00 IST
STOP_MULT = 1.30
SLIP_POINTS = 0.5
KINDS = ("CE", "PE")

#: cost(side, price, units) -> rupees for one fill ("BUY" / "SELL")
CostFn = Callable[[str, float, int], float]


def atm_strike(spot: float, step: int = 50) -> int:
    """Nearest strike; a spot exactly halfway rounds up (25125 -> 25150), like round-half-up."""
    if spot <= 0:
        raise ValueError("spot must be > 0")
    return int((spot / step) + 0.5) * step


@dataclass
class Leg:
    kind: str
    strike: int
    key: str | None
    entry: float
    stop: float
    entry_ms: int
    exit: float | None = None
    exit_ms: int | None = None
    reason: str | None = None  # "stop" | "time"

    @property
    def open(self) -> bool:
        return self.exit is None


@dataclass
class ExpiryStraddle:
    day: str                      # ISO date, IST
    is_expiry: bool
    units: int                    # lot size x lots
    step: int = 50
    slip: float = SLIP_POINTS
    stop_mult: float = STOP_MULT
    status: str = "waiting"       # waiting | open | done | no_trade | missed_entry
    spot_at_entry: float | None = None
    legs: list[Leg] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.units <= 0:
            raise ValueError("units must be > 0")
        if not self.is_expiry:
            self.status = "no_trade"

    # ---------------- entry ----------------
    def wants_entry(self, ms_of_day: int) -> bool:
        return self.status == "waiting" and ENTRY_MS <= ms_of_day <= ENTRY_LAST_MS

    def strike_for(self, spot: float) -> int:
        return atm_strike(spot, self.step)

    def on_tick(self, ms_of_day: int) -> None:
        """Call on every clock tick: marks a late start as missed so it never enters after the window."""
        if self.status == "waiting" and ms_of_day > ENTRY_LAST_MS:
            self.status = "missed_entry"
            self._log(ms_of_day, "missed_entry", "started after 09:21:59; research entered at 09:20 only")

    def enter(self, ms_of_day: int, spot: float, ce_ltp: float, pe_ltp: float,
              ce_key: str | None = None, pe_key: str | None = None) -> bool:
        """Sell both legs at LTP - slip. Returns True when the straddle was opened."""
        self.on_tick(ms_of_day)
        if not self.wants_entry(ms_of_day):
            return False
        if ce_ltp <= 0 or pe_ltp <= 0:
            self._log(ms_of_day, "skip_quote", f"bad LTP CE={ce_ltp} PE={pe_ltp}")
            return False
        strike = self.strike_for(spot)
        self.spot_at_entry = spot
        for kind, ltp, key in (("CE", ce_ltp, ce_key), ("PE", pe_ltp, pe_key)):
            fill = max(ltp - self.slip, 0.05)
            leg = Leg(kind, strike, key, round(fill, 2), round(fill * self.stop_mult, 2), ms_of_day)
            self.legs.append(leg)
            self._log(ms_of_day, "sell", f"{strike} {kind} @ {leg.entry} (LTP {ltp}), stop {leg.stop}")
        self.status = "open"
        return True

    # ---------------- management ----------------
    def on_quote(self, ms_of_day: int, kind: str, ltp: float) -> bool:
        """A live LTP for one leg. Returns True if that leg was stopped out."""
        if kind not in KINDS:
            raise ValueError(f"kind must be CE or PE, got {kind!r}")
        if self.status != "open" or ltp <= 0:
            return False
        if ms_of_day >= EXIT_MS:
            self.close_all(ms_of_day, {kind: ltp})
            return False
        leg = self._leg(kind)
        if leg is None or not leg.open or ltp < leg.stop:
            return False
        self._close(leg, ms_of_day, ltp, "stop")
        self._maybe_done()
        return True

    def close_all(self, ms_of_day: int, last: dict[str, float]) -> None:
        """Time exit: buy back every open leg at its last LTP + slip. Call at/after 15:15 with the last LTPs."""
        if self.status != "open" or ms_of_day < EXIT_MS:
            return
        for leg in self.legs:
            if leg.open:
                ltp = last.get(leg.kind)
                if ltp is None or ltp <= 0:
                    continue  # wait for a quote; the caller retries
                self._close(leg, ms_of_day, ltp, "time")
        self._maybe_done()

    # ---------------- results ----------------
    def summary(self, cost: CostFn | None = None) -> dict[str, Any]:
        gross = 0.0; costs = 0.0
        for leg in self.legs:
            if leg.exit is None:
                continue
            gross += (leg.entry - leg.exit) * self.units
            if cost is not None:
                costs += cost("SELL", leg.entry, self.units) + cost("BUY", leg.exit, self.units)
        return {
            "day": self.day, "status": self.status, "spot_at_entry": self.spot_at_entry,
            "strike": self.legs[0].strike if self.legs else None,
            "legs": [asdict(l) for l in self.legs],
            "stops_hit": sum(1 for l in self.legs if l.reason == "stop"),
            "gross_rs": round(gross, 2), "costs_rs": round(costs, 2), "net_rs": round(gross - costs, 2),
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExpiryStraddle:
        legs = [Leg(**l) for l in d.get("legs", [])]
        obj = cls(day=d["day"], is_expiry=d["is_expiry"], units=d["units"], step=d.get("step", 50),
                  slip=d.get("slip", SLIP_POINTS), stop_mult=d.get("stop_mult", STOP_MULT))
        obj.status = d.get("status", obj.status)
        obj.spot_at_entry = d.get("spot_at_entry")
        obj.legs = legs
        obj.events = list(d.get("events", []))
        return obj

    # ---------------- internals ----------------
    def _leg(self, kind: str) -> Leg | None:
        return next((l for l in self.legs if l.kind == kind), None)

    def _close(self, leg: Leg, ms: int, ltp: float, reason: str) -> None:
        leg.exit = round(ltp + self.slip, 2); leg.exit_ms = ms; leg.reason = reason
        self._log(ms, f"buy_{reason}", f"{leg.strike} {leg.kind} @ {leg.exit} (LTP {ltp})")

    def _maybe_done(self) -> None:
        if self.legs and all(not l.open for l in self.legs):
            self.status = "done"

    def _log(self, ms: int, kind: str, text: str) -> None:
        self.events.append({"ms": ms, "event": kind, "text": text})


def app_cost_fn(day_iso: str) -> CostFn:
    """The app's dated option cost table as a CostFn (imported lazily so the core stays stdlib-only)."""
    from datetime import date
    from app.backtest.costs import load_default_cost_table

    table = load_default_cost_table(); on = date.fromisoformat(day_iso)
    return lambda side, price, units: float(table.leg_cost(side, price, units, on).total)
