"""Persist live bars into the 1-minute Parquet and track which (day, instrument) is unreconciled."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from app.data.history import merge_window, read_parquet, symbol_dir_name, write_parquet_atomic
from app.data.importer import COLUMNS, build_frame
from app.live.model import MS_MIN, Bar

log = logging.getLogger("tradeboss.live.persist")


def parquet_path(candles_dir: Path, key: str) -> Path:
    return candles_dir / symbol_dir_name(key) / "1m.parquet"


def is_stored(candles_dir: Path, key: str) -> bool:
    return parquet_path(candles_dir, key).is_file()


def bars_to_frame(bars: Iterable[Bar], *, keep_oi: bool) -> pd.DataFrame:
    raw = [
        {"t": b.minute * MS_MIN, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
         "volume": 0.0 if b.volume is None else b.volume, "oi": b.oi}
        for b in bars
    ]
    df, _ = build_frame(raw, 1, keep_oi=keep_oi)
    return df


def upsert_bars(candles_dir: Path, key: str, bars: list[Bar], *, keep_oi: bool = False) -> int:
    """Add / overwrite the given minutes (NOT a whole-day replace). Only for symbols that are
    already stored locally. Returns the number of rows written."""
    path = parquet_path(candles_dir, key)
    if not path.is_file() or not bars:
        return 0
    new = bars_to_frame(bars, keep_oi=keep_oi)
    if new.empty:
        return 0
    existing = read_parquet(path)
    merged = pd.concat([existing, new], ignore_index=True) if len(existing) else new
    merged = merged.drop_duplicates(subset="time", keep="last").sort_values("time", ignore_index=True)
    merged["oi"] = merged["oi"].astype("float64")
    write_parquet_atomic(merged[COLUMNS], path)
    return len(new)


def replace_day(candles_dir: Path, key: str, day: date, bars: list[Bar], *, keep_oi: bool = False) -> int:
    """Replace everything stored for `day` with `bars` (the official reconcile)."""
    path = parquet_path(candles_dir, key)
    if not path.is_file() or not bars:
        return 0
    new = bars_to_frame(bars, keep_oi=keep_oi)
    merged = merge_window(read_parquet(path), new, (day, day))
    write_parquet_atomic(merged, path)
    return len(new)


def stored_day(candles_dir: Path, key: str, day: date) -> dict[int, Bar]:
    """The persisted bars of one IST day as {minute: Bar(source='stored')}."""
    path = parquet_path(candles_dir, key)
    if not path.is_file():
        return {}
    df = read_parquet(path)
    from app.data.history import day_start_ts

    lo = day_start_ts(day)
    df = df[(df["time"] >= lo) & (df["time"] < lo + 86_400)]
    out: dict[int, Bar] = {}
    for r in df.itertuples(index=False):
        m = int(r.time) // 60
        oi = None if pd.isna(r.oi) else float(r.oi)
        out[m] = Bar(m, float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume), oi, "tick")
    return out


# A day moves forward only: live bars land as pending, the 15:45 intraday pass advances
# them, and the historical pass (next startup or 09:00) marks them final.
_STAGE_RANK = {"pending": 0, "intraday_reconciled": 1, "final": 2}
Stage = str


class ReconcileState:
    """`data/live-state/reconcile.json`: per (day, instrument) stage.

    ``pending`` — live bars stored, no official pass yet.
    ``intraday_reconciled`` — replaced from today's intraday candles; historical still due.
    ``final`` — replaced from the historical candles. Not retried.

    Older files that only list ``pending`` keys are read as stage ``pending``.
    """

    def __init__(self, directory: Path) -> None:
        self.path = directory / "reconcile.json"
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (ValueError, AttributeError):
            log.warning("unreadable %s - treating as empty", self.path)
            return {}
        status = data.get("status")
        if isinstance(status, dict):
            out: dict[str, dict[str, str]] = {}
            for d, keys in status.items():
                if isinstance(keys, dict):
                    out[str(d)] = {str(k): str(v) for k, v in keys.items() if v in _STAGE_RANK}
            return out
        pending = data.get("pending", {})
        if not isinstance(pending, dict):
            return {}
        return {str(d): {str(k): "pending" for k in ks} for d, ks in pending.items()}

    def _write(self, status: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"status": status}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _set(self, day: date, key: str, stage: str) -> None:
        with self._lock:
            p = self._read()
            day_keys = p.setdefault(day.isoformat(), {})
            current = day_keys.get(key)
            if current is not None and _STAGE_RANK[current] >= _STAGE_RANK[stage]:
                return
            day_keys[key] = stage
            self._write(p)

    def mark(self, day: date, key: str) -> None:
        """Live bars were stored. Does not move a later stage backwards."""
        self._set(day, key, "pending")

    def mark_intraday(self, day: date, key: str) -> None:
        self._set(day, key, "intraday_reconciled")

    def mark_final(self, day: date, key: str) -> None:
        self._set(day, key, "final")

    def clear(self, day: date, key: str) -> None:
        with self._lock:
            p = self._read()
            day_keys = p.get(day.isoformat(), {})
            day_keys.pop(key, None)
            if day_keys:
                p[day.isoformat()] = day_keys
            else:
                p.pop(day.isoformat(), None)
            self._write(p)

    def status(self, day: date, key: str) -> str | None:
        with self._lock:
            return self._read().get(day.isoformat(), {}).get(key)

    def pending(self) -> list[tuple[date, str]]:
        """Still waiting for the intraday pass."""
        with self._lock:
            p = self._read()
        return [(date.fromisoformat(d), k) for d in sorted(p) for k, stage in p[d].items() if stage == "pending"]

    def not_final(self) -> list[tuple[date, str, str]]:
        """Every (day, key, stage) that is not yet historical-final. Retried daily."""
        with self._lock:
            p = self._read()
        return [
            (date.fromisoformat(d), k, stage)
            for d in sorted(p)
            for k, stage in p[d].items()
            if stage != "final"
        ]
