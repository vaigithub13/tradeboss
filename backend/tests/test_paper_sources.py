"""Live and replay paper results are kept apart.

A slot started after its day's session ended (15:30 IST) is a replay of the recording: it writes only
`<slot dir>/replay/<day>.json`, marked `source: replay`, never the live day file. In a live day, each signal and
trade says where it came from: `live`, or `replay` for what the catch-up decided from the recording (a Start late
in the day, a restart). Daily and weekly totals count live trades of live days only.
"""

from __future__ import annotations

import json
from datetime import date

from app.paper.live import PaperRunner
from app.paper.store import load_day, save_day, weekly_summary
from tests import test_paper_session as tps
from tests.test_paper_live import DAY, ms


def scripted_runner(tmp_path, script, *, catch_up=None):
    def make(day, strategy, params):
        return tps.session(tps.Scripted(script), model_price=lambda c, sp, ts: 95.0)

    return PaperRunner(tmp_path, make, catch_up=catch_up)


def feed(runner_or_session, minutes, on_session=False):
    for h, m, close in minutes:
        if on_session:
            runner_or_session.on_index_minute(tps.minute(h, m, close), now_ms=ms(h, m))
        else:
            runner_or_session.on_index_bar(tps.minute(h, m, close), now_ms=ms(h, m))


def test_a_start_after_the_session_ended_is_a_replay_and_never_writes_the_live_file(tmp_path) -> None:
    def catch_up(session, day):  # the whole day from the recording
        feed(session, [(9, m, 22600.0) for m in range(15, 22)], on_session=True)

    r = scripted_runner(tmp_path, {0: "BUY"}, catch_up=catch_up)
    r.start(DAY, "log_xz", {}, now_ms=ms(22, 1))
    assert r.source == "replay"
    assert load_day(tmp_path, DAY) is None  # no live file
    saved = load_day(tmp_path / "replay", DAY)
    assert saved["source"] == "replay" and len(saved["trades"]) == 0 and len(saved["signals"]) == 1
    assert saved["signals"][0]["source"] == "replay"
    assert r.status(ms(22, 2))["source"] == "replay"
    assert weekly_summary(tmp_path, DAY)["days"] == []


def test_a_replay_start_leaves_an_existing_live_file_alone(tmp_path) -> None:
    live = scripted_runner(tmp_path, {0: "BUY"})
    live.start(DAY, "log_xz", {}, now_ms=ms(9, 10))
    feed(live, [(9, m, 22600.0) for m in range(15, 22)])
    live.finish_day()
    before = (tmp_path / f"{DAY.isoformat()}.json").read_text()
    again = scripted_runner(tmp_path, {0: "BUY"})
    again.start(DAY, "log_xz", {}, now_ms=ms(21, 0))
    again.flush()
    assert (tmp_path / f"{DAY.isoformat()}.json").read_text() == before
    assert json.loads(before)["source"] == "live"


def test_in_a_live_day_the_catch_up_is_marked_replay_and_left_out_of_the_totals(tmp_path) -> None:
    def catch_up(session, day):  # a Start at 09:26: 09:15-09:24 come from the recording
        feed(session, [(9, m, 22600.0) for m in range(15, 25)], on_session=True)

    r = scripted_runner(tmp_path, {0: "BUY", 1: "SELL", 2: "BUY"}, catch_up=catch_up)
    r.start(DAY, "log_xz", {}, now_ms=ms(9, 26))
    assert r.source == "live"
    feed(r, [(9, m, 22600.0) for m in range(25, 30)])  # bar 2, live
    r.session.on_clock(ms(15, 15))
    snap = load_day(tmp_path, DAY) or {}
    r.flush()
    snap = load_day(tmp_path, DAY)
    assert [s["source"] for s in snap["signals"]] == ["replay", "replay", "live"]
    assert [t["source"] for t in snap["trades"]] == ["replay", "replay", "live"]
    assert snap["summary"]["trades"] == 1  # the live trade only
    assert snap["summary"]["replay"]["trades"] == 2
    assert [row["source"] for row in snap["report"]] == ["replay", "replay", "live"]
    assert snap["summary"]["exits"]["square-off"] == 1 and snap["summary"]["exits"]["reversal"] == 0


def test_the_week_counts_live_days_only(tmp_path) -> None:
    base = {"trades": 1, "wins": 1, "gross": 10, "charges": 1, "net": 9, "modelled_legs": 0, "signals": 1, "unfilled": 0}
    save_day(tmp_path, date(2026, 10, 5), {"source": "live", "summary": base})
    save_day(tmp_path, date(2026, 10, 6), {"summary": base})  # written before sources: run live
    save_day(tmp_path, date(2026, 10, 7), {"source": "replay", "summary": base})  # a stray replay in the live dir
    save_day(tmp_path / "replay", date(2026, 10, 8), {"source": "replay", "summary": base})
    week = weekly_summary(tmp_path, date(2026, 10, 7))
    assert [d["date"] for d in week["days"]] == ["2026-10-05", "2026-10-06"]
    assert week["totals"]["net"] == 18 and week["totals"]["trades"] == 2


def test_a_restart_keeps_the_sources(tmp_path) -> None:
    def catch_up(session, day):
        feed(session, [(9, m, 22600.0) for m in range(15, 20)], on_session=True)

    r = scripted_runner(tmp_path, {0: "BUY"}, catch_up=catch_up)
    r.start(DAY, "log_xz", {}, now_ms=ms(9, 21))
    r.flush()
    saved = load_day(tmp_path, DAY)
    again = tps.session(tps.Scripted({}))
    again.restore(saved)
    assert again.signals[0]["source"] == "replay" and again.book.position.source == "replay"


def test_a_trade_keeps_the_nifty_price_at_exit(tmp_path) -> None:
    r = scripted_runner(tmp_path, {0: "BUY"})
    r.start(DAY, "log_xz", {}, now_ms=ms(9, 10))
    feed(r, [(9, m, 22600.0) for m in range(15, 22)])
    r.on_index_tick(22633.5, now_ms=ms(9, 40))
    r.session.on_clock(ms(15, 15))
    t = r.session.book.trades[-1]
    assert t["index_exit"] == 22633.5 and t["index_entry"] == 22600.0
