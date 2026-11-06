"""GEX snapshots, the 5-minute scheduler and the end-of-day study. No socket, no real backtest."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.options.gex_history import (
    LONG,
    SHORT,
    append_snapshot,
    day_stats,
    load_day,
    recorded_days,
    regime,
    snapshot_at,
    snapshot_row,
    summarise_days,
    summarise_tagged,
    tag_trades,
)
from app.options.gex_scheduler import GexScheduler, due_slot, report_due
from scripts.gex_report import backtest_start, build

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 11, 9)


def at(h: int, m: int, s: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST)


def ts(h: int, m: int, day: date = DAY) -> int:
    return int(at(h, m, day=day).timestamp())


def snap(h: int, m: int, spot: float, flip: float | None = 25000.0, net: float = 100.0, day: date = DAY) -> dict:
    return {"t": ts(h, m, day), "expiry": "2026-11-10", "spot": spot, "total_net": net, "total_call": 150.0,
            "total_put": -50.0, "gamma_flip": flip, "call_wall": 25200.0, "put_wall": 24800.0, "strikes": []}


# ------------------------------------------------------------------ storage
def test_snapshot_row_is_compact_and_days_are_ist(tmp_path: Path) -> None:
    result = {"as_of": ts(9, 20), "expiry": "2026-11-10", "spot": 25010.0, "total_net": 5.0, "total_call": 7.0,
              "total_put": -2.0, "gamma_flip": 24990.0, "call_wall": 25100.0, "put_wall": 24900.0,
              "strikes": [{"strike": 25000.0, "call": 1.23456, "put": -0.98765, "net": 0.24691}]}
    row = snapshot_row(result)
    assert row["strikes"] == [[25000.0, 1.235, -0.988]]
    path = append_snapshot(tmp_path, row)
    assert path.name == "2026-11-09.jsonl"
    assert recorded_days(tmp_path) == [DAY]
    assert load_day(tmp_path, DAY)[0]["spot"] == 25010.0


def test_load_day_skips_a_broken_line_and_keeps_one_snapshot_per_slot(tmp_path: Path) -> None:
    append_snapshot(tmp_path, snap(9, 25, 25000))
    append_snapshot(tmp_path, {**snap(9, 25, 25001), "t": ts(9, 25) + 20})  # a second writer, same slot
    append_snapshot(tmp_path, snap(9, 20, 24990))
    with (tmp_path / "2026-11-09.jsonl").open("a") as fh:
        fh.write('{"t": 12')
    rows = load_day(tmp_path, DAY)
    assert [r["spot"] for r in rows] == [24990, 25000]


# ------------------------------------------------------------------ labels
def test_snapshot_at_never_looks_ahead() -> None:
    snaps = [snap(9, 15, 1), snap(9, 20, 2), snap(9, 25, 3)]
    assert snapshot_at(snaps, ts(9, 24))["spot"] == 2
    assert snapshot_at(snaps, ts(9, 20))["spot"] == 2
    assert snapshot_at(snaps, ts(9, 14)) is None


def test_regime_uses_the_flip_then_the_sign() -> None:
    assert regime(snap(9, 30, 0, flip=25000), 25000) == LONG
    assert regime(snap(9, 30, 0, flip=25000), 24999) == SHORT
    assert regime(snap(9, 30, 0, flip=None, net=-1), 25000) == SHORT
    assert regime(snap(9, 30, 0, flip=None, net=3), 25000) == LONG


def test_day_stats_anchor_at_0945_and_trend_flag() -> None:
    snaps = [snap(9, 15, 24800), snap(9, 40, 24900), snap(9, 45, 24950), snap(12, 0, 24880), snap(15, 30, 24850)]
    s = day_stats(snaps)
    assert s is not None
    assert s["regime"] == SHORT and s["spot_at_anchor"] == 24950
    assert s["range_after"] == 100 and s["move_after"] == -100 and s["trend_day"] is True
    assert day_stats([snap(9, 15, 1), snap(9, 40, 2)]) is None


def test_trades_are_tagged_at_entry_and_summarised() -> None:
    snaps = {DAY: [snap(9, 15, 24950), snap(9, 45, 25050)]}
    trades = [
        {"entry_time": ts(9, 30), "entry_price": 24960, "exit_price": 24900, "direction": "SHORT",
         "option": {"net_pnl": 800.0}},
        {"entry_time": ts(10, 0), "entry_price": 25060, "exit_price": 25040, "direction": "LONG",
         "option": {"net_pnl": -300.0}},
        {"entry_time": ts(10, 0, date(2026, 11, 10)), "entry_price": 1, "exit_price": 2, "direction": "LONG"},
        {"entry_time": ts(9, 10), "entry_price": 1, "exit_price": 2, "direction": "LONG"},
    ]
    tagged = tag_trades(trades, snaps)
    assert [t["regime"] for t in tagged] == [SHORT, LONG]
    s = summarise_tagged(tagged)
    assert s[SHORT] == {"trades": 1, "win_rate": 1.0, "net": 800.0, "per_trade": 800.0}
    assert s[LONG]["win_rate"] == 0.0


def test_index_points_are_used_when_there_is_no_option_pnl() -> None:
    tagged = tag_trades(
        [{"entry_time": ts(9, 30), "entry_price": 25010, "exit_price": 24990, "direction": "SHORT"}],
        {DAY: [snap(9, 15, 25000)]},
    )
    assert tagged[0]["pnl"] == 20 and tagged[0]["is_option"] is False


def test_summarise_days_counts_each_regime() -> None:
    rows = [
        {"regime": LONG, "range_after": 100.0, "move_after": 10.0, "trend_day": False},
        {"regime": LONG, "range_after": 60.0, "move_after": -40.0, "trend_day": True},
        {"regime": SHORT, "range_after": 200.0, "move_after": 150.0, "trend_day": True},
    ]
    out = summarise_days(rows)
    assert out[LONG] == {"days": 2, "avg_range": 80.0, "avg_abs_move": 25.0, "trend_days": 1}
    assert out[SHORT]["days"] == 1


# ------------------------------------------------------------------ scheduler
def test_slots_run_every_five_minutes_inside_market_hours() -> None:
    assert due_slot(at(9, 14, 50), None) is None
    assert due_slot(at(9, 15, 5), None) == at(9, 15)
    assert due_slot(at(9, 17, 0), at(9, 15)) is None
    assert due_slot(at(9, 20, 30), at(9, 15)) == at(9, 20)
    assert due_slot(at(9, 24, 0), None) is None  # 4 minutes late: skipped, not taken late
    assert due_slot(at(15, 30, 10), at(15, 25)) == at(15, 30)
    assert due_slot(at(15, 34, 0), at(15, 25)) is None
    assert due_slot(at(15, 31, 0), at(15, 30)) is None


def test_report_runs_once_after_the_close() -> None:
    assert report_due(at(15, 39), None, True) is False
    assert report_due(at(15, 40), None, True) is True
    assert report_due(at(15, 45), DAY, True) is False
    assert report_due(at(16, 0), None, False) is False
    assert report_due(at(23, 30), None, True) is False


def test_scheduler_records_once_per_slot_and_not_on_holidays(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {"t": at(9, 15, 2).timestamp()}
    calls: list[float] = []
    reports: list[date] = []
    sched = GexScheduler(
        record=lambda: calls.append(now["t"]) or True,
        has_snapshots=lambda d: True,
        is_session=lambda d: d == DAY,
        clock=lambda: now["t"],
    )

    async def fake_report(day: date) -> None:
        reports.append(day)

    monkeypatch.setattr(sched, "_run_report", fake_report)

    async def go() -> None:
        await sched.tick()
        now["t"] += 15
        await sched.tick()
        now["t"] = at(9, 20, 1).timestamp()
        await sched.tick()
        now["t"] = at(15, 41).timestamp()
        await sched.tick()
        await sched.tick()
        now["t"] = at(9, 20, 1, day=date(2026, 11, 8)).timestamp()  # a Sunday
        await sched.tick()

    asyncio.run(go())
    assert len(calls) == 2
    assert reports == [DAY]


def test_a_failing_read_does_not_stop_the_scheduler() -> None:
    def boom() -> bool:
        raise RuntimeError("network")

    sched = GexScheduler(record=boom, has_snapshots=lambda d: False, is_session=lambda d: True,
                         clock=lambda: at(9, 15, 1).timestamp())
    asyncio.run(sched.tick())


# ------------------------------------------------------------------ report
def test_backtest_start_never_reaches_the_holdout() -> None:
    assert backtest_start(date(2026, 11, 9)) == date(2026, 10, 2)
    assert backtest_start(date(2027, 3, 1)) == date(2027, 3, 1) - timedelta(days=45)


def test_build_runs_each_candidate_over_the_recorded_days(tmp_path: Path) -> None:
    for s in (snap(9, 15, 24950), snap(9, 45, 24960), snap(15, 30, 24800)):
        append_snapshot(tmp_path, s)
    seen: list[tuple[str, date, date]] = []

    def fake_run(strategy: str, params: dict[str, Any], start: date, end: date) -> list[dict[str, Any]]:
        seen.append((strategy, start, end))
        if strategy == "pdh_pdl_breakout":
            raise ValueError("no candles")
        return [{"entry_time": ts(10, 0), "entry_price": 24940, "exit_price": 24860, "direction": "SHORT",
                 "option": {"net_pnl": 1200.0}}]

    text, payload = build(tmp_path, upto=DAY, run=fake_run, only={"C2", "C11"})
    assert seen == [("orb_plus", date(2026, 10, 2), DAY), ("pdh_pdl_breakout", date(2026, 10, 2), DAY)]
    c2, c11 = payload["candidates"]
    assert c2["by_regime"][SHORT]["trades"] == 1 and c2["by_regime"][LONG]["trades"] == 0
    assert "no candles" in c11["error"]
    assert payload["days"][0]["regime"] == SHORT
    assert "too few to judge" in text and "| C2 |" in text
    json.dumps(payload)


def test_build_without_snapshots_runs_nothing(tmp_path: Path) -> None:
    text, payload = build(tmp_path, upto=DAY, run=lambda *a: pytest.fail("ran"))
    assert payload["candidates"] == [] and "0 recorded days" in text
