"""The paper session driven by a recorded feed, frame by frame, never the network.

`data/` is local and not in git, so these tests skip on a clean checkout. They run the real pipeline:
recording -> LiveEngine (exchange-final 1-minute bars, VIX) -> PaperSession (closed 5m bars -> Log XZ
-> paper fills at the recorded quotes, or the model with the feed's own VIX) -> the end-of-day check.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtest.catalog import build_strategy
from app.backtest.costs import load_default_cost_table
from app.backtest.expiry import load_default_calendar
from app.backtest.lots import load_default_lot_table
from app.config import settings
from app.data.store import CandleStore
from app.live.engine import LiveEngine
from app.live.recorder import recording_path, replay
from app.live.spreads.decode import depth_quotes
from app.options.contract import choose_contract
from app.options.strikes import load_default_step_table
from app.paper.check import end_of_day_check
from app.paper.history import stored_warmup
from app.paper.live import PaperRunner
from app.paper.store import load_day
from app.paper.pricing import VixSeries, model_price_for
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.upstox.instruments import NIFTY_INDEX_KEY, VIX_KEY, index_on

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = "NIFTY50"
PARAMS: dict = {}  # Log XZ defaults: RMA 14, 5m


def _missing(day: date, *, need_candles: bool) -> str | None:
    if not recording_path(settings.feed_recordings_dir, day).exists():
        return f"no feed recording for {day.isoformat()} in data/"
    if index_on(settings.instruments_dir, day) is None:
        return f"no instrument snapshot on or before {day.isoformat()} in data/"
    if need_candles and not (settings.candles_dir / SYMBOL / "1m.parquet").exists():
        return "no stored NIFTY 1m candles in data/"
    return None


def _choose(direction: str, spot: float, on: date):
    return choose_contract(direction, spot, on, calendar=load_default_calendar(),
                           lots=load_default_lot_table(), steps=load_default_step_table())


def run_replay(day: date) -> dict:
    """One day's recording through the live engine into a paper session, warmed on the stored bars before the day."""
    key_of = {i.symbol: i.key for i in index_on(settings.instruments_dir, day).instruments}  # that day's contracts
    vix = VixSeries()
    session = PaperSession(day=day, strategy=build_strategy({"strategy": "log_xz", "params": PARAMS}),
                           choose=_choose, key_for=key_of.get, quotes=QuoteBook(),
                           cost_table=load_default_cost_table(), model_price=model_price_for(vix.at))
    session.warm(stored_warmup(CandleStore(settings.candles_dir), SYMBOL, day))  # exactly the backtest's warm-up
    engine = LiveEngine()

    def on_frame(raw: bytes, wall_ms: int, idx: int) -> None:
        engine.on_frame(raw, recv_wall_ms=wall_ms, frame_idx=idx)
        session.on_depth(depth_quotes(raw))  # depth first, so a bar closed by this frame sees its quotes
        for ev in engine.take_events():
            if ev.key == VIX_KEY:
                vix.add(ev.bar.time_s, ev.bar.close)
            elif ev.key == NIFTY_INDEX_KEY and ev.bar.source in ("i1", "session_end", "official"):
                b = ev.bar
                session.on_index_minute(
                    {"time": b.time_s, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                     "volume": b.volume or 0, "source": b.source},
                    now_ms=engine.current_ts,
                )
        if engine.current_ts > 0:
            if NIFTY_INDEX_KEY in engine.ticked:
                session.on_index_tick(engine.ticked[NIFTY_INDEX_KEY], now_ms=engine.current_ts)
            session.on_clock(engine.current_ts)

    frames = replay(recording_path(settings.feed_recordings_dir, day), on_frame)
    session.end_of_day()
    return {"frames": frames, "session": session}


# ---------------------------------------------------------------- 5 Oct: no option depth in the recording

DAY5 = date(2026, 10, 5)
SKIP5 = _missing(DAY5, need_candles=True)


@pytest.fixture(scope="module")
def oct5():
    if SKIP5:
        pytest.skip(SKIP5)
    return run_replay(DAY5)


