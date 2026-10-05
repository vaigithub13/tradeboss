"""The paper session driven by the recorded 5 Oct 2026 feed, frame by frame, never the network.

`data/` is local and not in git, so this test skips on a clean checkout. It runs the real pipeline:
recording -> LiveEngine (1-minute bars) -> PaperSession (closed 5m bars -> Log XZ -> paper fills)
-> the end-of-day check against the normal backtest on the stored candles.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtest.catalog import build_strategy
from app.backtest.expiry import load_default_calendar
from app.backtest.costs import load_default_cost_table
from app.backtest.lots import load_default_lot_table
from app.config import settings
from app.data.resampler import resample
from app.data.store import CandleStore
from app.live.engine import LiveEngine
from app.live.recorder import recording_path, replay
from app.live.spreads.decode import depth_quotes
from app.options.contract import choose_contract
from app.options.strikes import load_default_step_table
from app.paper.check import end_of_day_check
from app.paper.pricing import model_price_for, vix_from_store
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.upstox.instruments import NIFTY_INDEX_KEY, current_index

DAY = date(2026, 10, 5)
IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = "NIFTY50"
PARAMS = {"z_length": 14, "ma": "rma"}


def _skip_reason() -> str | None:
    if not recording_path(settings.feed_recordings_dir, DAY).exists():
        return "no feed recording for 2026-10-05 in data/"
    if current_index(settings.instruments_dir) is None:
        return "no instrument snapshot in data/"
    if not (settings.candles_dir / SYMBOL / "1m.parquet").exists():
        return "no stored NIFTY 1m candles in data/"
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")


def _day_bounds() -> tuple[int, int]:
    start = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=IST).timestamp())
    return start, start + 86_400


def _choose(direction: str, spot: float, on: date):
    return choose_contract(direction, spot, on, calendar=load_default_calendar(),
                           lots=load_default_lot_table(), steps=load_default_step_table())


@pytest.fixture(scope="module")
def replayed():
    start, end = _day_bounds()
    store = CandleStore(settings.candles_dir)
    prior, _ = store.load(SYMBOL, from_time=start - 10 * 86_400, to_time=start, session_types=("normal", "weekend_full"))
    warm = [b for b in resample(list(prior), "5m") if int(b["time"]) < start]

    key_of = {i.symbol: i.key for i in current_index(settings.instruments_dir).instruments}

    strategy = build_strategy({"strategy": "log_xz", "params": PARAMS})
    session = PaperSession(day=DAY, strategy=strategy, choose=_choose, key_for=key_of.get,
                           quotes=QuoteBook(), cost_table=load_default_cost_table(), bar_minutes=5,
                           model_price=model_price_for(vix_from_store(store, DAY)))
    session.warm(warm)

    engine = LiveEngine()

    def on_frame(raw: bytes, wall_ms: int, idx: int) -> None:
        engine.on_frame(raw, recv_wall_ms=wall_ms, frame_idx=idx)
        session.on_depth(depth_quotes(raw))
        for ev in engine.take_events():
            if ev.key != NIFTY_INDEX_KEY:
                continue
            b = ev.bar
            session.on_index_minute(
                {"time": b.time_s, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                 "volume": b.volume or 0},
                now_ms=engine.current_ts,
            )

    frames = replay(recording_path(settings.feed_recordings_dir, DAY), on_frame)
    session.end_of_day(now_ms=engine.current_ts)
    return {"frames": frames, "session": session, "store": store, "strategy_params": PARAMS}


def test_replay_produces_closed_bars_and_no_unknown_fill_sources(replayed) -> None:
    session = replayed["session"]
    assert replayed["frames"] > 0
    assert len(session.shown) > 0
    for rec in session.signals:
        if rec["status"] == "filled":
            assert rec["fill_source"] in ("quote", "modelled")
    for t in session.book.trades:
        assert t["entry_source"] in ("quote", "modelled") and t["exit_source"] in ("quote", "modelled")


def test_the_5_oct_recording_has_no_option_depth_so_its_fills_are_modelled(replayed) -> None:
    """The recording holds depth for the front future only. Every option fill must therefore be modelled."""
    session = replayed["session"]
    filled = [r for r in session.signals if r["status"] == "filled"]
    assert filled, "the model fallback should fill the signals"
    assert all(r["fill_source"] == "modelled" for r in filled)
    assert all(t["entry_source"] == "modelled" and t["entry_slippage"] is None for t in session.book.trades)


def test_every_closed_bar_is_a_full_five_minutes_from_0915(replayed) -> None:
    for bar in replayed["session"].shown:
        t = datetime.fromtimestamp(int(bar["time"]), IST)
        assert t.minute % 5 == 0 and (t.hour, t.minute) >= (9, 15)


def test_end_of_day_check_runs_against_the_normal_backtest(replayed) -> None:
    session = replayed["session"]
    entries = [{"time": s["time"], "side": s["side"]} for s in session.signals
               if s["side"] in ("BUY", "SELL") and s["time"] is not None]
    report = end_of_day_check(
        day=DAY,
        live_entries=entries,
        live_bars=session.live_bars(),
        incomplete=session.bars.incomplete,
        store=replayed["store"],
        symbol=SYMBOL,
        make_strategy=lambda: build_strategy({"strategy": "log_xz", "params": PARAMS}),
    )
    assert report["day"] == "2026-10-05"
    assert report["live_signals"] == len(entries)
    for d in report["differences"]:
        assert d["reason"] and d["source"] in ("live", "backtest")
