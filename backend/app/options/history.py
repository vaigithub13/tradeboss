"""Real option 1m candles for the model calibration, from two kinds of source.

* `UpstoxHistorySource`   Upstox's expired-contract history (past expiries) and the normal historical
                          API (contracts that are still listed). Needs no live feed.
* `RecordedSource`        candles we stored ourselves (an end-of-day capture of the listed contracts, or a
                          live recorder later) - the history that keeps growing every week and survives
                          Upstox's limits.

Both write to / read from `OptionHistoryStore` (one Parquet file per expiry under data/option_history/), so
the calibration does not care where a candle came from; `merge_bars` resolves overlaps by source preference.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import duckdb
import pandas as pd

from app.options.strikes import KINDS, atm_strike
from app.upstox.client import UpstoxError, parse_candles
from app.upstox.instruments import InstrumentIndex

log = logging.getLogger("tradeboss.options.history")
IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_KEY = "NSE_INDEX|Nifty 50"
MAX_WINDOW_DAYS = 30  # Upstox limits 1-minute requests to one month


@dataclass(frozen=True)
class ContractRef:
    strike: float
    kind: str  # CE | PE
    key: str  # expired key (NSE_FO|58548|03-10-2024) or the live instrument key
    symbol: str
    lot_size: int


@dataclass(frozen=True)
class OptionBar:
    time: int  # unix seconds, bar start
    expiry: date
    strike: float
    kind: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float | None
    source: str


class OptionHistorySource(Protocol):
    name: str

    def expiries(self) -> list[date]: ...

    def contracts(self, expiry: date) -> list[ContractRef]: ...

    def candles(self, ref: ContractRef, expiry: date, from_date: date, to_date: date) -> list[dict[str, Any]]: ...


# ------------------------------------------------------------------------------------------ store
class OptionHistoryStore:
    """<base>/NIFTY_<expiry>.parquet (all candles of that expiry) + NIFTY_<expiry>.done.json (what was asked)."""

    COLUMNS = ["time", "strike", "kind", "open", "high", "low", "close", "volume", "oi", "source", "key", "symbol", "lot_size"]

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self._opens: dict[tuple[date, float, str], dict[int, float]] = {}

    def _pq(self, expiry: date) -> Path:
        return self.base_dir / f"NIFTY_{expiry.isoformat()}.parquet"

    def _ledger(self, expiry: date) -> Path:
        return self.base_dir / f"NIFTY_{expiry.isoformat()}.done.json"

    def _read_ledger(self, expiry: date) -> dict[str, str]:
        p = self._ledger(expiry)
        return json.loads(p.read_text()) if p.exists() else {}

    def _atomic(self, path: Path, write: Callable[[Path], None]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        os.close(fd)
        try:
            write(Path(tmp))
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    @staticmethod
    def _lk(strike: float, kind: str) -> str:
        return f"{strike:g}|{kind}"

    def have(self, expiry: date) -> set[tuple[float, str]]:
        out = set()
        for k in self._read_ledger(expiry):
            s, kind = k.split("|")
            out.add((float(s), kind))
        return out

    def mark(self, expiry: date, strike: float, kind: str, status: str) -> None:
        ledger = self._read_ledger(expiry)
        ledger[self._lk(strike, kind)] = status
        payload = json.dumps(ledger, sort_keys=True)

        def _write(p: Path) -> None:
            p.write_text(payload)

        self._atomic(self._ledger(expiry), _write)

    def write(self, expiry: date, ref: ContractRef, bars: Sequence[dict[str, Any]], source: str) -> None:
        """Store the candles of one contract (replacing any stored earlier for it) and remember it was done."""
        old = self._frame(expiry)
        if len(old):
            old = old[~((old["strike"] == ref.strike) & (old["kind"] == ref.kind))]
        new = pd.DataFrame(
            {
                "time": [int(b["t"]) // 1000 for b in bars], "strike": ref.strike, "kind": ref.kind,
                "open": [float(b["open"]) for b in bars], "high": [float(b["high"]) for b in bars],
                "low": [float(b["low"]) for b in bars], "close": [float(b["close"]) for b in bars],
                "volume": [float(b["volume"]) for b in bars],
                "oi": [None if b.get("oi") is None else float(b["oi"]) for b in bars],
                "source": source, "key": ref.key, "symbol": ref.symbol, "lot_size": ref.lot_size,
            },
            columns=self.COLUMNS,
        )
        frame = pd.concat([old, new], ignore_index=True) if len(old) else new
        if len(frame):
            frame = frame.sort_values(["strike", "kind", "time"]).reset_index(drop=True)
            self._atomic(self._pq(expiry), lambda p: _write_parquet(frame, p))
        self.mark(expiry, ref.strike, ref.kind, "done")

    def _frame(self, expiry: date) -> pd.DataFrame:
        p = self._pq(expiry)
        if not p.exists():
            return pd.DataFrame(columns=self.COLUMNS)
        con = duckdb.connect()
        try:
            return con.execute(f"SELECT * FROM read_parquet('{str(p).replace(chr(39), chr(39) * 2)}')").df()
        finally:
            con.close()

    def read(self, expiry: date) -> list[OptionBar]:
        df = self._frame(expiry)
        out = []
        for r in df.itertuples(index=False):
            oi = None if pd.isna(r.oi) else float(r.oi)
            out.append(OptionBar(int(r.time), expiry, float(r.strike), str(r.kind), float(r.open), float(r.high),
                                 float(r.low), float(r.close), float(r.volume), oi, str(r.source)))
        return out

    def refs(self, expiry: date) -> list[ContractRef]:
        df = self._frame(expiry)
        if not len(df):
            return []
        d = df.drop_duplicates(["strike", "kind"]).sort_values(["strike", "kind"])
        return [ContractRef(float(r.strike), str(r.kind), str(r.key), str(r.symbol), int(r.lot_size)) for r in d.itertuples(index=False)]

    def premium_at(self, expiry: date, strike: float, kind: str, time: int) -> float | None:
        """1m open of this contract at `time` (bar start), or None when that minute is not stored."""
        key = (expiry, float(strike), kind)
        cached = self._opens.get(key)
        if cached is None:
            cached = self._load_opens(expiry, float(strike), kind)
            self._opens[key] = cached
        return cached.get(int(time))

    def _load_opens(self, expiry: date, strike: float, kind: str) -> dict[int, float]:
        path = self._pq(expiry)
        if not path.exists():
            return {}
        q = str(path).replace("'", "''")
        con = duckdb.connect()
        try:
            rows = con.execute(
                f"SELECT time, open FROM read_parquet('{q}') WHERE kind = ? AND abs(strike - ?) < 1e-6",
                [kind, strike],
            ).fetchall()
        finally:
            con.close()
        return {int(t): float(o) for t, o in rows}

    def expiries(self) -> list[date]:
        if not self.base_dir.is_dir():
            return []
        return sorted(date.fromisoformat(p.stem[6:]) for p in self.base_dir.glob("NIFTY_*.parquet"))


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    con = duckdb.connect()
    try:
        con.register("df", df)
        con.execute(f"COPY (SELECT * FROM df) TO '{str(path).replace(chr(39), chr(39) * 2)}' (FORMAT PARQUET)")
    finally:
        con.close()


def merge_bars(lists: Iterable[Sequence[OptionBar]]) -> list[OptionBar]:
    """One candle per (expiry, strike, kind, minute); when several lists have it, the EARLIER list wins."""
    seen: dict[tuple[date, float, str, int], OptionBar] = {}
    for bars in lists:
        for b in bars:
            seen.setdefault((b.expiry, b.strike, b.kind, b.time), b)
    return [seen[k] for k in sorted(seen)]


# ------------------------------------------------------------------------------------------ sources
class UpstoxHistorySource:
    name = "upstox"

    def __init__(self, client: Any, instruments: InstrumentIndex | None, *, today: date,
                 underlying_key: str = NIFTY_KEY) -> None:
        self.client, self.instruments, self.today, self.underlying_key = client, instruments, today, underlying_key

    def _expired(self, expiry: date) -> bool:
        return expiry < self.today

    def expiries(self) -> list[date]:
        return sorted(self.client.expired_expiries(self.underlying_key))

    def contracts(self, expiry: date) -> list[ContractRef]:
        if self._expired(expiry):
            rows = self.client.expired_option_contracts(self.underlying_key, expiry)
            return [
                ContractRef(float(r["strike_price"]), str(r["instrument_type"]), str(r["instrument_key"]),
                            str(r.get("trading_symbol", "")), int(r.get("lot_size") or 0))
                for r in rows if r.get("instrument_type") in KINDS
            ]
        if self.instruments is None:
            return []
        return [
            ContractRef(float(i.strike or 0), i.instrument_type, i.key, i.symbol, int(i.lot_size or 0))
            for i in self.instruments.instruments
            if i.kind == "option" and i.expiry == expiry and i.underlying_key == self.underlying_key and i.instrument_type in KINDS
        ]

    def candles(self, ref: ContractRef, expiry: date, from_date: date, to_date: date) -> list[dict[str, Any]]:
        rows: list[Any] = []
        start = from_date
        while start <= to_date:
            end = min(to_date, start + timedelta(days=MAX_WINDOW_DAYS - 1))
            if self._expired(expiry):
                rows += self.client.expired_historical_candles(ref.key, start, end)
            else:
                rows += self.client.historical_candles(ref.key, start, end, unit="minutes", interval=1)
            start = end + timedelta(days=1)
        return parse_candles(rows)

    @property
    def source_label(self) -> Callable[[date], str]:
        return lambda expiry: "upstox-expired" if self._expired(expiry) else "upstox-active"


class RecordedSource:
    """Candles we stored ourselves (nothing is fetched)."""

    name = "recorded"

    def __init__(self, store: OptionHistoryStore) -> None:
        self.store = store

    def expiries(self) -> list[date]:
        return self.store.expiries()

    def contracts(self, expiry: date) -> list[ContractRef]:
        return self.store.refs(expiry)

    def candles(self, ref: ContractRef, expiry: date, from_date: date, to_date: date) -> list[dict[str, Any]]:
        lo = int(datetime(from_date.year, from_date.month, from_date.day, tzinfo=IST).timestamp())
        hi = int(datetime(to_date.year, to_date.month, to_date.day, tzinfo=IST).timestamp()) + 86_400
        return [
            {"t": b.time * 1000, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume, "oi": b.oi}
            for b in self.store.read(expiry)
            if b.strike == ref.strike and b.kind == ref.kind and lo <= b.time < hi
        ]


# ------------------------------------------------------------------------------------------ planning / fetching
def plan_strikes(ranges: dict[date, tuple[float, float]], step: int, window: int = 1) -> list[float]:
    """Strikes needed so that ATM +/- `window` is covered at every minute: per day from ATM(low)-window to ATM(high)+window."""
    need: set[float] = set()
    for low, high in ranges.values():
        k = atm_strike(low, step) - window * step
        end = atm_strike(high, step) + window * step
        while k <= end + 1e-9:
            need.add(k)
            k += step
    return sorted(need)


def trading_days_before(expiry: date, n: int, is_trading_day: Callable[[date], bool]) -> list[date]:
    """The last `n` trading days up to and including `expiry` (oldest first)."""
    days: list[date] = []
    d = expiry
    while len(days) < n:
        if is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
        if (expiry - d).days > 10 * n:
            break
    return sorted(days)


@dataclass
class FetchStats:
    fetched: int = 0
    skipped: int = 0
    not_listed: int = 0
    failed: int = 0
    empty: int = 0


def fetch_expiry(source: Any, store: OptionHistoryStore, expiry: date, strikes: Sequence[float], days: Sequence[date],
                 kinds: Sequence[str] = KINDS) -> FetchStats:
    """Fetch every wanted (strike, kind) of one expiry that the store does not have yet."""
    stats = FetchStats()
    wanted = [(s, k) for s in strikes for k in kinds]
    done = store.have(expiry)
    todo = [w for w in wanted if w not in done]
    stats.skipped = len(wanted) - len(todo)
    if not todo:
        return stats
    listed = {(r.strike, r.kind): r for r in source.contracts(expiry)}
    label = source.source_label(expiry) if hasattr(source, "source_label") else getattr(source, "name", "unknown")
    for strike, kind in todo:
        ref = listed.get((strike, kind))
        if ref is None:
            store.mark(expiry, strike, kind, "not_listed")
            stats.not_listed += 1
            continue
        try:
            bars = source.candles(ref, expiry, days[0], days[-1])
        except UpstoxError as exc:  # the next run tries this contract again
            log.warning("fetch %s %s %s failed: %s", expiry, strike, kind, type(exc).__name__)
            stats.failed += 1
            continue
        store.write(expiry, ref, bars, label)
        stats.fetched += 1
        stats.empty += 0 if bars else 1
    return stats


def capture_listed_day(
    source: Any,
    store: OptionHistoryStore,
    day: date,
    expiry: date,
    strikes: Sequence[float],
    *,
    label: str = "live-recorded",
) -> FetchStats:
    """One session of currently listed contracts (source b). Merges into what the store already has.

    The live feed is not touched: this is the end-of-day / historical-API path that starts growing
    the recorded store from Monday. The same `store.write` is what a later live recorder would call.
    """
    stats = FetchStats()
    listed = {(r.strike, r.kind): r for r in source.contracts(expiry)}
    for strike in strikes:
        for kind in KINDS:
            ref = listed.get((strike, kind))
            if ref is None:
                stats.not_listed += 1
                continue
            try:
                bars = source.candles(ref, expiry, day, day)
            except UpstoxError:
                stats.failed += 1
                continue
            existing = [
                {"t": b.time * 1000, "open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "volume": b.volume, "oi": b.oi}
                for b in store.read(expiry)
                if b.strike == ref.strike and b.kind == ref.kind
            ]
            seen = {b["t"] for b in existing}
            merged = existing + [b for b in bars if b["t"] not in seen]
            store.write(expiry, ref, merged, label)
            stats.fetched += 1
            stats.empty += 0 if bars else 1
    return stats


def default_history_store() -> OptionHistoryStore:
    """`data/option_history` next to the repo root."""
    return OptionHistoryStore(Path(__file__).resolve().parents[3] / "data" / "option_history")


def ingest_recorded_bars(
    store: OptionHistoryStore, expiry: date, ref: ContractRef, bars: Sequence[dict[str, Any]],
    *, label: str = "live-recorded",
) -> None:
    """What a Monday live recorder calls: write already-built 1m bars into the same store."""
    store.write(expiry, ref, bars, label)
