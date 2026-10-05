"""Persistence, backfill fetch, overlay, official reconcile and the startup missed-reconcile (case 8)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.data.history import read_parquet, symbol_dir_name
from app.data.importer import build_frame, write_parquet
from app.data.store import CandleStore, set_overlay
from app.live.backfill import fetch_backfill
from app.live.engine import LiveEngine
from app.live.frames import FeedItem, encode_feed, encode_market_info
from app.live.model import IST, MS_MIN, Bar, minute_of
from app.live.overlay import LiveOverlay
from app.live.persist import ReconcileState, is_stored, parquet_path, stored_day, upsert_bars
from app.live.reconcile import compare_sets, reconcile_intraday_day, reconcile_missed, reconcile_today
from app.upstox.client import UpstoxAuthError

FRI = date(2026, 10, 2)
MON = date(2026, 10, 5)
KEY = "NSE_EQ|INE002A01018"
SEGS = {"NSE_EQ": "NORMAL_OPEN"}


def T(h: int, m: int, s: int = 0, day: date = MON) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000


def M(h: int, m: int, day: date = MON) -> int:
    return minute_of(T(h, m, day=day))


def iso(minute: int) -> str:
    return datetime.fromtimestamp(minute * 60, IST).isoformat()


def full_day_rows(day: date, base: float = 100.0, vol: float = 10.0) -> list[list[Any]]:
    """Upstox-style rows (newest first, like the API) for a full 375-minute session."""
    rows = [[iso(m), base, base + 1, base - 1, base + 0.5, vol, 0] for m in range(M(9, 15, day), M(15, 30, day))]
    return rows[::-1]


class FakeHistory:
    def __init__(
        self,
        by_day: dict[date, list[list[Any]]] | None = None,
        fail: Exception | None = None,
        *,
        intraday: list[list[Any]] | None = None,
    ) -> None:
        self.by_day = by_day or {}
        self.fail = fail
        self.calls: list[tuple[str, date, date]] = []
        self.intraday_rows: list[list[Any]] = list(intraday or [])

    def historical_candles(self, key: str, from_date: date, to_date: date, *, unit: str = "minutes", interval: int = 1) -> list[Any]:
        self.calls.append((key, from_date, to_date))
        if self.fail:
            raise self.fail
        assert from_date == to_date
        return list(self.by_day.get(from_date, []))

    def intraday_candles(self, key: str, *, unit: str = "minutes", interval: int = 1) -> list[Any]:
        if self.fail:
            raise self.fail
        return list(self.intraday_rows)


@pytest.fixture
def cdir(tmp_path: Path) -> Path:
    """candles dir with one stored symbol: a full Friday of 1m bars."""
    raw = [{"t": r[0] and int(datetime.fromisoformat(r[0]).timestamp()) * 1000, "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5]} for r in full_day_rows(FRI)]
    df, _ = build_frame(raw, 1)
    write_parquet(df, tmp_path / "candles" / symbol_dir_name(KEY) / "1m.parquet")
    return tmp_path / "candles"


def live_engine(minutes: range | None = None) -> LiveEngine:
    """An engine that saw Monday's session: ticks every minute 09:15-09:19 + a quiet gap, then the close."""
    e = LiveEngine()
    n = 0

    def send(raw: bytes) -> None:
        nonlocal n
        n += 1
        e.on_frame(raw, None, n)

    send(encode_market_info({"NSE_EQ": "PRE_OPEN_START"}, T(9, 5)))
    send(encode_feed([FeedItem(KEY, 99.0, T(15, 29, day=FRI), 1, 5, None, None, True)], T(9, 5), snapshot=True))
    send(encode_market_info(SEGS, T(9, 15)))
    for i, (m, px) in enumerate([(15, 100.0), (16, 100.5), (19, 101.0)]):
        send(encode_feed([FeedItem(KEY, px, T(9, m, 10), 1, 100 + i * 10, None, None, True)], T(9, m, 11)))
    send(encode_market_info({"NSE_EQ": "NORMAL_CLOSE"}, T(15, 30, 1)))
    assert e.builders[KEY].ended
    return e


