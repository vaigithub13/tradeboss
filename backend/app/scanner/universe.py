"""Load the editable universe file and map symbols to Upstox equity keys."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.upstox.instruments import InstrumentIndex

DATA_FILE = Path(__file__).resolve().parent / "universes.json"


@dataclass(frozen=True)
class UniverseStock:
    symbol: str
    sector: str


@dataclass(frozen=True)
class ResolvedStock:
    symbol: str
    sector: str
    instrument_key: str
    folder: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_universes(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DATA_FILE).read_text(encoding="utf-8"))


def universe_names(spec: dict[str, Any] | None = None) -> dict[str, list[UniverseStock]]:
    spec = spec if spec is not None else load_universes()
    out: dict[str, list[UniverseStock]] = {}
    for uid, body in (spec.get("universes") or {}).items():
        stocks = [
            UniverseStock(symbol=str(row["symbol"]), sector=str(row.get("sector") or ""))
            for row in body.get("stocks") or []
        ]
        out[str(uid)] = stocks
    return out


def resolve_names(
    names: list[UniverseStock],
    equities: dict[str, list[tuple[str, str]]],
) -> tuple[list[ResolvedStock], list[str]]:
    """`equities` maps a trading symbol to [(instrument_key, folder), ...]. Ambiguous names are unresolved."""
    resolved: list[ResolvedStock] = []
    unresolved: list[str] = []
    for stock in names:
        hits = equities.get(stock.symbol) or []
        if len(hits) != 1:
            unresolved.append(stock.symbol)
            continue
        key, folder = hits[0]
        resolved.append(ResolvedStock(stock.symbol, stock.sector, key, folder))
    return resolved, unresolved


def equity_table(index: InstrumentIndex) -> dict[str, list[tuple[str, str]]]:
    from app.data.history import symbol_dir_name

    table: dict[str, list[tuple[str, str]]] = {}
    for inst in index.instruments:
        if inst.kind != "equity":
            continue
        table.setdefault(inst.symbol, []).append((inst.key, symbol_dir_name(inst.key)))
    return table


def resolve_universe(
    index: InstrumentIndex | None,
    universe_id: str,
    *,
    spec: dict[str, Any] | None = None,
) -> tuple[list[ResolvedStock], list[str]]:
    names = universe_names(spec).get(universe_id)
    if names is None:
        raise KeyError(f"unknown universe {universe_id!r}")
    if index is None:
        return [], [s.symbol for s in names]
    return resolve_names(names, equity_table(index))
