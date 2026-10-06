"""Reconcile live-built bars with official candles, in two stages.

* 15:45 IST (`reconcile_today`): today's INTRADAY candles replace what we stored. The day is
  marked ``intraday_reconciled``. Retried until 16:30 while the fetch is empty or fails.
* Next startup, or 09:00 (`reconcile_missed`): HISTORICAL candles replace the day again and
  it is marked ``final``. Any day that is not final is retried daily.

Futures minutes 15:30-15:39, and open-interest-only changes on minutes whose prices match,
are expected. They are logged apart from differences and are not counted as differences.
The post-close futures minutes are not written into the store.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from app.live.backfill import raw_to_bars
from app.live.engine import LiveEngine, segment_of
from app.live.minutelog import append_jsonl, reconcile_path
from app.live.model import Bar, Diff, diff_fields, fmt_minute, ist_minute_of_day
from app.live.persist import ReconcileState, is_stored, replace_day, stored_day
from app.upstox.client import parse_candles

log = logging.getLogger("tradeboss.live.reconcile")


class _Historical(Protocol):
    def historical_candles(self, instrument_key: str, from_date: date, to_date: date, *, unit: str = ..., interval: int = ...) -> list[Any]: ...

    def intraday_candles(self, instrument_key: str, *, unit: str = ..., interval: int = ...) -> list[Any]: ...


# Futures keep printing through the closing auction. Those minutes are not session bars.
_POST_CLOSE = range(15 * 60 + 30, 15 * 60 + 40)


@dataclass
class ReconcileReport:
    day: date
    key: str
    official_bars: int = 0
    diffs: list[Diff] = field(default_factory=list)
    expected: list[Diff] = field(default_factory=list)
    replaced_rows: int = 0
    ok: bool = True
    error: str | None = None


def fetch_official(client: _Historical, key: str, day: date) -> list[Bar]:
    rows = client.historical_candles(key, day, day, unit="minutes", interval=1)
    return raw_to_bars(parse_candles(rows), day, "official")


def fetch_intraday(client: _Historical, key: str, day: date) -> list[Bar]:
    """Official 1m bars of the current trading day (the intraday endpoint)."""
    rows = client.intraday_candles(key, unit="minutes", interval=1)
    return raw_to_bars(parse_candles(rows), day, "official")


def futures_post_close(key: str, minute: int) -> bool:
    return "_FO" in segment_of(key) and ist_minute_of_day(minute) in _POST_CLOSE


def split_expected(key: str, diffs: list[Diff]) -> tuple[list[Diff], list[Diff]]:
    """Real differences, then the ones 5 Oct showed are normal.

    Expected: a futures bar that exists only in official data at 15:30-15:39, and an
    open-interest change on a minute whose prices (and volume) already match.
    """
    real: list[Diff] = []
    expected: list[Diff] = []
    for d in diffs:
        oi_only = d.kind == "changed" and bool(d.fields) and set(d.fields) <= {"oi"}
        post_close = d.kind == "added" and futures_post_close(key, d.minute)
        (expected if oi_only or post_close else real).append(d)
    return real, expected


def session_bars(key: str, bars: list[Bar]) -> list[Bar]:
    """Official bars we keep. Futures 15:30-15:39 stay out of the store."""
    return [b for b in bars if not futures_post_close(key, b.minute)]


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
            rows.append({"day": day.isoformat(), "mode": kind, "expected": False, **d.as_dict()})
        for d in r.expected:
            rows.append({"day": day.isoformat(), "mode": kind, "expected": True, **d.as_dict()})
        rows.append({
            "day": day.isoformat(), "mode": kind, "summary": True, "key": r.key, "ok": r.ok, "error": r.error,
            "official_bars": r.official_bars, "differences": len(r.diffs), "expected": len(r.expected),
            "replaced_rows": r.replaced_rows,
        })
    append_jsonl(reconcile_path(log_dir, day.isoformat()), rows)


def _ours(engine: LiveEngine | None, candles_dir: Path, key: str, day: date) -> dict[int, Bar]:
    if engine is not None and key in engine.builders:
        return {b.minute: b for b in engine.bars(key, include_withheld=True)}
    return stored_day(candles_dir, key, day)


def _apply(
    engine: LiveEngine | None,
    candles_dir: Path,
    key: str,
    day: date,
    official: list[Bar],
    *,
    engine_lock: AbstractContextManager[Any] | None,
) -> tuple[list[Diff], list[Diff], int]:
    """Compare, replace the session bars, and split the report into differences and expected."""
    real, expected = split_expected(key, compare_sets(key, _ours(engine, candles_dir, key, day), {b.minute: b for b in official}))
    keep = session_bars(key, official)
    with engine_lock or nullcontext():
        if engine is not None and key in engine.builders:
            engine.apply_official(key, keep)
    replaced = 0
    if is_stored(candles_dir, key):
        keep_oi = "_FO" in segment_of(key)
        replaced = replace_day(candles_dir, key, day, keep, keep_oi=keep_oi)
    return real, expected, replaced


def reconcile_today(
    engine: LiveEngine,
    client: _Historical,
    *,
    candles_dir: Path,
    log_dir: Path,
    state: ReconcileState,
    extra_keys: list[str] | None = None,
    engine_lock: AbstractContextManager[Any] | None = None,
    keep: Callable[[str], bool] | None = None,
) -> list[ReconcileReport]:
    """15:45: intraday candles for every instrument the engine has today (plus `extra_keys`), except those
    `keep` rejects (contracts subscribed only for their depth, which have no candles of their own).

    A failed or empty fetch stays ``pending`` and is retried. Success marks ``intraday_reconciled``.
    """
    day = engine.day
    assert day is not None
    keys = list(dict.fromkeys([*engine.keys(), *(extra_keys or [])]))
    if keep is not None:
        keys = [k for k in keys if keep(k)]
    return _reconcile_intraday(
        client, day, keys,
        candles_dir=candles_dir, log_dir=log_dir, state=state, engine=engine, engine_lock=engine_lock,
    )


def reconcile_intraday_day(
    client: _Historical,
    day: date,
    keys: list[str],
    *,
    candles_dir: Path,
    log_dir: Path,
    state: ReconcileState,
) -> list[ReconcileReport]:
    """The 15:45 pass for one day, against what is already stored (no live engine)."""
    return _reconcile_intraday(
        client, day, keys, candles_dir=candles_dir, log_dir=log_dir, state=state, engine=None, engine_lock=None,
    )


def _reconcile_intraday(
    client: _Historical,
    day: date,
    keys: list[str],
    *,
    candles_dir: Path,
    log_dir: Path,
    state: ReconcileState,
    engine: LiveEngine | None,
    engine_lock: AbstractContextManager[Any] | None,
) -> list[ReconcileReport]:
    reports: list[ReconcileReport] = []
    for key in keys:
        if state.status(day, key) == "final":
            continue
        rep = ReconcileReport(day, key)
        reports.append(rep)
        try:
            official = fetch_intraday(client, key, day)
        except Exception as exc:  # noqa: BLE001 - network / auth / parse: stay pending
            rep.ok, rep.error = False, f"{type(exc).__name__}: {exc}"
            log.warning("intraday reconcile %s %s failed: %s", day, key, exc)
            continue
        rep.official_bars = len(official)
        if not official:
            rep.ok, rep.error = False, "intraday history has no bars yet"
            continue
        rep.diffs, rep.expected, rep.replaced_rows = _apply(
            engine, candles_dir, key, day, official, engine_lock=engine_lock,
        )
        state.mark_intraday(day, key)
        log.info(
            "intraday reconciled %s %s: %d official bars, %d differences, %d expected",
            day, key, len(official), len(rep.diffs), len(rep.expected),
        )
    _log_diffs(log_dir, day, "intraday", reports)
    return reports


def reconcile_missed(
    client: _Historical,
    *,
    candles_dir: Path,
    log_dir: Path,
    state: ReconcileState,
    today: date,
    only_day: date | None = None,
) -> list[ReconcileReport]:
    """Historical pass. Days that are not ``final`` are retried.

    Startup and the 09:00 job pass no ``only_day`` and skip the session still in progress
    (``day >= today``). A manual run passes ``only_day`` so today is attempted too; an empty
    historical response leaves the intraday mark in place.
    """
    reports: list[ReconcileReport] = []
    by_day: dict[date, list[ReconcileReport]] = {}
    for day, key, _stage in state.not_final():
        if only_day is not None and day != only_day:
            continue
        if only_day is None and day >= today:
            continue
        rep = ReconcileReport(day, key)
        by_day.setdefault(day, []).append(rep)
        reports.append(rep)
        try:
            official = fetch_official(client, key, day)
        except Exception as exc:  # noqa: BLE001
            rep.ok, rep.error = False, f"{type(exc).__name__}: {exc}"
            log.warning("historical reconcile %s %s failed: %s", day, key, exc)
            continue
        rep.official_bars = len(official)
        if not official:
            rep.ok, rep.error = False, "official history has no bars yet"
            continue
        rep.diffs, rep.expected, rep.replaced_rows = _apply(
            None, candles_dir, key, day, official, engine_lock=None,
        )
        state.mark_final(day, key)
        log.info(
            "historical reconciled %s %s: %d official bars, %d differences, %d expected",
            day, key, len(official), len(rep.diffs), len(rep.expected),
        )
    for day, reps in by_day.items():
        _log_diffs(log_dir, day, "historical", reps)
    return reports


def describe(d: Diff) -> str:
    return f"{fmt_minute(d.minute)} {d.key} {d.kind} {d.ours_source}->{d.theirs_source} {d.fields}"
