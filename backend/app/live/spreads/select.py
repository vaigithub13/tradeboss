"""Nearest weekly, ATM ± 2 strikes, call and put. The set moves with the ATM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.options.strikes import atm_strike

HOLD_MS = 2_000
RELEASE_MS = 30_000
MAX_OPTION_KEYS = 20


@dataclass(frozen=True)
class Contract:
    key: str
    strike: float
    kind: str
    expiry: date


def nearest_weekly(calendar, day: date) -> date:
    """The weekly that is trading on `day`. On its expiry morning that contract is still the one."""
    return calendar.next_expiry(day, "weekly").date


def select_contracts(
    spot: float, step: int, expiry: date, listed: list[Contract]
) -> tuple[list[Contract], list[tuple[float, str]]]:
    """Ten contracts around the ATM, and the (strike, kind) pairs the snapshot does not have."""
    center = atm_strike(spot, step)
    strikes = [center + i * step for i in range(-2, 3)]
    chosen: list[Contract] = []
    missing: list[tuple[float, str]] = []
    for strike in strikes:
        for kind in ("CE", "PE"):
            hit = next((c for c in listed if c.expiry == expiry and c.kind == kind and c.strike == strike), None)
            if hit is None:
                missing.append((float(strike), kind))
            else:
                chosen.append(hit)
    return chosen, missing


@dataclass
class _Generation:
    center: float
    contracts: list[Contract]
    since_ms: int
    seen: set[str] = field(default_factory=set)

    @property
    def keys(self) -> list[str]:
        return [c.key for c in self.contracts]


class StrikeSelector:
    """Desired option keys. A new ATM has to hold for 2 seconds. The previous set stays
    until every new key has printed a book, or 30 seconds pass. Option keys never pass 20:
    a further move drops the oldest generation."""

    def __init__(self) -> None:
        self._gens: list[_Generation] = []
        self._pending: tuple[float, int] | None = None
        self.missing: list[tuple[float, str]] = []

    def keys(self) -> list[str]:
        return self._unique()[:MAX_OPTION_KEYS]

    def _unique(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for gen in self._gens:
            for key in gen.keys:
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    def contracts(self) -> dict[str, Contract]:
        out: dict[str, Contract] = {}
        for gen in self._gens:
            for contract in gen.contracts:
                out[contract.key] = contract
        return out

    def on_spot(self, spot: float, step: int, expiry: date, listed: list[Contract], ts_ms: int) -> list[str]:
        chosen, missing = select_contracts(spot, step, expiry, listed)
        if not chosen:
            return self.keys()
        self.missing = missing
        center = atm_strike(spot, step)
        if not self._gens:
            self._gens = [_Generation(center, chosen, ts_ms)]
            self._pending = None
            return self.keys()
        current = self._gens[-1].center
        if center == current:
            self._pending = None
            self._release(ts_ms)
            return self.keys()
        if self._pending is None or self._pending[0] != center:
            self._pending = (center, ts_ms)
            return self.keys()
        if ts_ms - self._pending[1] < HOLD_MS:
            return self.keys()
        self._pending = None
        self._gens.append(_Generation(center, chosen, ts_ms))
        self._trim()
        self._release(ts_ms)
        return self.keys()

    def on_depth(self, key: str, ts_ms: int) -> None:
        if not self._gens:
            return
        newest = self._gens[-1]
        if key in newest.keys and ts_ms >= newest.since_ms:
            newest.seen.add(key)
        self._release(ts_ms)

    def _release(self, ts_ms: int) -> None:
        if len(self._gens) < 2:
            return
        newest = self._gens[-1]
        ready = bool(newest.keys) and set(newest.keys) <= newest.seen
        if ready or ts_ms - newest.since_ms >= RELEASE_MS:
            self._gens = [newest]

    def _trim(self) -> None:
        while len(self._gens) > 1 and len(self._unique()) > MAX_OPTION_KEYS:
            self._gens.pop(0)
