"""Instrument master: dated daily snapshots of the NSE file + symbol search.

Upstox publishes the instrument master as a public gzipped JSON (refreshed ~6 AM IST; the file for
the day does not contain expired contracts). We keep EVERY day's raw file as
    <data>/instruments/YYYY-MM-DD/NSE.json.gz
so lot sizes, expiries and contracts that later expire are preserved for backtests.

The file is public: no token is involved (and none is ever sent to this host).
"""

from __future__ import annotations

import gzip
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

IST = timezone(timedelta(hours=5, minutes=30))
NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
SNAPSHOT_FILE = "NSE.json.gz"
#: the file is refreshed around 6 AM IST; do not snapshot earlier than this (it would be yesterday's)
SNAPSHOT_AFTER = time(6, 30)
_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
VIX_KEY = "NSE_INDEX|India VIX"

Kind = Literal["index", "equity", "future", "option"]
KINDS: tuple[Kind, ...] = ("index", "equity", "future", "option")


class SnapshotError(RuntimeError):
    pass


# ------------------------------------------------------------------ download + snapshots
def download_nse(http: httpx.Client | None = None, url: str = NSE_INSTRUMENTS_URL) -> bytes:
    """The raw NSE.json.gz bytes. Public file: no Authorization header is ever sent."""
    own = http is None
    client = http or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        resp = client.get(url, headers={"Accept": "*/*"})
        resp.raise_for_status()
        return resp.content
    finally:
        if own:
            client.close()


def parse_master(raw: bytes) -> list[dict[str, Any]]:
    """Decode + sanity-check a raw NSE.json.gz. Raises SnapshotError for anything odd."""
    try:
        rows = json.loads(gzip.decompress(raw))
    except (OSError, EOFError, ValueError) as e:
        raise SnapshotError(f"instrument file is not valid gzip JSON: {type(e).__name__}") from None
    if not isinstance(rows, list) or len(rows) < 1000:
        raise SnapshotError("instrument file looks truncated (too few rows)")
    if not any(isinstance(r, dict) and r.get("instrument_key") == NIFTY_INDEX_KEY for r in rows):
        raise SnapshotError(f"instrument file does not contain {NIFTY_INDEX_KEY!r}")
    return rows


def snapshot_dir(base: Path, day: date) -> Path:
    return base / day.isoformat()


def snapshot_path(base: Path, day: date) -> Path:
    return snapshot_dir(base, day) / SNAPSHOT_FILE


def list_snapshots(base: Path) -> list[date]:
    if not base.is_dir():
        return []
    days = [
        date.fromisoformat(p.name)
        for p in base.iterdir()
        if p.is_dir() and _DATE_DIR.match(p.name) and (p / SNAPSHOT_FILE).is_file()
    ]
    return sorted(days)


def latest_snapshot(base: Path) -> tuple[date, Path] | None:
    days = list_snapshots(base)
    return (days[-1], snapshot_path(base, days[-1])) if days else None


def ist_now() -> datetime:
    return datetime.now(IST)


SnapshotOutcome = Literal["saved", "unchanged", "exists"]
#: written instead of NSE.json.gz when the file is identical to the previous snapshot
UNCHANGED_MARKER = "UNCHANGED"


@dataclass(frozen=True)
class SnapshotResult:
    day: date
    #: the file that holds today's content (for "unchanged": the earlier identical snapshot)
    path: Path
    outcome: SnapshotOutcome
    #: for "unchanged": the date of the snapshot it is identical to
    same_as: date | None = None

    @property
    def created(self) -> bool:
        return self.outcome == "saved"


def _same_content(a: bytes, b: bytes) -> bool:
    """Byte-identical, or identical once un-gzipped (the gzip header can carry a timestamp that
    differs between downloads of the very same file)."""
    if a == b:
        return True
    try:
        return gzip.decompress(a) == gzip.decompress(b)
    except (OSError, EOFError):
        return False


def snapshot_marker(base: Path, day: date) -> Path:
    return snapshot_dir(base, day) / UNCHANGED_MARKER


