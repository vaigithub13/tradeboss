"""A paper day that is stopped in the middle of an open position, then resumed, gives the same signals and trades
as a day that was never interrupted. Recorded 5 Oct 2026 feed; skips when it is not on this machine."""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.catalog import build_strategy
from app.backtest.costs import load_default_cost_table
from app.config import settings
from app.live.recorder import recording_path
from app.paper.catchup import replay_into
from app.paper.history import stored_warmup
from app.paper.live import PaperRunner
from app.paper.pricing import VixSeries, model_price_for
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.paper.store import load_day, save_day
from app.data.store import CandleStore
from tests.test_paper_replay import DAY5, PARAMS, SKIP5, SYMBOL, _choose

pytestmark = pytest.mark.skipif(SKIP5 is not None, reason=SKIP5 or "")


def _session(day: date, vix: VixSeries) -> PaperSession:
    from app.upstox.instruments import index_on

    key_of = {i.symbol: i.key for i in index_on(settings.instruments_dir, day).instruments}
    s = PaperSession(day=day, strategy=build_strategy({"strategy": "log_xz", "params": PARAMS}), choose=_choose,
                     key_for=key_of.get, quotes=QuoteBook(), cost_table=load_default_cost_table(),
                     model_price=model_price_for(vix.at))
    s.warm(stored_warmup(CandleStore(settings.candles_dir), SYMBOL, day))
    return s


def _path():
    return recording_path(settings.feed_recordings_dir, DAY5)


def _uninterrupted() -> PaperSession:
    vix = VixSeries()
    s = _session(DAY5, vix)
    replay_into(s, _path(), on_vix=vix.add)
    s.end_of_day()
    return s


def _outcome(s: PaperSession) -> tuple:
    sig = [(r["time"], r["side"], r["status"], r["fill_source"], r["fill_price"]) for r in s.signals]
    trades = [(t["index_entry_time"], t["entry_price"], t["exit_price"], t["net"]) for t in s.book.trades]
    return sig, trades


def _cut_inside_an_open_position(full: PaperSession) -> int:
    """A time (unix seconds) between the first two decided signals, so the position is open at the cut."""
    decided = [r["time"] for r in full.signals if r["status"] == "filled"]
    assert len(decided) >= 2, "the 5 Oct day should have at least two filled signals"
    return decided[0] + (decided[1] - decided[0]) // 2


def test_a_restart_inside_an_open_position_matches_the_uninterrupted_day(tmp_path) -> None:
    full = _uninterrupted()
    cut_s = _cut_inside_an_open_position(full)

    vix1 = VixSeries()
    first = _session(DAY5, vix1)
    replay_into(first, _path(), on_vix=vix1.add, until_ms=cut_s * 1000)
    saved = first.snapshot()
    assert saved["open"] is not None, "the cut should fall inside an open position"
    save_day(tmp_path, DAY5, {"state": "running", "ended_by": None, **saved})
    loaded = load_day(tmp_path, DAY5)  # through the JSON file, as a restart does

    vix2 = VixSeries()
    second = _session(DAY5, vix2)
    second.restore(loaded)
    replay_into(second, _path(), on_vix=vix2.add)
    second.end_of_day()

    assert _outcome(second) == _outcome(full)


def test_the_runner_resumes_today_from_its_day_file(tmp_path) -> None:
    full = _uninterrupted()
    cut_s = _cut_inside_an_open_position(full)

    def factory(vix: VixSeries):
        return lambda day, strategy, params: _session(day, vix)

    vix1 = VixSeries()
    first = PaperRunner(tmp_path, factory(vix1),
                        catch_up=lambda s, d: replay_into(s, _path(), on_vix=vix1.add, until_ms=cut_s * 1000))
    first.start(DAY5, "log_xz", {})
    assert first.state == "running" and first.session.book.position is not None

    vix2 = VixSeries()
    second = PaperRunner(tmp_path, factory(vix2),
                         catch_up=lambda s, d: replay_into(s, _path(), on_vix=vix2.add))
    assert second.resume_if_running(DAY5) is True
    second.finish_day()

    assert _outcome(second.session) == _outcome(full)
    assert load_day(tmp_path, DAY5)["state"] == "stopped"


def test_a_start_with_other_settings_is_refused_rather_than_mixed(tmp_path) -> None:
    from app.paper.live import SettingsMismatch

    vix = VixSeries()
    runner = PaperRunner(tmp_path, lambda day, strategy, params: _session(day, vix),
                         catch_up=lambda s, d: replay_into(s, _path(), on_vix=vix.add, until_ms=0))
    runner.start(DAY5, "log_xz", {})
    runner.stop(0)
    with pytest.raises(SettingsMismatch):
        runner.start(DAY5, "log_xz", {"z_length": 20})


def test_a_restart_after_the_session_ended_still_saves_the_end_of_day_check(tmp_path) -> None:
    """Live 6 Oct: the backend restarted between 15:30 and the 15:45 reconcile, so the runner had no session and
    the day's file never got its check. The check is rebuilt from the day's file instead."""
    vix = VixSeries()
    first = PaperRunner(tmp_path, lambda day, strategy, params: _session(day, vix),
                        catch_up=lambda s, d: replay_into(s, _path(), on_vix=vix.add))
    first.start(DAY5, "log_xz", {})
    first.finish_day()
    assert "eod_check" not in load_day(tmp_path, DAY5)

    restarted = PaperRunner(tmp_path, lambda day, strategy, params: _session(day, VixSeries()))
    assert restarted.resume_if_running(DAY5) is False  # a stopped day is not started again
    shown = restarted.status(0)  # but the panel still shows the day's result
    assert shown["state"] == "stopped" and shown["day"] == DAY5.isoformat()
    assert shown["signals"] == first.session.signals and shown["summary"] == first.session.snapshot()["summary"]
    report = restarted.reconcile_check(CandleStore(settings.candles_dir), SYMBOL, DAY5)
    assert report is not None and report["differences"] == []
    saved = load_day(tmp_path, DAY5)
    assert saved["eod_check"]["live_signals"] == 4 and saved["state"] == "stopped"
    assert saved["signals"] == first.session.signals  # the restored day is saved unchanged