@pytest.mark.skipif(SKIP5 is not None, reason=SKIP5 or "")
def test_5_oct_bars_are_exchange_final_and_cover_the_session(oct5) -> None:
    s = oct5["session"]
    assert oct5["frames"] > 0 and len(s.shown) >= 74


@pytest.mark.skipif(SKIP5 is not None, reason=SKIP5 or "")
def test_5_oct_fills_are_modelled_because_the_recording_has_no_option_depth(oct5) -> None:
    filled = [r for r in oct5["session"].signals if r["status"] == "filled"]
    assert filled and all(r["fill_source"] == "modelled" for r in filled)


@pytest.mark.skipif(SKIP5 is not None, reason=SKIP5 or "")
def test_5_oct_live_and_backtest_signals_match(oct5) -> None:
    s = oct5["session"]
    entries = [{"time": r["time"], "side": r["side"]} for r in s.signals if r["side"] in ("BUY", "SELL")]
    report = end_of_day_check(
        day=DAY5,
        live_entries=entries,
        live_bars=s.live_bars(),
        incomplete=s.bars.incomplete,
        store=CandleStore(settings.candles_dir),
        symbol=SYMBOL,
        make_strategy=lambda: build_strategy({"strategy": "log_xz", "params": PARAMS}),
    )
    assert report["live_signals"] == report["backtest_signals"] == 4
    assert report["differences"] == []


# ---------------------------------------------------------------- 6 Oct: option depth expected

DAY6 = date(2026, 10, 6)
SKIP6 = _missing(DAY6, need_candles=False)


@pytest.mark.skipif(SKIP6 is not None, reason=SKIP6 or "")
def test_6_oct_fills_come_from_real_quotes_not_the_model() -> None:
    s = run_replay(DAY6)["session"]
    filled = [r for r in s.signals if r["status"] == "filled"]
    assert filled, "the recording should produce at least one signal"
    assert all(r["fill_source"] == "quote" for r in filled), [r["fill_source"] for r in filled]


@pytest.mark.skipif(SKIP5 is not None, reason=SKIP5 or "")
def test_5_oct_reconcile_check_is_saved_into_the_day_file(oct5, tmp_path) -> None:
    runner = PaperRunner(tmp_path, lambda day, strategy, params: oct5["session"])
    runner.start(DAY5, "log_xz", {})
    runner.finish_day()
    report = runner.reconcile_check(CandleStore(settings.candles_dir), SYMBOL)
    assert report is not None and report["differences"] == []
    assert load_day(tmp_path, DAY5)["eod_check"]["live_signals"] == 4


# ---------------------------------------------------------------- 7 Oct: the live session ran cold
DAY7 = date(2026, 10, 7)
SKIP7 = _missing(DAY7, need_candles=True)


@pytest.mark.skipif(SKIP7 is not None, reason=SKIP7 or "")
def test_7_oct_warmed_replay_matches_the_backtest_with_real_fills() -> None:
    """Live 7 Oct started cold (warm_bars 0): 2 signals against the backtest's 6. Warmed, they agree."""
    s = run_replay(DAY7)["session"]
    assert s.warm_bars == 500
    entries = [{"time": r["time"], "side": r["side"]} for r in s.signals if r["side"] in ("BUY", "SELL")]
    report = end_of_day_check(
        day=DAY7, live_entries=entries, live_bars=s.live_bars(), incomplete=s.bars.incomplete,
        store=CandleStore(settings.candles_dir), symbol=SYMBOL,
        make_strategy=lambda: build_strategy({"strategy": "log_xz", "params": PARAMS}),
    )
    assert report["live_signals"] == report["backtest_signals"] == 6 and report["differences"] == []
    assert all(r["fill_source"] == "quote" for r in s.signals if r["status"] == "filled")


# ---------------------------------------------------------------- Price Channel (stop orders) on the recordings
PC = {"strategy": "price_channel", "params": {"length": 20, "execution": "realistic", "lots": 1}}


