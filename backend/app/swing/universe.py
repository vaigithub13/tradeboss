"""Nifty 500 universe as of a saved date, tagged large / mid / small.

NSE publishes the current constituent CSV only. A name that left an index or was
delisted since 2015 is not on that file, so it cannot be resolved here.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.upstox.instruments import Instrument, InstrumentIndex

CONSTITUENT_URLS = {
    "nifty50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "next50": "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
    "nifty500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "midcap150": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "smallcap250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
}

BROAD_INDICES: tuple[tuple[str, str], ...] = (
    ("Nifty 50", "NSE_INDEX|Nifty 50"),
    ("Nifty 500", "NSE_INDEX|Nifty 500"),
    ("Nifty Midcap 150", "NSE_INDEX|NIFTY MIDCAP 150"),
    ("Nifty Smallcap 250", "NSE_INDEX|NIFTY SMLCAP 250"),
)

SECTOR_INDICES: tuple[tuple[str, str], ...] = (
    ("Nifty Auto", "NSE_INDEX|Nifty Auto"),
    ("Nifty Bank", "NSE_INDEX|Nifty Bank"),
    ("Nifty Financial Services", "NSE_INDEX|Nifty Fin Service"),
    ("Nifty FMCG", "NSE_INDEX|Nifty FMCG"),
    ("Nifty IT", "NSE_INDEX|Nifty IT"),
    ("Nifty Media", "NSE_INDEX|Nifty Media"),
    ("Nifty Metal", "NSE_INDEX|Nifty Metal"),
    ("Nifty Pharma", "NSE_INDEX|Nifty Pharma"),
    ("Nifty PSU Bank", "NSE_INDEX|Nifty PSU Bank"),
    ("Nifty Realty", "NSE_INDEX|Nifty Realty"),
    ("Nifty Energy", "NSE_INDEX|Nifty Energy"),
    ("Nifty Infrastructure", "NSE_INDEX|Nifty Infra"),
    ("Nifty Commodities", "NSE_INDEX|Nifty Commodities"),
    ("Nifty Oil and Gas", "NSE_INDEX|NIFTY OIL AND GAS"),
    ("Nifty Healthcare", "NSE_INDEX|NIFTY HEALTHCARE"),
    ("Nifty Consumer Durables", "NSE_INDEX|NIFTY CONSR DURBL"),
    ("Nifty Private Bank", "NSE_INDEX|Nifty Pvt Bank"),
    ("Nifty PSE", "NSE_INDEX|Nifty PSE"),
)

SURVIVORSHIP_NOTE = (
    "NSE publishes the current constituent CSV and index levels, not a 2015-present "
    "membership tape. Stocks that left these indices or were delisted since 2015 are "
    "absent from the file, so they were not added. The not-found count is names on "
    "today's lists with no Upstox equity key. It is a lower bound on the survivorship gap."
)

ASM_NOTE = (
    "No historical ASM/GSM membership tape was found on NSE or Upstox. "
    "Today's list is not copied onto past days."
)
CIRCUIT_NOTE = (
    "No historical circuit-band series was found. A current price band is not stored as history."
)


def parse_constituents(text: str) -> list[dict[str, str]]:
    """One NSE constituent CSV. Rows without a symbol or ISIN are dropped."""
    sample = text.lstrip("\ufeff")
    start = sample.find("Company Name,")
    if start > 0:
        sample = sample[start:]
    rows = []
    for row in csv.DictReader(io.StringIO(sample)):
        symbol = str(row.get("Symbol") or "").strip().upper()
        isin = str(row.get("ISIN Code") or row.get("ISIN") or "").strip().upper()
        if not symbol or not isin or not isin.startswith("IN"):
            continue
        rows.append({
            "symbol": symbol,
            "isin": isin,
            "name": str(row.get("Company Name") or "").strip(),
            "industry": str(row.get("Industry") or "").strip(),
            "series": str(row.get("Series") or "").strip(),
        })
    return rows


def tag_segment(symbol: str, lists: dict[str, set[str]]) -> str:
    """Large is Nifty 50 and Nifty Next 50. Mid and small are today's index membership."""
    if symbol in lists.get("midcap150", set()):
        return "mid"
    if symbol in lists.get("smallcap250", set()):
        return "small"
    if symbol in lists.get("nifty50", set()) or symbol in lists.get("next50", set()):
        return "large"
    if symbol in lists.get("nifty500", set()):
        return "large"
    return "other"


