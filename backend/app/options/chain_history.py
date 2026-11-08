"""Full option-chain snapshots: data/options/<YYYY-MM-DD>.jsonl (one line per 5-minute slot).

Same storage style as GEX (append-only JSONL, day from IST timestamp). Each line keeps every
strike's fields so later phases can backtest whether OI levels held.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.options.chain_view import format_ist
from app.options.time import IST


def local_day(t: int) -> date:
    return datetime.fromtimestamp(int(t), IST).date()


def snapshot_row(view: dict[str, Any]) -> dict[str, Any]:
    """Compact-but-complete line from a build_chain_view result (uses all_strikes when present)."""
    strikes_src = view.get("all_strikes") or [
        {"strike": r["strike"], "call": r["call"], "put": r["put"]} for r in view.get("rows", [])
    ]
    as_of = int(view["as_of"])
    return {
        "t": as_of,
        "as_of_ist": view.get("as_of_ist") or format_ist(as_of),
        "expiry": view["expiry"],
        "spot": view.get("spot"),
        "atm": view.get("atm"),
        "pcr": view.get("pcr"),
        "total_call_oi": view.get("total_call_oi"),
        "total_put_oi": view.get("total_put_oi"),
        "resistance": view.get("resistance") or [],
        "support": view.get("support") or [],
        "strikes": [
            {
                "strike": s["strike"],
                "call": _leg_snap(s["call"]),
                "put": _leg_snap(s["put"]),
            }
            for s in strikes_src
        ],
    }


def _leg_snap(leg: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "oi", "prev_oi", "oi_chg", "oi_chg_pct", "volume", "iv", "ltp", "close",
        "price_chg", "bid", "ask", "spread_pct", "delta", "gamma", "theta", "vega", "pop",
    )
    return {k: leg.get(k) for k in keys}


def append_snapshot(options_dir: Path, row: dict[str, Any]) -> Path:
    options_dir.mkdir(parents=True, exist_ok=True)
    path = options_dir / f"{local_day(int(row['t'])).isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path


def load_day(options_dir: Path, day: date) -> list[dict[str, Any]]:
    path = options_dir / f"{day.isoformat()}.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("t"), int):
            rows.append(row)
    rows.sort(key=lambda r: (r["t"], r.get("expiry") or ""))
    return rows


def latest_snapshot(options_dir: Path, *, expiry: str | None = None) -> dict[str, Any] | None:
    """Most recent snapshot across day files, optionally filtered by expiry."""
    if not options_dir.is_dir():
        return None
    best: dict[str, Any] | None = None
    for path in sorted(options_dir.glob("*.jsonl"), reverse=True):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not isinstance(row.get("t"), int):
                continue
            if expiry is not None and row.get("expiry") != expiry:
                continue
            if best is None or int(row["t"]) > int(best["t"]):
                best = row
    return best


def view_from_snapshot(snap: dict[str, Any], *, window: int = 15, status: str = "MARKET_CLOSED") -> dict[str, Any]:
    """Rebuild a UI payload from a stored snapshot line."""
    from app.options.chain_view import build_chain_view

    raw = []
    for s in snap.get("strikes") or []:
        call = s.get("call") or {}
        put = s.get("put") or {}
        raw.append(
            {
                "strike_price": s.get("strike"),
                "underlying_spot_price": snap.get("spot"),
                "call_options": {
                    "market_data": {
                        "oi": call.get("oi"),
                        "prev_oi": call.get("prev_oi"),
                        "volume": call.get("volume"),
                        "ltp": call.get("ltp"),
                        "close_price": call.get("close"),
                        "bid_price": call.get("bid"),
                        "ask_price": call.get("ask"),
                    },
                    "option_greeks": {
                        "iv": call.get("iv"),
                        "delta": call.get("delta"),
                        "gamma": call.get("gamma"),
                        "theta": call.get("theta"),
                        "vega": call.get("vega"),
                        "pop": call.get("pop"),
                    },
                },
                "put_options": {
                    "market_data": {
                        "oi": put.get("oi"),
                        "prev_oi": put.get("prev_oi"),
                        "volume": put.get("volume"),
                        "ltp": put.get("ltp"),
                        "close_price": put.get("close"),
                        "bid_price": put.get("bid"),
                        "ask_price": put.get("ask"),
                    },
                    "option_greeks": {
                        "iv": put.get("iv"),
                        "delta": put.get("delta"),
                        "gamma": put.get("gamma"),
                        "theta": put.get("theta"),
                        "vega": put.get("vega"),
                        "pop": put.get("pop"),
                    },
                },
            }
        )
    view = build_chain_view(
        raw,
        now=int(snap["t"]),
        expiry=str(snap.get("expiry") or ""),
        window=window,
        status=status,
    )
    view["from_snapshot"] = True
    if snap.get("as_of_ist"):
        view["as_of_ist"] = snap["as_of_ist"]
    return view
