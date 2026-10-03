"""Transaction costs and slippage (critical module). Spec: tests/test_bt_costs.py.

Costs come from a DATED, EDITABLE table (`data/cost_rates.json`): the row with the latest
`effective_from` on or before the trade date applies. Nothing is assumed: a rate that is missing
from the row raises `UnknownRate` when (and only when) a leg needs it, and a date before the first
row raises `NoRatesForDate`. All money maths is `Decimal`; each component is rounded half-up to
its own quantum (default 0.01, configurable per component, e.g. STT to the rupee).

Percent rates are written as percent ("0.1" = 0.1%). `sebi_per_crore` is rupees per 1 crore of
turnover. Turnover = price x units.

Per leg (index options): brokerage; STT on SELL; exchange transaction charge; SEBI fee; stamp duty
on BUY; GST on the components listed in the row's `gst_on` (default brokerage + exchange + SEBI fee;
brokers differ, so a seeded row states it) plus extras flagged `gst`; optional `extra` lines.

Every row says how far each rate can be trusted: `verification` maps a rate to "verified" (checked
against a contract note) or "unverified" (anything else, e.g. a published rate card); a rate with no
entry counts as unverified. `sources` records where each number came from. A run that charges costs
from unverified rates says so in its warnings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CENT = Decimal("0.01")
DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_COST_FILE = DATA_DIR / "cost_rates.json"

RATE_KEYS = (
    "brokerage_flat",  # rupees per executed order
    "brokerage_pct",  # percent of turnover; with brokerage_flat the LOWER of the two applies
    "stt_sell_pct",
    "exchange_pct",
    "sebi_per_crore",
    "stamp_buy_pct",
    "gst_pct",
)
COMPONENTS = ("brokerage", "stt", "exchange", "sebi", "stamp", "gst")
SIDES = ("BUY", "SELL")


class CostConfigError(ValueError):
    pass


class NoRatesForDate(LookupError):
    pass


class UnknownRate(LookupError):
    pass


def _dec(name: str, value: Any) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise CostConfigError(f"{name}: {value!r} is not a number") from exc
    if not d.is_finite() or d < 0:
        raise CostConfigError(f"{name}: must be a finite number >= 0, got {value!r}")
    return d


def _q(x: Decimal, quantum: Decimal) -> Decimal:
    return x.quantize(quantum, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ExtraCharge:
    name: str
    pct: Decimal
    sides: tuple[str, ...]
    gst: bool = False


@dataclass(frozen=True)
class CostRow:
    effective_from: date
    rates: dict[str, Decimal | None]
    extra: tuple[ExtraCharge, ...] = ()
    rounding: dict[str, Decimal] | None = None
    gst_on: tuple[str, ...] = ("brokerage", "exchange", "sebi")
    verification: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def unverified(self) -> list[str]:
        """Names of the rates / conventions of this row that no contract note has confirmed."""
        names = [k for k in RATE_KEYS if self.rates.get(k) is not None] + [e.name for e in self.extra] + ["gst_on"]
        if self.rounding is not None:
            names.append("rounding")
        return [n for n in names if self.verification.get(n, "unverified") != "verified"]


@dataclass(frozen=True)
class LegCost:
    components: dict[str, Decimal]
    total: Decimal


class CostTable:
    def __init__(self, rows: list[CostRow]) -> None:
        seen: set[date] = set()
        for r in rows:
            if r.effective_from in seen:
                raise CostConfigError(f"duplicate effective_from {r.effective_from}")
            seen.add(r.effective_from)
        self.rows = sorted(rows, key=lambda r: r.effective_from)

    # ---------------------------------------------------------------- loading
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostTable:
        rows: list[CostRow] = []
        for raw in data.get("rows", []):
            try:
                eff = date.fromisoformat(str(raw["effective_from"]))
            except (KeyError, ValueError) as exc:
                raise CostConfigError(f"bad effective_from in {raw!r} (use YYYY-MM-DD)") from exc
            rates_in = dict(raw.get("rates", {}))
            extra_in = rates_in.pop("extra", [])
            unknown = sorted(set(rates_in) - set(RATE_KEYS))
            if unknown:
                raise CostConfigError(f"unknown rate(s) {unknown}; expected {list(RATE_KEYS)} (+ 'extra')")
            rates = {k: (None if rates_in.get(k) is None else _dec(k, rates_in[k])) for k in RATE_KEYS}
            extras = tuple(
                ExtraCharge(
                    name=str(e["name"]),
                    pct=_dec(f"extra.{e['name']}", e["pct"]),
                    sides=tuple(e.get("sides", SIDES)),
                    gst=bool(e.get("gst", False)),
                )
                for e in extra_in
            )
            allowed = set(COMPONENTS) | {e.name for e in extras}
            rounding_in = raw.get("rounding")
            rounding = None
            if rounding_in is not None:
                bad = sorted(set(rounding_in) - allowed)
                if bad:
                    raise CostConfigError(f"rounding for unknown component(s) {bad}")
                rounding = {k: _dec(f"rounding.{k}", v) for k, v in rounding_in.items()}
                if any(v <= 0 for v in rounding.values()):
                    raise CostConfigError("rounding quantum must be > 0")
            core_gst = {"brokerage", "stt", "exchange", "sebi", "stamp"}
            gst_in = raw.get("gst_on")
            gst_on = ("brokerage", "exchange", "sebi") if gst_in is None else tuple(str(x) for x in gst_in)
            bad_gst = sorted(set(gst_on) - core_gst - {e.name for e in extras})
            if bad_gst:
                raise CostConfigError(f"gst_on: unknown component(s) {bad_gst}")
            known = set(RATE_KEYS) | {e.name for e in extras} | {"gst_on", "rounding"}
            verification = {str(k): str(v) for k, v in dict(raw.get("verification", {})).items()}
            if sorted(set(verification.values()) - {"verified", "unverified"}):
                raise CostConfigError("verification values must be 'verified' or 'unverified'")
            sources = {str(k): str(v) for k, v in dict(raw.get("sources", {})).items()}
            stray = sorted((set(verification) | set(sources)) - known)
            if stray:
                raise CostConfigError(f"verification/sources for unknown name(s) {stray}")
            rows.append(CostRow(eff, rates, extras, rounding, gst_on, verification, sources, str(raw.get("note", ""))))
        return cls(rows)

    @classmethod
    def load(cls, path: Path) -> CostTable:
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "effective_from": r.effective_from.isoformat(),
                    "rates": {k: (None if v is None else str(v)) for k, v in sorted(r.rates.items())},
                    "extra": [{"name": e.name, "pct": str(e.pct), "sides": list(e.sides), "gst": e.gst} for e in r.extra],
                    "rounding": None if r.rounding is None else {k: str(v) for k, v in sorted(r.rounding.items())},
                    "gst_on": list(r.gst_on),
                    "verification": dict(sorted(r.verification.items())),
                    "sources": dict(sorted(r.sources.items())),
                    "note": r.note,
                }
                for r in self.rows
            ]
        }

    # ---------------------------------------------------------------- maths
    def row_for(self, on: date) -> CostRow:
        found: CostRow | None = None
        for r in self.rows:
            if r.effective_from <= on:
                found = r
        if found is None:
            first = self.rows[0].effective_from.isoformat() if self.rows else "none"
            raise NoRatesForDate(
                f"no cost rates for {on.isoformat()} (first dated row: {first}); add a row from a contract note "
                f"to app/backtest/data/cost_rates.json"
            )
        return found

    def leg_cost(self, side: str, price: float, units: int, on: date) -> LegCost:
        if side not in SIDES:
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        row = self.row_for(on)
        turnover = Decimal(str(price)) * Decimal(units)

        def need(key: str) -> Decimal:
            v = row.rates.get(key)
            if v is None:
                raise UnknownRate(f"rate {key!r} is not in the cost row effective {row.effective_from} (not on the contract note?)")
            return v

        def quantum(component: str) -> Decimal:
            return (row.rounding or {}).get(component, CENT)

        flat, pct = row.rates.get("brokerage_flat"), row.rates.get("brokerage_pct")
        if flat is None and pct is None:
            need("brokerage_flat")
        options = [x for x in (flat, None if pct is None else turnover * pct / 100) if x is not None]
        comp: dict[str, Decimal] = {"brokerage": _q(min(options), quantum("brokerage"))}
        comp["stt"] = _q(turnover * need("stt_sell_pct") / 100, quantum("stt")) if side == "SELL" else Decimal("0.00")
        comp["exchange"] = _q(turnover * need("exchange_pct") / 100, quantum("exchange"))
        comp["sebi"] = _q(turnover * need("sebi_per_crore") / Decimal(10_000_000), quantum("sebi"))
        comp["stamp"] = _q(turnover * need("stamp_buy_pct") / 100, quantum("stamp")) if side == "BUY" else Decimal("0.00")
        for e in row.extra:
            comp[e.name] = _q(turnover * e.pct / 100, quantum(e.name)) if side in e.sides else Decimal("0.00")
        gst_base = sum((comp[c] for c in row.gst_on), Decimal("0.00"))
        gst_base += sum((comp[e.name] for e in row.extra if e.gst and e.name not in row.gst_on), Decimal("0.00"))
        # GST is on the (rounded) taxable components; position it before the extras in the dict
        gst = _q(gst_base * need("gst_pct") / 100, quantum("gst"))
        ordered = {k: comp[k] for k in ("brokerage", "stt", "exchange", "sebi", "stamp")}
        ordered["gst"] = gst
        for e in row.extra:
            ordered[e.name] = comp[e.name]
        return LegCost(ordered, sum(ordered.values(), Decimal("0.00")))


def load_default_cost_table() -> CostTable:
    return CostTable.load(DEFAULT_COST_FILE)


class CostModel:
    """A named cost preset: `zero` charges nothing, `options` reads the dated table."""

    def __init__(self, name: str, table: CostTable | None) -> None:
        self.name = name
        self.table = table

    @classmethod
    def zero(cls) -> CostModel:
        return cls("zero", None)

    def leg_cost(self, side: str, price: float, units: int, on: date) -> LegCost:
        if self.table is None:
            return LegCost({}, Decimal("0.00"))
        return self.table.leg_cost(side, price, units, on)

    def unverified_warnings(self, days: set[date]) -> list[str]:
        """One line per cost row used on `days` that still has rates no contract note has confirmed."""
        if self.table is None:
            return []
        out = []
        for eff in sorted({self.table.row_for(d).effective_from for d in days}):
            row = self.table.row_for(eff)
            bad = row.unverified()
            if bad:
                out.append(f"cost rates effective {eff.isoformat()} are UNVERIFIED (no contract note has confirmed: "
                           f"{', '.join(bad)})")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "table": None if self.table is None else self.table.to_dict()}


def get_cost_model(preset: str, table: CostTable | None = None) -> CostModel:
    if preset == "zero":
        return CostModel.zero()
    if preset == "options":
        return CostModel("options", table if table is not None else load_default_cost_table())
    raise ValueError(f"unknown cost preset {preset!r}; 3a has 'options' and 'zero'")


@dataclass(frozen=True)
class Slippage:
    """Adverse price movement on market and stop fills (never on limit fills)."""

    kind: str = "none"  # none | points | pct
    value: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in ("none", "points", "pct"):
            raise ValueError(f"unknown slippage kind {self.kind!r}")
        if not self.value >= 0:
            raise ValueError("slippage must be >= 0")

    @classmethod
    def none(cls) -> Slippage:
        return cls("none", 0.0)

    @classmethod
    def points(cls, value: float) -> Slippage:
        return cls("points", float(value))

    @classmethod
    def pct(cls, value: float) -> Slippage:
        return cls("pct", float(value))

    def apply(self, side: str, price: float) -> float:
        if self.kind == "none" or self.value == 0:
            return price
        p = Decimal(str(price))
        delta = Decimal(str(self.value)) if self.kind == "points" else p * Decimal(str(self.value)) / 100
        return float(p + delta if side == "BUY" else p - delta)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value}