def _equity_maps(index: InstrumentIndex) -> tuple[dict[str, Instrument], dict[str, Instrument]]:
    by_isin: dict[str, Instrument] = {}
    by_symbol: dict[str, Instrument] = {}
    for inst in index.instruments:
        if inst.kind != "equity" or "|" not in inst.key:
            continue
        isin = inst.key.split("|", 1)[1].upper()
        by_isin.setdefault(isin, inst)
        by_symbol.setdefault(inst.symbol.upper(), inst)
    return by_isin, by_symbol


def master_equities(path: Path) -> list[Instrument]:
    """NSE equity rows the live index drops: BE (trade-to-trade) and RR (REIT)."""
    import gzip

    raw = gzip.open(path, "rt").read() if path.suffix == ".gz" else path.read_text()
    rows = json.loads(raw)
    out = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict) or row.get("segment") != "NSE_EQ":
            continue
        key = row.get("instrument_key")
        if not isinstance(key, str) or "|" not in key:
            continue
        symbol = str(row.get("trading_symbol") or "")
        name = str(row.get("name") or symbol)
        typ = str(row.get("instrument_type") or "EQ")
        out.append(Instrument(key, symbol, name, "equity", "NSE_EQ", typ))
    return out


def build_universe(
    texts: dict[str, str],
    index: InstrumentIndex,
    *,
    as_of: date,
    list_errors: dict[str, str] | None = None,
    extra_equities: list[Instrument] | None = None,
) -> dict[str, Any]:
    """Resolve today's lists. A missing Upstox key stays in `not_found` and is not substituted."""
    parsed = {name: parse_constituents(text) for name, text in texts.items()}
    sets = {name: {row["symbol"] for row in rows} for name, rows in parsed.items()}
    stocks = []
    for row in parsed.get("nifty500", []):
        lists = [name for name in ("nifty50", "next50", "midcap150", "smallcap250", "nifty500") if row["symbol"] in sets.get(name, set())]
        stocks.append({**row, "lists": lists, "segment": tag_segment(row["symbol"], sets)})
    stocks.sort(key=lambda row: row["symbol"])

    by_isin, by_symbol = _equity_maps(index)
    for inst in extra_equities or []:
        if inst.kind != "equity" or "|" not in inst.key:
            continue
        isin = inst.key.split("|", 1)[1].upper()
        by_isin.setdefault(isin, inst)
        by_symbol.setdefault(inst.symbol.upper(), inst)
    resolved = []
    not_found = []
    for row in stocks:
        inst = by_isin.get(row["isin"]) or by_symbol.get(row["symbol"])
        if inst is None:
            not_found.append({**row, "reason": "no Upstox equity for this ISIN or symbol"})
            continue
        resolved.append({
            **row,
            "instrument_key": inst.key,
            "upstox_symbol": inst.symbol,
            "role": "equity",
        })

    indices = []
    index_missing = []
    for label, key in (*BROAD_INDICES, *SECTOR_INDICES):
        inst = index.get(key)
        item = {"name": label, "instrument_key": key, "role": "index", "segment": "index"}
        if inst is None:
            index_missing.append(item)
        else:
            indices.append({**item, "upstox_symbol": inst.symbol})

    return {
        "id": "swing_universe_v1",
        "as_of": as_of.isoformat(),
        "snapshot_day": None if index.snapshot_day is None else index.snapshot_day.isoformat(),
        "segment_rule": "Nifty 50 and Nifty Next 50 are large. Midcap 150 is mid. Smallcap 250 is small.",
        "survivorship_note": SURVIVORSHIP_NOTE,
        "asm_gsm_note": ASM_NOTE,
        "circuit_note": CIRCUIT_NOTE,
        "list_errors": list_errors or {},
        "counts": {
            "nifty500": len(sets.get("nifty500", set())),
            "resolved": len(resolved),
            "not_found": len(not_found),
            "indices": len(indices),
            "indices_missing": len(index_missing),
            "departed_since_2015_added": 0,
        },
        "by_segment": {
            segment: sum(1 for row in resolved if row["segment"] == segment)
            for segment in ("large", "mid", "small", "other")
        },
        "stocks": resolved,
        "not_found": not_found,
        "indices": indices,
        "indices_missing": index_missing,
    }