def snapshot_done(base: Path, day: date) -> bool:
    """Is there a decision for this day? (a snapshot, or a note that it equals an earlier one)"""
    return snapshot_path(base, day).is_file() or snapshot_marker(base, day).is_file()


def take_snapshot(
    base: Path,
    *,
    now: datetime | None = None,
    fetch: Callable[[], bytes] = download_nse,
    force: bool = False,
) -> SnapshotResult:
    """Save today's (IST) raw instrument file unless it already exists. Atomic; validated first.

    If the download is identical to the most recent earlier snapshot (weekends / holidays) it is
    NOT saved again: a tiny `UNCHANGED` note naming that snapshot is written instead, so "did
    today's job run" stays answerable and the startup fallback does not retry all day.
    `force` re-downloads and always writes the file."""
    day = (now or ist_now()).astimezone(IST).date()
    path = snapshot_path(base, day)
    if not force and snapshot_done(base, day):
        existing = path if path.is_file() else _marker_target(base, day)
        return SnapshotResult(day, existing, "exists")
    raw = fetch()
    parse_master(raw)  # refuse to store garbage
    previous = [d for d in list_snapshots(base) if d < day]
    if not force and previous:
        prev_day = previous[-1]
        prev_path = snapshot_path(base, prev_day)
        if _same_content(raw, prev_path.read_bytes()):
            marker = snapshot_marker(base, day)
            marker.parent.mkdir(parents=True, exist_ok=True)
            tmp = marker.with_suffix(".tmp")
            tmp.write_text(f"{prev_day.isoformat()}\n")
            os.replace(tmp, marker)
            return SnapshotResult(day, prev_path, "unchanged", same_as=prev_day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".gz.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    marker = snapshot_marker(base, day)
    if marker.exists():  # a forced re-download that turned out different from the earlier note
        marker.unlink()
    return SnapshotResult(day, path, "saved")


def _marker_target(base: Path, day: date) -> Path:
    try:
        same = date.fromisoformat(snapshot_marker(base, day).read_text().strip())
        return snapshot_path(base, same)
    except (OSError, ValueError):
        return snapshot_path(base, day)


def snapshot_due(base: Path, now: datetime | None = None) -> bool:
    """Startup fallback rule: nothing decided for today yet and it is past 06:30 IST."""
    n = (now or ist_now()).astimezone(IST)
    return n.time() >= SNAPSHOT_AFTER and not snapshot_done(base, n.date())


# ------------------------------------------------------------------ instruments + search
@dataclass(frozen=True)
class Instrument:
    key: str
    symbol: str  # trading_symbol, e.g. RELIANCE / NIFTY 27000 CE 29 DEC 26
    name: str
    kind: Kind
    segment: str
    instrument_type: str  # INDEX / EQ / FUT / CE / PE
    expiry: date | None = None
    strike: float | None = None
    lot_size: int | None = None
    underlying_key: str | None = None

    @property
    def has_oi(self) -> bool:
        return self.kind in ("future", "option")

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_key": self.key,
            "symbol": self.symbol,
            "name": self.name,
            "kind": self.kind,
            "segment": self.segment,
            "instrument_type": self.instrument_type,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "strike": self.strike,
            "lot_size": self.lot_size,
            "underlying_key": self.underlying_key,
        }


def _expiry_date(ms: Any) -> date | None:
    if isinstance(ms, bool) or not isinstance(ms, int | float):
        return None
    return datetime.fromtimestamp(ms / 1000, IST).date()


