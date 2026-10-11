"""Paper against the backtest on a committed day (M5, review 2026-10-11). Never skipped: no data/ needed.

tests/fixtures/paper_day is 9 Oct 2026, built by scripts/make_paper_fixture.py from the local recording, candles
and option bars: the feed frames trimmed to what paper reads, 16 days of NIFTY and VIX 1m bars (the 500-bar warm-up)
and the traded contracts' 1m bars. Each slot replays the recording through the live engine into a warmed paper
session, then runs the end-of-day check (the backtest with the option overlay) on the fixture's candles and option
bars. The trades must also be the ones that live session made that day.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.backtest.costs import load_default_cost_table
from app.data.store import CandleStore
from app.options.history import OptionHistoryStore
from app.paper.catchup import replay_into
from app.paper.check import end_of_day_check
from app.paper.history import stored_warmup
from app.paper.live import choose_nearest_weekly_atm, paper_strategy
from app.paper.pricing import VixSeries, model_price_for
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession

FIXTURE = Path(__file__).parent / "fixtures" / "paper_day"
MANIFEST = json.loads((FIXTURE / "manifest.json").read_text())
DAY = date.fromisoformat(MANIFEST["day"])


@pytest.fixture(scope="module")
def recording(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("paper_day") / "recording.bin"
    path.write_bytes(gzip.decompress((FIXTURE / "recording.bin.gz").read_bytes()))
    return path


def _replay(slot: dict[str, Any], recording: Path) -> PaperSession:
    strategy, rule = paper_strategy(slot["strategy"], slot["params"])
    vix = VixSeries()
    session = PaperSession(day=DAY, strategy=strategy, choose=choose_nearest_weekly_atm,
                           key_for=MANIFEST["symbols"].get, quotes=QuoteBook(), cost_table=load_default_cost_table(),
                           model_price=model_price_for(vix.at), exit_rule=rule)
    session.warm(stored_warmup(CandleStore(FIXTURE / "candles"), "NIFTY50", DAY))
    replay_into(session, recording, on_vix=vix.add)
    session.end_of_day()
    return session


@pytest.mark.parametrize("slot", MANIFEST["slots"], ids=lambda s: f"slot{s['slot']}-{s['strategy']}")
def test_paper_matches_the_backtest_on_the_committed_day(slot: dict[str, Any], recording: Path) -> None:
    session = _replay(slot, recording)
    assert session.warm_bars == 500
    assert session.bars.incomplete == set() and session.bars.given_up == []
    report = end_of_day_check(
        day=DAY, signals=session.signals, live_report=session.report_rows(), live_bars=session.live_bars(),
        incomplete=session.bars.incomplete, store=CandleStore(FIXTURE / "candles"), symbol="NIFTY50",
        strategy=slot["strategy"], params=slot["params"], basis="fill" if slot["uses_stops"] else "decision",
        tape=OptionHistoryStore(FIXTURE / "options"),
    )
    assert report.get("error") is None
    assert report["warnings"] == []  # every premium exit was checked against the stored option bars
    assert report["live_trades"] == report["backtest_trades"] == len(slot["live_trades"]) > 0
    assert report["differences"] == [], report["differences"]

    # and they are the trades the live session made that day: contract, direction, entry and exit minute, reason
    replayed = [(t["symbol"], t["direction"], t["entry_at_ms"] // 60_000, t["exit_at_ms"] // 60_000, t["exit_reason"])
                for t in session.book.trades]
    live = [(t["symbol"], t["direction"], t["entry_at_ms"] // 60_000, t["exit_at_ms"] // 60_000, t["exit_reason"])
            for t in slot["live_trades"]]
    assert replayed == live
    assert all(t["entry_source"] == "quote" and t["exit_source"] == "quote" for t in session.book.trades)


def test_the_fixture_holds_market_data_only() -> None:
    """No token, key, authorization header or account field in anything committed."""
    blobs = [gzip.decompress((FIXTURE / "recording.bin.gz").read_bytes())]
    blobs += [p.read_bytes() for p in FIXTURE.rglob("*") if p.is_file() and p.name != "recording.bin.gz"]
    for blob in blobs:
        assert b"Bearer" not in blob and b"Authorization" not in blob and b"authorized_redirect_uri" not in blob
        assert re.search(rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", blob) is None  # a JWT
        assert re.search(rb"sk-[A-Za-z0-9]{16,}", blob) is None


def test_the_fixture_is_small() -> None:
    size = sum(p.stat().st_size for p in FIXTURE.rglob("*") if p.is_file())
    assert size < 4_000_000