@pytest.mark.parametrize("day", [DAY5, DAY6, DAY7])
def test_price_channel_paper_fills_match_the_backtest(day: date) -> None:
    """Stops armed on closed 5m bars, filled on the live index price (a cross during the bar's decision fills when
    armed). Compared on the bar each fill is in: no differences on 5, 6 and 7 Oct 2026."""
    from app.paper.catchup import replay_into

    missing = _missing(day, need_candles=True)
    if missing:
        pytest.skip(missing)
    key_of = {i.symbol: i.key for i in index_on(settings.instruments_dir, day).instruments}
    vix = VixSeries()
    s = PaperSession(day=day, strategy=build_strategy(PC), choose=_choose, key_for=key_of.get, quotes=QuoteBook(),
                     cost_table=load_default_cost_table(), model_price=model_price_for(vix.at))
    s.warm(stored_warmup(CandleStore(settings.candles_dir), SYMBOL, day))
    replay_into(s, recording_path(settings.feed_recordings_dir, day), on_vix=vix.add)
    s.end_of_day()
    filled = [r for r in s.signals if r["status"] == "filled"]
    assert filled and s.uses_stops
    report = end_of_day_check(
        day=day, live_entries=[{"time": r["time"], "side": r["side"]} for r in filled], live_bars=s.live_bars(),
        incomplete=s.bars.incomplete, store=CandleStore(settings.candles_dir), symbol=SYMBOL,
        make_strategy=lambda: build_strategy(PC), basis="fill",
    )
    assert report["differences"] == [] and report["live_signals"] == report["backtest_signals"]


# ---------------------------------------------------------------- exit rules on the recordings (paper vs backtest)
@pytest.mark.parametrize("day,strategy,params", [
    (DAY7, "log_xz", {}), (date(2026, 10, 8), "log_xz", {}), (date(2026, 10, 8), "price_channel", {"length": 20}),
])
def test_premium_exits_in_paper_match_the_backtest_on_the_recordings(day: date, strategy: str, params: dict) -> None:
    """The 1:2 premium rule in paper (traded prices of the contract, sold at the bid) against the backtest (the
    contract's stored 1m bars, live timing): the same trades, the same exit reasons, exits within the same minute."""
    from app.backtest.catalog import parse_config
    from app.backtest.execute import execute_run
    from app.options.history import default_history_store
    from app.paper.catchup import replay_into
    from app.paper.live import choose_nearest_weekly_atm, paper_strategy

    missing = _missing(day, need_candles=True)
    if missing:
        pytest.skip(missing)
    if not default_history_store().read(date(2026, 10, 13)):
        pytest.skip("no stored option bars for the 13 Oct 2026 expiry in data/")
    strat, rule = paper_strategy(strategy, {**params, "exit_rule": "premium_1to2"})
    key_of = {i.symbol: i.key for i in index_on(settings.instruments_dir, day).instruments}
    vix = VixSeries()
    s = PaperSession(day=day, strategy=strat, choose=choose_nearest_weekly_atm, key_for=key_of.get, quotes=QuoteBook(),
                     cost_table=load_default_cost_table(), model_price=model_price_for(vix.at), exit_rule=rule)
    s.warm(stored_warmup(CandleStore(settings.candles_dir), SYMBOL, day))
    replay_into(s, recording_path(settings.feed_recordings_dir, day), on_vix=vix.add)
    s.end_of_day()
    cfg = parse_config({"strategy": strategy, "params": params, "symbol": SYMBOL, "timeframe": "5m",
                        "start": day.isoformat(), "end": day.isoformat(), "sessions": ["normal", "weekend_full"],
                        "mode": "options", "slippage_points": 0.2, "exit_rule": "premium_1to2"})
    backtest = execute_run(cfg, lambda _p: None, hash_data=False)["result"]["report"]
    paper = s.report_rows()
    assert len(paper) == len(backtest) > 0
    for p, b in zip(paper, backtest):
        assert p["contract"] == b["contract"] and p["fill_time"] // 60 == b["fill_time"] // 60
        assert p["exit_reason"] == b["exit_reason"], (p, b)
        assert p["exit_time"] // 60 == b["exit_time"] // 60, (p, b)
