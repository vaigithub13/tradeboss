"""Load and resolve research_universe_v1 against an Upstox instrument snapshot."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.upstox.instruments import (
    BANKNIFTY_INDEX_KEY,
    Instrument,
    InstrumentIndex,
)

DATA_FILE = Path(__file__).resolve().parent / "data" / "research_universe_v1.json"


@dataclass(frozen=True)
class ResolvedMember:
    symbol: str
    role: str  # index | future | equity
    instrument_key: str
    folder: str
    expiry: str | None = None


@dataclass(frozen=True)
class UniverseResolution:
    id: str
    as_of: str
    history_from: str
    resolved: list[ResolvedMember]
    unresolved: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "as_of": self.as_of,
            "history_from": self.history_from,
            "resolved": [asdict(m) for m in self.resolved],
            "unresolved": list(self.unresolved),
            "notes": list(self.notes),
        }


def load_universe_spec(path: Path | None = None) -> dict[str, Any]:
    raw = json.loads((path or DATA_FILE).read_text(encoding="utf-8"))
    if raw.get("id") != "research_universe_v1":
        raise ValueError(f"expected research_universe_v1, got {raw.get('id')!r}")
    return raw


def resolve_universe(
    index: InstrumentIndex,
    *,
    today: date | None = None,
    spec: dict[str, Any] | None = None,
) -> UniverseResolution:
    """Exact trading_symbol matches only. Unresolved symbols are listed, never replaced."""
    from app.data.history import symbol_dir_name

    spec = spec or load_universe_spec()
    today = today or date.fromisoformat(str(spec["as_of"]))
    notes: list[str] = []
    resolved: list[ResolvedMember] = []
    unresolved: list[str] = []

    index_key = str((spec.get("index") or {}).get("instrument_key") or BANKNIFTY_INDEX_KEY)
    index_sym = str((spec.get("index") or {}).get("symbol") or "BANKNIFTY")
    bank = index.get(index_key)
    if bank is None or bank.kind != "index":
        unresolved.append(index_sym)
        notes.append(f"index key {index_key} not in snapshot")
    else:
        resolved.append(
            ResolvedMember(
                symbol=bank.symbol or index_sym,
                role="index",
                instrument_key=bank.key,
                folder=symbol_dir_name(bank.key),
            )
        )
        fut = index.front_future(today, underlying_key=index_key)
        if fut is None:
            unresolved.append(f"{index_sym}_FUT")
            notes.append(f"no unexpired BankNifty future for {today.isoformat()}")
        else:
            resolved.append(
                ResolvedMember(
                    symbol=fut.symbol,
                    role="future",
                    instrument_key=fut.key,
                    folder=symbol_dir_name(fut.key),
                    expiry=None if fut.expiry is None else fut.expiry.isoformat(),
                )
            )

    by_symbol: dict[str, list[Instrument]] = {}
    for inst in index.instruments:
        if inst.kind == "equity":
            by_symbol.setdefault(inst.symbol, []).append(inst)

    for sym in spec.get("stocks") or []:
        hits = by_symbol.get(str(sym)) or []
        if len(hits) != 1:
            unresolved.append(str(sym))
            if not hits:
                notes.append(f"{sym}: no NSE_EQ match (rename/demerger?); not substituted")
            else:
                notes.append(f"{sym}: {len(hits)} equity matches; refusing ambiguous resolve")
            continue
        inst = hits[0]
        resolved.append(
            ResolvedMember(
                symbol=inst.symbol,
                role="equity",
                instrument_key=inst.key,
                folder=symbol_dir_name(inst.key),
            )
        )

    return UniverseResolution(
        id=str(spec["id"]),
        as_of=str(spec["as_of"]),
        history_from=str(spec["history_from"]),
        resolved=resolved,
        unresolved=unresolved,
        notes=notes,
    )


def equity_isin(instrument_key: str) -> str | None:
    """`NSE_EQ|INE002A01018` → `INE002A01018`."""
    if not instrument_key.startswith("NSE_EQ|"):
        return None
    isin = instrument_key.split("|", 1)[1]
    return isin or None