def official_rows(day: date = MON) -> list[list[Any]]:
    return full_day_rows(day, base=100.0, vol=7.0)


# ---------------------------------------------------------------- persistence
def test_upsert_adds_minutes_without_replacing_the_rest_of_the_day_or_other_days(cdir: Path) -> None:
    before = read_parquet(parquet_path(cdir, KEY))
    e = live_engine()
    n = upsert_bars(cdir, KEY, e.bars(KEY))
    after = read_parquet(parquet_path(cdir, KEY))
    assert n == len(e.bars(KEY)) == 375
    assert len(after) == len(before) + n
    assert (after["time"].iloc[: len(before)].values == before["time"].values).all()  # Friday untouched
    assert set(after["session_type"]) <= {"normal", "weekend_full", "special_short"}


def test_upsert_twice_is_idempotent_and_later_values_win(cdir: Path) -> None:
    e = live_engine()
    upsert_bars(cdir, KEY, e.bars(KEY))
    once = read_parquet(parquet_path(cdir, KEY))
    changed = [Bar(M(9, 15), 1, 2, 0.5, 1.5, 3.0, None, "official")]
    upsert_bars(cdir, KEY, e.bars(KEY))
    assert read_parquet(parquet_path(cdir, KEY)).equals(once)
    upsert_bars(cdir, KEY, changed)
    row = read_parquet(parquet_path(cdir, KEY))
    assert float(row[row["time"] == M(9, 15) * 60]["open"].iloc[0]) == 1.0
    assert len(row) == len(once)


def test_a_symbol_that_is_not_stored_locally_is_not_created_by_the_live_feed(cdir: Path) -> None:
    assert not is_stored(cdir, "NSE_EQ|INE999Z01010")
    assert upsert_bars(cdir, "NSE_EQ|INE999Z01010", [Bar(M(9, 15), 1, 1, 1, 1, 1.0, None, "tick")]) == 0
    assert not parquet_path(cdir, "NSE_EQ|INE999Z01010").exists()


def test_unknown_volume_is_stored_as_zero_until_reconcile(cdir: Path) -> None:
    upsert_bars(cdir, KEY, [Bar(M(9, 15), 1, 2, 1, 2, None, None, "tick", partial=True)])
    assert stored_day(cdir, KEY, MON)[M(9, 15)].volume == 0.0


# ---------------------------------------------------------------- overlay -> store
def test_the_overlay_merges_into_candle_reads_and_time_range(cdir: Path) -> None:
    store = CandleStore(cdir)
    ov = LiveOverlay()
    last_friday = max(c["time"] for c in store.load(symbol_dir_name(KEY))[0])
    ov.replace(symbol_dir_name(KEY), [Bar(M(9, 15), 100, 101, 99, 100.5, 12.0, None, "tick"),
                                      Bar(M(9, 16), 100.5, 102, 100, 101, None, None, "tick")])
    set_overlay(ov.rows)
    try:
        candles, minutes = store.load(symbol_dir_name(KEY))
        assert minutes == 1 and candles[-1]["time"] == M(9, 16) * 60 and candles[-2]["time"] == M(9, 15) * 60
        assert candles[-1]["volume"] == 0.0 and len(candles) == 375 + 2
        assert store.time_range(symbol_dir_name(KEY))[1] == M(9, 16) * 60
        only_old, _ = store.load(symbol_dir_name(KEY), to_time=last_friday)
        assert len(only_old) == 375
        none, _ = store.load(symbol_dir_name(KEY), session_types=["muhurat"])
        assert none == []
    finally:
        set_overlay(None)
    assert store.load(symbol_dir_name(KEY))[0][-1]["time"] == last_friday


