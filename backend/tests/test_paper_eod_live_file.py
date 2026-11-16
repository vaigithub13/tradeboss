"""The end-of-day check must land on the live day file, even if a post-close auto-start flipped the runner to replay."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from app.paper.live import PaperRunner
from app.paper.store import load_day, save_day
from tests import test_paper_session as tps
from tests.test_paper_live import DAY, ms


def test_reconcile_check_writes_eod_to_the_live_file_after_a_replay_flip(tmp_path: Path) -> None:
    def make(day, strategy, params):
        return tps.session(tps.Scripted({}), model_price=lambda c, sp, ts: 95.0)

    runner = PaperRunner(tmp_path, make)
    runner.start(DAY, "log_xz", {}, start_source="hand")
    runner.finish_day()
    live = load_day(tmp_path, DAY)
    assert live is not None and "eod_check" not in live
    assert live["source"] == "live"

    # Simulate paper_autostart after 15:30: runner is now a finished replay.
    runner.source = "replay"
    runner.start_source = "auto"
    runner.ended_by = "replay finished"
    runner.eod_check = None

    fake = {
        "live_signals": 0, "live_trades": 0, "backtest_signals": 0, "backtest_trades": 0,
        "differences": [], "fill_driven": [], "exchange_only": [], "warnings": [],
    }
    import app.paper.live as live_mod
    original = live_mod.end_of_day_check
    live_mod.end_of_day_check = MagicMock(return_value=fake)  # type: ignore[assignment]
    try:
        out = runner.reconcile_check(store=MagicMock(), symbol="NIFTY50", day=DAY, tape=None)
    finally:
        live_mod.end_of_day_check = original  # type: ignore[assignment]

    assert out == fake
    live_after = load_day(tmp_path, DAY)
    assert live_after is not None and live_after.get("eod_check") == fake
    # Must not only write the replay copy
    assert live_after.get("source") == "live"
