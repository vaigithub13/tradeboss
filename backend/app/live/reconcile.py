"""Reconcile live-built bars with the official history, and recover days that were missed.

* `reconcile_today`  - at/after 15:45 IST: fetch official 1m for the day, replace our bars, log
                       every difference (engine bars AND the persisted parquet).
* `reconcile_missed` - on startup: any past day still marked unreconciled is reconciled from the
                       historical API against what was persisted.
"""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from app.live.backfill import raw_to_bars
from app.live.engine import LiveEngine
from app.live.minutelog import append_jsonl, reconcile_path
from app.live.model import Bar, Diff, diff_fields, fmt_minute
from app.live.persist import ReconcileState, is_stored, replace_day, stored_day
from app.upstox.client import parse_candles

log = logging.getLogger("tradeboss.live.reconcile")


class _Historical(Protocol):
    def historical_candles(self, instrument_key: str, from_date: date, to_date: date, *, unit: str = ..., interval: int = ...) -> list[Any]: ...


@dataclass
class ReconcileReport:
    day: date
    key: str
    official_bars: int = 0
    diffs: list[Diff] = field(default_factory=list)
    replaced_rows: int = 0
    ok: bool = True
    error: str | None = None


def fetch_official(client: _Historical, key: str, day: date) -> list[Bar]:
    rows = client.historical_candles(key, day, day, unit="minutes", interval=1)
    return raw_to_bars(parse_candles(rows), day, "official")


def compare_sets(key: str, ours: dict[int, Bar], official: dict[int, Bar]) -> list[Diff]:
    """Every minute where `ours` (any source) differs from official: changed / added / removed."""
    out: list[Diff] = []
    if not official:
        return out
    lo, hi = min(official), max(official)
    for m in sorted(official):
        o = official[m]
        mine = ours.get(m)
        if mine is None:
            out.append(Diff(key, m, "added", None, "official"))
        else:
            f = diff_fields(mine, o)
            if f:
                out.append(Diff(key, m, "changed", mine.source, "official", f))
    for m in sorted(x for x in ours if x not in official and lo <= x <= hi):
        out.append(Diff(key, m, "removed", ours[m].source, "official"))
    return out


def _log_diffs(log_dir: Path, day: date, kind: str, reports: list[ReconcileReport]) -> None:
    rows: list[dict] = []
    for r in reports:
        for d in r.diffs:
            rows.append({"day": day.isoformat(), "mode": kind, **d.as_dict()})
        rows.append({
            "day": day.isoformat(), "mode": kind, "summary": True, "key": r.key, "ok": r.ok, "error": r.error,
            "official_bars": r.official_bars, "differences": len(r.diffs), "replaced_rows": r.replaced_rows,
        })
    append_jsonl(reconcile_path(log_dir, day.isoformat()), rows)


def reconcile_today(
    engine: LiveEngine,
    client: _Historical,
    *,
    candles_dir: Path,
    log_dir: Path,
    state: ReconcileState,
    extra_keys: list[str] | None = None,
    engine_lock: AbstractContextManager[Any] | None = None,
) -> list[ReconcileReport]:
    """Reconcile every instrument the engine has today (plus `extra_keys`, e.g. subscribed
    instruments that never produced a tick because we connected after the close). Instruments
    whose fetch fails stay marked unreconciled (retried later and at the next startup)."""
    day = engine.day
    assert day is not None
    reports: list[ReconcileReport] = []
    for key in list(dict.fromkeys([*engine.keys(), *(extra_keys or [])])):
        rep = ReconcileReport(day, key)
        reports.append(rep)
        try:
            official = fetch_official(client, key, day)
        except Exception as exc:  # noqa: BLE001 - network / auth / parse: keep it pending
            rep.ok, rep.error = False, f"{type(exc).__name__}: {exc}"
            log.warning("reconcile %s %s failed: %s", day, key, exc)
            continue
        rep.official_bars = len(official)
        if not official:
            rep.ok, rep.error = False, "official history has no bars yet"
            continue
        with engine_lock or nullcontext():
            in_engine = key in engine.builders
            if in_engine:
                rep.diffs = engine.apply_official(key, official)
        if not in_engine:
            rep.diffs = compare_sets(key, stored_day(candles_dir, key, day), {b.minute: b for b in official})
        if is_stored(candles_dir, key):
            keep_oi = "_FO" in key.split("|", 1)[0]
            rep.replaced_rows = replace_day(candles_dir, key, day, official, keep_oi=keep_oi)
        state.clear(day, key)
        log.info("reconciled %s %s: %d official bars, %d differences", day, key, len(official), len(rep.diffs))
    _log_diffs(log_dir, day, "today", reports)
    return reports


def reconcile_missed(
    client: _Historical,
    *,
    candles_dir: Path,
    log_dir: Path,
    state: ReconcileState,
    today: date,
) -> list[ReconcileReport]:
    """Startup recovery: reconcile every past (day, instrument) still marked unreconciled."""
    reports: list[ReconcileReport] = []
    by_day: dict[date, list[ReconcileReport]] = {}
    for day, key in state.pending():
        if day >= today:
            continue  # today's session is handled by the running engine
        rep = ReconcileReport(day, key)
        by_day.setdefault(day, []).append(rep)
        reports.append(rep)
        try:
            official = fetch_official(client, key, day)
        except Exception as exc:  # noqa: BLE001
            rep.ok, rep.error = False, f"{type(exc).__name__}: {exc}"
            log.warning("missed reconcile %s %s failed: %s", day, key, exc)
            continue
        rep.official_bars = len(official)
        if not official:
            rep.ok, rep.error = False, "official history has no bars for that day"
            continue
        ours = stored_day(candles_dir, key, day)
        rep.diffs = compare_sets(key, ours, {b.minute: b for b in official})
        keep_oi = "_FO" in key.split("|", 1)[0]
        rep.replaced_rows = replace_day(candles_dir, key, day, official, keep_oi=keep_oi)
        state.clear(day, key)
        log.info("missed reconcile %s %s: %d official bars, %d differences", day, key, len(official), len(rep.diffs))
    for day, reps in by_day.items():
        _log_diffs(log_dir, day, "missed", reps)
    return reports


def describe(d: Diff) -> str:
    return f"{fmt_minute(d.minute)} {d.key} {d.kind} {d.ours_source}->{d.theirs_source} {d.fields}"