def test_an_overlay_row_replaces_a_stored_row_with_the_same_time(cdir: Path) -> None:
    store = CandleStore(cdir)
    d = symbol_dir_name(KEY)
    t = store.load(d)[0][100]["time"]
    ov = LiveOverlay()
    ov.replace(d, [Bar(t // 60, 5, 6, 4, 5.5, 1.0, None, "official")])
    set_overlay(ov.rows)
    try:
        candles, _ = store.load(d)
        assert len(candles) == 375 and next(c for c in candles if c["time"] == t)["open"] == 5
    finally:
        set_overlay(None)


# ---------------------------------------------------------------- backfill fetch
def test_fetch_backfill_returns_only_the_wanted_minutes_of_the_day() -> None:
    h = FakeHistory()
    h.intraday_rows = full_day_rows(MON)[::1]
    bars = fetch_backfill(h, KEY, MON, M(10, 30), M(10, 33))
    assert [b.minute for b in bars] == [M(10, 30), M(10, 31), M(10, 32), M(10, 33)]
    assert all(b.source == "backfill" for b in bars)
    h.intraday_rows = full_day_rows(FRI)  # another day's rows are never used
    assert fetch_backfill(h, KEY, MON, M(10, 30), M(10, 33)) == []


# ---------------------------------------------------------------- compare
def test_compare_sets_reports_changed_added_and_removed_within_the_official_span() -> None:
    ours = {1: Bar(1, 1, 1, 1, 1, 0.0, None, "filled"), 2: Bar(2, 5, 5, 5, 5, 1.0, None, "tick"), 9: Bar(9, 1, 1, 1, 1, 1.0, None, "tick")}
    off = {2: Bar(2, 5, 6, 5, 5, 1.0, None, "official"), 3: Bar(3, 1, 1, 1, 1, 1.0, None, "official")}
    kinds = {(d.minute, d.kind) for d in compare_sets("k", ours, off)}
    assert kinds == {(2, "changed"), (3, "added")}  # 1 and 9 lie outside the official span
    assert compare_sets("k", ours, {}) == []


# ---------------------------------------------------------------- 15:45 reconcile
def test_reconcile_today_replaces_with_official_logs_every_difference_and_clears_the_marker(cdir: Path, tmp_path: Path) -> None:
    e = live_engine()
    upsert_bars(cdir, KEY, e.bars(KEY))
    state = ReconcileState(tmp_path / "state")
    state.mark(MON, KEY)
    logs = tmp_path / "rec"
    reports = reconcile_today(e, FakeHistory(intraday=official_rows()), candles_dir=cdir, log_dir=logs, state=state)
    assert reports[0].ok and reports[0].official_bars == 375 and state.pending() == []
    assert state.status(MON, KEY) == "intraday_reconciled"
    assert all(b.source == "official" for b in e.bars(KEY)) and len(e.bars(KEY)) == 375
    stored = stored_day(cdir, KEY, MON)
    assert len(stored) == 375 and stored[M(9, 15)].volume == 7.0
    lines = [json.loads(x) for x in (logs / "2026-10-05.reconcile.jsonl").read_text().splitlines()]
    diffs = [x for x in lines if not x.get("summary")]
    assert any(d["kind"] == "changed" and d["minute"] == "09:15" and d["ours_source"] == "tick" for d in diffs)
    assert any(d["kind"] == "changed" and d["minute"] == "09:17" and d["ours_source"] == "filled" for d in diffs)
    assert lines[-1]["summary"] and lines[-1]["official_bars"] == 375 and lines[-1]["differences"] == len(diffs)


def test_reconcile_today_removes_a_filled_bar_official_does_not_have(cdir: Path, tmp_path: Path) -> None:
    e = live_engine()
    rows = [r for r in official_rows() if datetime.fromisoformat(r[0]).minute not in (17,) or datetime.fromisoformat(r[0]).hour != 9]
    reports = reconcile_today(e, FakeHistory(intraday=rows), candles_dir=cdir, log_dir=tmp_path / "rec", state=ReconcileState(tmp_path / "s"))
    assert any(d.kind == "removed" and d.minute == M(9, 17) and d.ours_source == "filled" for d in reports[0].diffs)
    assert e.builders[KEY].bar(M(9, 17)) is None


def test_reconcile_today_keeps_the_marker_when_the_fetch_fails_or_is_empty(cdir: Path, tmp_path: Path) -> None:
    e = live_engine()
    state = ReconcileState(tmp_path / "s")
    state.mark(MON, KEY)
    r1 = reconcile_today(e, FakeHistory(fail=UpstoxAuthError("expired")), candles_dir=cdir, log_dir=tmp_path / "rec", state=state)
    assert not r1[0].ok and "UpstoxAuthError" in (r1[0].error or "")
    r2 = reconcile_today(e, FakeHistory({}), candles_dir=cdir, log_dir=tmp_path / "rec", state=state)
    assert not r2[0].ok
    assert state.pending() == [(MON, KEY)] and all(b.source != "official" for b in e.bars(KEY))


# ---------------------------------------------------------------- case 8: missed reconcile
def test_case8_on_startup_a_past_unreconciled_day_is_reconciled_from_the_historical_api(cdir: Path, tmp_path: Path) -> None:
    e = live_engine()
    upsert_bars(cdir, KEY, e.bars(KEY))  # the live bars were persisted Monday, the 15:45 job never ran
    state = ReconcileState(tmp_path / "state")
    state.mark(MON, KEY)
    assert len(stored_day(cdir, KEY, MON)) == 375
    hist = FakeHistory({MON: official_rows()})
    reports = reconcile_missed(hist, candles_dir=cdir, log_dir=tmp_path / "rec", state=state, today=date(2026, 10, 6))
    assert hist.calls == [(KEY, MON, MON)]  # a single-day window
    assert len(reports) == 1 and reports[0].ok and reports[0].diffs
    assert state.pending() == []
    stored = stored_day(cdir, KEY, MON)
    assert len(stored) == 375 and all(b.volume == 7.0 for b in stored.values())
    assert len(stored_day(cdir, KEY, FRI)) == 375  # other days untouched
    lines = [json.loads(x) for x in (tmp_path / "rec" / "2026-10-05.reconcile.jsonl").read_text().splitlines()]
    assert lines[-1]["mode"] == "historical" and lines[-1]["summary"]
    assert state.status(MON, KEY) == "final"


def test_case8_today_is_left_to_the_running_engine_and_failures_stay_pending(cdir: Path, tmp_path: Path) -> None:
    state = ReconcileState(tmp_path / "state")
    state.mark(MON, KEY)
    hist = FakeHistory({MON: official_rows()})
    assert reconcile_missed(hist, candles_dir=cdir, log_dir=tmp_path / "rec", state=state, today=MON) == []
    assert hist.calls == [] and state.pending() == [(MON, KEY)]
    bad = FakeHistory(fail=UpstoxAuthError("expired"))
    rep = reconcile_missed(bad, candles_dir=cdir, log_dir=tmp_path / "rec", state=state, today=date(2026, 10, 6))
    assert not rep[0].ok and state.pending() == [(MON, KEY)]
    ok = reconcile_missed(hist, candles_dir=cdir, log_dir=tmp_path / "rec", state=state, today=date(2026, 10, 6))
    assert ok[0].ok and state.pending() == [] and state.status(MON, KEY) == "final"


def test_case8_several_days_and_instruments_are_each_reconciled_once(cdir: Path, tmp_path: Path) -> None:
    state = ReconcileState(tmp_path / "state")
    d1, d2 = date(2026, 10, 1), date(2026, 10, 5)
    state.mark(d1, KEY)
    state.mark(d2, KEY)
    state.mark(d2, "NSE_EQ|INE000X00000")  # not stored locally: nothing to replace, marker still cleared
    hist = FakeHistory({d1: official_rows(d1), d2: official_rows(d2)})
    reports = reconcile_missed(hist, candles_dir=cdir, log_dir=tmp_path / "rec", state=state, today=date(2026, 10, 6))
    assert len(reports) == 3 and state.pending() == [] and state.not_final() == []
    assert sorted(c[1] for c in hist.calls) == sorted([d1, d2, d2])
    assert len(stored_day(cdir, KEY, d1)) == 375


# ---------------------------------------------------------------- marker file
def test_reconcile_state_survives_a_corrupt_file_and_dedupes(tmp_path: Path) -> None:
    s = ReconcileState(tmp_path)
    s.mark(MON, KEY)
    s.mark(MON, KEY)
    assert s.pending() == [(MON, KEY)]
    s.path.write_text(json.dumps({"pending": {MON.isoformat(): [KEY, "NSE_FO|1"]}}))
    assert s.status(MON, KEY) == "pending" and (MON, "NSE_FO|1") in s.pending()
    s.mark_intraday(MON, KEY)
    s.mark(MON, KEY)  # a later live write does not move the stage backwards
    assert s.status(MON, KEY) == "intraday_reconciled" and s.pending() == [(MON, "NSE_FO|1")]
    s.mark_final(MON, KEY)
    s.mark_intraday(MON, KEY)
    assert s.status(MON, KEY) == "final"
    s.path.write_text("{not json")
    assert s.pending() == []
    s.mark(MON, "a")
    s.clear(MON, "a")
    s.clear(MON, "never-there")
    assert s.pending() == []
    assert MS_MIN == 60_000


def test_futures_post_close_and_oi_only_are_expected_a_volume_change_is_not(cdir: Path, tmp_path: Path) -> None:
    fo = "NSE_FO|48704"
    raw = [
        {"t": m * MS_MIN, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": vol, "oi": oi}
        for m, vol, oi in ((M(9, 15), 10.0, 1000.0), (M(9, 16), 10.0, 2000.0))
    ]
    df, _ = build_frame(raw, 1, keep_oi=True)
    write_parquet(df, cdir / symbol_dir_name(fo) / "1m.parquet")

    def row(h: int, mi: int, vol: float, oi: float) -> list[Any]:
        return [iso(M(h, mi)), 100, 101, 99, 100.5, vol, oi]

    hist = FakeHistory(intraday=[
        row(9, 15, 10, 1500),   # prices and volume match; open interest does not
        row(9, 16, 40, 2500),   # volume (and open interest) differ
        row(15, 30, 1, 2500),
        row(15, 39, 1, 2500),
    ])
    state = ReconcileState(tmp_path / "s")
    state.mark(MON, fo)
    reports = reconcile_intraday_day(hist, MON, [fo], candles_dir=cdir, log_dir=tmp_path / "rec", state=state)
    assert reports[0].ok and {(d.minute, tuple(d.fields)) for d in reports[0].diffs} == {(M(9, 16), ("volume", "oi"))}
    assert {(d.minute, d.kind) for d in reports[0].expected} == {
        (M(9, 15), "changed"), (M(15, 30), "added"), (M(15, 39), "added"),
    }
    stored = stored_day(cdir, fo, MON)
    assert M(15, 30) not in stored and M(15, 39) not in stored
    assert stored[M(9, 15)].oi == 1500.0 and stored[M(9, 16)].volume == 40.0
    assert state.status(MON, fo) == "intraday_reconciled"
    lines = [json.loads(x) for x in (tmp_path / "rec" / "2026-10-05.reconcile.jsonl").read_text().splitlines()]
    assert lines[-1]["differences"] == 1 and lines[-1]["expected"] == 3
    assert sum(1 for x in lines if x.get("expected") is True and not x.get("summary")) == 3


def test_an_empty_historical_fetch_leaves_the_intraday_mark_and_a_manual_run_tries_today(cdir: Path, tmp_path: Path) -> None:
    state = ReconcileState(tmp_path / "s")
    state.mark_intraday(MON, KEY)
    empty = FakeHistory({})
    rep = reconcile_missed(empty, candles_dir=cdir, log_dir=tmp_path / "rec", state=state, today=MON, only_day=MON)
    assert not rep[0].ok and empty.calls == [(KEY, MON, MON)]
    assert state.status(MON, KEY) == "intraday_reconciled"
    assert reconcile_missed(empty, candles_dir=cdir, log_dir=tmp_path / "rec", state=state, today=MON) == []
