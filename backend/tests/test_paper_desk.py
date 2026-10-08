"""Two paper strategies at once: separate sessions, positions, P&L and day files; one feed drives both."""

from __future__ import annotations

from datetime import date

import pytest

from app.paper.desk import SLOTS, PaperDesk, UnknownSlot
from app.paper.live import PaperRunner
from app.paper.store import load_day
from tests import test_paper_session as tps
from tests.test_paper_live import DAY, ms


def desk(tmp_path, scripts: dict[str, dict[int, str]]) -> PaperDesk:
    def make_for(slot: str):
        return lambda day, strategy, params: tps.session(tps.Scripted(scripts.get(slot, {})), model_price=lambda c, sp, ts: 95.0)

    return PaperDesk({slot: PaperRunner(PaperDesk.slot_dir(tmp_path, slot), make_for(slot)) for slot in SLOTS})


def feed(d: PaperDesk, minutes) -> None:
    for h, m, close in minutes:
        d.on_index_bar(tps.minute(h, m, close), now_ms=ms(h, m))


def test_slot_1_keeps_the_existing_directory_and_slot_2_has_its_own(tmp_path) -> None:
    assert SLOTS == ("1", "2", "3", "4")
    assert PaperDesk.slot_dir(tmp_path, "1") == tmp_path and PaperDesk.slot_dir(tmp_path, "2") == tmp_path / "slot2"
    assert PaperDesk.slot_dir(tmp_path, "3") == tmp_path / "slot3" and PaperDesk.slot_dir(tmp_path, "4") == tmp_path / "slot4"


def test_two_strategies_trade_on_one_feed_with_separate_positions_and_files(tmp_path) -> None:
    d = desk(tmp_path, {"1": {0: "BUY"}, "2": {0: "SELL"}})
    d.runner("1").start(DAY, "log_xz", {})
    d.runner("2").start(DAY, "log_xz", {"use_target": True})
    assert d.state == "running"
    feed(d, [(9, m, 22600.0) for m in range(15, 22)])
    one, two = d.runner("1").session, d.runner("2").session
    assert one.book.position.direction == "LONG" and two.book.position.direction == "SHORT"
    assert load_day(tmp_path, DAY)["requested_params"] == {}
    assert load_day(tmp_path / "slot2", DAY)["requested_params"] == {"use_target": True}
    d.runner("1").stop(ms(9, 30))
    assert d.runner("2").state == "running" and d.state == "running"  # stopping one leaves the other
    statuses = d.status(ms(9, 30))
    assert [s["slot"] for s in statuses["slots"]] == list(SLOTS)
    assert statuses["slots"][0]["state"] == "stopped" and statuses["slots"][1]["state"] == "running"


def test_the_clock_and_the_end_of_day_reach_both(tmp_path) -> None:
    d = desk(tmp_path, {"1": {0: "BUY"}, "2": {0: "BUY"}})
    for slot in SLOTS:
        d.runner(slot).start(DAY, "log_xz", {})
    feed(d, [(9, m, 22600.0) for m in range(15, 22)])
    d.on_clock(ms(15, 15))
    assert all(d.runner(s).session.book.position is None for s in SLOTS)
    d.finish_day()
    assert all(d.runner(s).state == "stopped" for s in SLOTS) and d.state == "stopped"


def test_wanted_keys_are_the_union_and_an_unknown_slot_is_refused(tmp_path) -> None:
    d = desk(tmp_path, {"1": {0: "BUY"}, "2": {0: "SELL"}})
    for slot in SLOTS:
        d.runner(slot).start(DAY, "log_xz", {})
    feed(d, [(9, m, 22600.0) for m in range(15, 22)])
    keys = d.wanted_keys()
    assert any("CE" in k for k in keys) and any("PE" in k for k in keys)
    with pytest.raises(UnknownSlot):
        d.runner("5")


def test_a_failure_in_one_slot_never_reaches_the_other_slot_or_the_feed(tmp_path, caplog) -> None:
    d = desk(tmp_path, {"1": {0: "BUY"}, "2": {0: "SELL"}})
    for slot in SLOTS:
        d.runner(slot).start(DAY, "log_xz", {})

    def boom(*a, **k):
        raise RuntimeError("slot 2 broke")

    d.runner("2").session.on_index_minute = boom  # type: ignore[method-assign]
    d.runner("2").session.on_index_tick = boom  # type: ignore[method-assign]
    feed(d, [(9, m, 22600.0) for m in range(15, 22)])  # does not raise
    d.on_index_tick(22610.0, now_ms=ms(9, 22))
    assert d.runner("1").session.book.position is not None
    assert any("slot 2" in r.getMessage() for r in caplog.records)


def test_index_ticks_reach_each_running_slot(tmp_path) -> None:
    d = desk(tmp_path, {"1": {}, "2": {}})
    seen = []
    for slot in SLOTS:
        d.runner(slot).start(DAY, "log_xz", {})
        d.runner(slot).session.on_index_tick = lambda price, now_ms, s=slot: seen.append((s, price)) or []
    d.on_index_tick(22610.0, now_ms=ms(9, 22))
    assert seen == [(s, 22610.0) for s in SLOTS]