def instrument_of(row: dict[str, Any]) -> Instrument | None:
    """One master row -> Instrument, or None if it is not something we offer:
    NSE indices, NSE stocks (EQ), and Nifty 50 futures / options only."""
    seg, typ, key = row.get("segment"), row.get("instrument_type"), row.get("instrument_key")
    if not isinstance(key, str) or not key:
        return None
    symbol = str(row.get("trading_symbol") or "")
    name = str(row.get("name") or row.get("short_name") or symbol)
    lot = row.get("lot_size")
    lot_size = int(lot) if isinstance(lot, int | float) and not isinstance(lot, bool) else None
    if seg == "NSE_INDEX" and typ == "INDEX":
        return Instrument(key, symbol or name, name, "index", seg, typ)
    if seg == "NSE_EQ" and typ == "EQ":
        return Instrument(key, symbol, name, "equity", seg, typ, lot_size=lot_size)
    if seg == "NSE_FO" and typ in ("FUT", "CE", "PE") and row.get("underlying_key") == NIFTY_INDEX_KEY:
        strike = row.get("strike_price")
        return Instrument(
            key,
            symbol,
            name,
            "future" if typ == "FUT" else "option",
            seg,
            typ,
            expiry=_expiry_date(row.get("expiry")),
            strike=float(strike) if isinstance(strike, int | float) and typ != "FUT" else None,
            lot_size=lot_size,
            underlying_key=NIFTY_INDEX_KEY,
        )
    return None


def load_instruments(path: Path) -> list[Instrument]:
    rows = parse_master(path.read_bytes())
    out = [i for r in rows if isinstance(r, dict) and (i := instrument_of(r)) is not None]
    return out


_KIND_RANK = {"index": 0, "equity": 1, "future": 2, "option": 3}


class InstrumentIndex:
    """In-memory view of one snapshot."""

    def __init__(self, instruments: Iterable[Instrument], snapshot_day: date | None = None) -> None:
        self.snapshot_day = snapshot_day
        self.instruments = list(instruments)
        self._by_key = {i.key: i for i in self.instruments}

    def get(self, key: str) -> Instrument | None:
        return self._by_key.get(key)

    def search(
        self,
        query: str = "",
        kind: Kind | None = None,
        limit: int = 30,
        today: date | None = None,
        prefer: Callable[[Instrument], bool] | None = None,
    ) -> list[Instrument]:
        """Every whitespace-separated token must appear in symbol or name (case-insensitive).
        Ranking: exact symbol, symbol prefix, name prefix, other; then index < stock < future <
        option; derivatives by expiry then strike. Expired contracts are never offered.
        With an empty query, instruments for which `prefer` is true (e.g. data already stored)
        come first."""
        today = today or ist_now().date()
        tokens = query.lower().split()
        q = " ".join(tokens)
        scored: list[tuple[tuple[Any, ...], Instrument]] = []
        for i in self.instruments:
            if kind and i.kind != kind:
                continue
            if i.expiry is not None and i.expiry < today:
                continue
            sym, nm = i.symbol.lower(), i.name.lower()
            hay = f"{sym} {nm}"
            if any(t not in hay for t in tokens):
                continue
            if not tokens:
                rank = 4 if not (prefer and prefer(i)) else -1
            elif sym == q:
                rank = 0
            elif sym.startswith(q):
                rank = 1
            elif nm.startswith(q):
                rank = 2
            else:
                rank = 3
            scored.append(
                (
                    (
                        rank,
                        _KIND_RANK[i.kind],
                        i.expiry or date.max,
                        i.strike or 0.0,
                        i.instrument_type,
                        i.symbol,
                    ),
                    i,
                )
            )
        scored.sort(key=lambda p: p[0])
        return [i for _, i in scored[: max(1, limit)]]

    def front_future(self, today: date | None = None) -> Instrument | None:
        """The current Nifty futures contract: nearest expiry that has not passed."""
        today = today or ist_now().date()
        futs = [i for i in self.instruments if i.kind == "future" and i.expiry and i.expiry >= today]
        return min(futs, key=lambda i: i.expiry or date.max) if futs else None


_cache: dict[tuple[Path, float], InstrumentIndex] = {}


def current_index(base: Path) -> InstrumentIndex | None:
    """Index of the newest snapshot (cached by file mtime); None if there is no snapshot yet."""
    latest = latest_snapshot(base)
    if latest is None:
        return None
    day, path = latest
    cache_key = (path, path.stat().st_mtime)
    hit = _cache.get(cache_key)
    if hit is None:
        _cache.clear()
        hit = InstrumentIndex(load_instruments(path), day)
        _cache[cache_key] = hit
    return hit
