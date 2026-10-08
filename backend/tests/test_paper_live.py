"""The live paper runner the feed service drives, the VIX it prices with, and the routes around it."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.paper.book import fill
from app.paper.live import AlreadyRunning, NotRunning, PaperRunner
from app.paper.pricing import MODEL_SLIPPAGE_POINTS, VixSeries
from app.paper.store import load_day
from app.routes.paper import router as paper_router
from tests import test_paper_session as tps

DAY = date(2026, 10, 5)
IST = timezone(timedelta(hours=5, minutes=30))


def ms(h: int, m: int) -> int:
    return int(datetime(2026, 10, 5, h, m, tzinfo=IST).timestamp()) * 1000


class Fixture:
    """A runner whose sessions use the scripted strategy and the model at 95 (no quotes needed)."""

    def __init__(self, tmp_path, script: dict[int, str]) -> None:
        self.wanted_calls = 0
        self.script = script
        self.runner = PaperRunner(tmp_path, self._make, on_wanted=self._wanted)

    def _make(self, day, strategy, params):
        if strategy != "log_xz":
            raise KeyError(strategy)
        return tps.session(tps.Scripted(self.script), model_price=lambda c, sp, ts: 95.0)

    def _wanted(self) -> None:
        self.wanted_calls += 1

    def feed(self, minutes: list[tuple[int, int, float]]) -> None:
        for h, m, close in minutes:
            self.runner.on_index_bar(tps.minute(h, m, close), now_ms=ms(h, m))


def test_start_feeds_closed_bars_and_saves_the_day_file(tmp_path) -> None:
    fx = Fixture(tmp_path, {0: "BUY"})
    fx.runner.start(DAY, "log_xz", {})
    fx.feed([(9, m, 22600.0) for m in range(15, 22)])
    status = fx.runner.status(ms(9, 22))
    assert status["state"] == "running" and status["day"] == "2026-10-05"
    assert len(status["signals"]) == 1 and status["signals"][0]["status"] == "filled"
    saved = load_day(tmp_path, DAY)
    assert saved["state"] == "running" and len(saved["signals"]) == 1


def test_a_second_start_is_refused_while_running(tmp_path) -> None:
    fx = Fixture(tmp_path, {})
    fx.runner.start(DAY, "log_xz", {})
    with pytest.raises(AlreadyRunning):
        fx.runner.start(DAY, "log_xz", {})


def test_an_unknown_strategy_changes_no_state(tmp_path) -> None:
    fx = Fixture(tmp_path, {})
    with pytest.raises(KeyError):
        fx.runner.start(DAY, "nope", {})
    assert fx.runner.state == "stopped" and fx.runner.session is None


def test_stop_squares_off_at_the_current_quote_and_marks_the_day_stopped(tmp_path) -> None:
    fx = Fixture(tmp_path, {0: "BUY"})
    fx.runner.start(DAY, "log_xz", {})
    fx.feed([(9, m, 22600.0) for m in range(15, 22)])
    assert fx.runner.session.book.position is not None
    fx.runner.stop(ms(9, 22))
    assert fx.runner.state == "stopped" and fx.runner.ended_by == "stopped"
    assert fx.runner.session.book.position is None
    assert fx.runner.session.book.trades[0]["exit_reason"] == "stop"
    assert load_day(tmp_path, DAY)["state"] == "stopped"


def test_stop_without_a_running_strategy_is_refused(tmp_path) -> None:
    with pytest.raises(NotRunning):
        Fixture(tmp_path, {}).runner.stop(ms(9, 20))


def test_a_bar_from_another_day_never_reaches_the_strategy(tmp_path) -> None:
    fx = Fixture(tmp_path, {0: "BUY"})
    fx.runner.start(DAY, "log_xz", {})
    stale = tps.minute(9, 15, 22600.0)
    fx.runner.on_index_bar(stale, now_ms=ms(9, 15) + 86_400_000)  # the next IST day
    assert fx.runner.session.shown == []


def test_wanted_keys_are_announced_to_the_feed_once_they_change(tmp_path) -> None:
    fx = Fixture(tmp_path, {0: "BUY"})
    fx.runner.start(DAY, "log_xz", {})
    assert fx.wanted_calls == 0  # nothing wanted until the first bar gives a spot
    fx.feed([(9, m, 22600.0) for m in range(15, 17)])
    assert fx.wanted_calls == 1  # ATM call and put of the nearest weekly
    assert set(fx.runner.wanted_keys()) == {"KEY|NIFTY 22600 CE 06 OCT 26", "KEY|NIFTY 22600 PE 06 OCT 26"}
    fx.feed([(9, 18, 22600.0)])
    assert fx.wanted_calls == 1  # the same contracts: no new announcement


def test_finishing_the_day_keeps_the_file_and_stops_trading(tmp_path) -> None:
    fx = Fixture(tmp_path, {})
    fx.runner.start(DAY, "log_xz", {})
    fx.feed([(9, m, 22600.0) for m in range(15, 25)])
    fx.runner.finish_day()
    assert fx.runner.state == "stopped" and fx.runner.ended_by == "session ended"
    assert load_day(tmp_path, DAY)["ended_by"] == "session ended"


def test_the_vix_series_gives_the_last_close_at_or_before_a_time() -> None:
    v = VixSeries()
    assert v.at(100) is None
    v.add(100, 12.0)
    v.add(160, 12.5)
    assert v.at(159) == 12.0 and v.at(160) == 12.5 and v.at(10_000) == 12.5
    v.add(160, 12.7)  # a later copy of the same minute replaces it
    assert v.at(170) == 12.7
    v.add(120, 99.0)  # older than the newest bar: ignored
    assert v.at(170) == 12.7


def test_a_modelled_buy_pays_the_slippage_and_a_sell_gives_it_up() -> None:
    from app.backtest.costs import load_default_cost_table
    from app.paper.quotes import QuoteBook

    table = load_default_cost_table()
    kw = dict(key="K", quotes=QuoteBook(), now_ms=0, units=75, day=DAY, cost_table=table,
              model_price=lambda: 100.0, model_slippage=MODEL_SLIPPAGE_POINTS)
    assert fill("BUY", **kw).price == 100.0 + MODEL_SLIPPAGE_POINTS
    assert fill("SELL", **kw).price == 100.0 - MODEL_SLIPPAGE_POINTS
    assert fill("BUY", **kw).mid is None


def test_no_vix_means_no_modelled_fill() -> None:
    from app.backtest.costs import load_default_cost_table
    from app.paper.quotes import QuoteBook

    assert fill("BUY", key="K", quotes=QuoteBook(), now_ms=0, units=75, day=DAY,
                cost_table=load_default_cost_table(), model_price=lambda: None) is None


# ---------------------------------------------------------------- routes

def client_for(runner: PaperRunner, *, enabled: bool = True, now: int = ms(9, 20),
               second: PaperRunner | None = None) -> TestClient:
    from app.paper.desk import PaperDesk

    other = second or Fixture(runner.directory / "slot2", {}).runner
    app = FastAPI()
    app.include_router(paper_router)
    third = Fixture(runner.directory / "slot3", {}).runner
    fourth = Fixture(runner.directory / "slot4", {}).runner
    app.state.live = SimpleNamespace(enabled=enabled, paper=PaperDesk({"1": runner, "2": other, "3": third, "4": fourth}),
                                     now_ms=lambda: now)
    return TestClient(app)


def test_routes_start_status_stop_and_day(tmp_path) -> None:
    fx = Fixture(tmp_path, {})
    c = client_for(fx.runner)
    assert c.get("/api/paper/status").json()["state"] == "stopped"
    r = c.post("/api/paper/start", json={"strategy": "log_xz", "params": {}})
    assert r.status_code == 200 and r.json()["state"] == "running"
    assert c.post("/api/paper/start", json={"strategy": "log_xz"}).status_code == 409
    assert c.post("/api/paper/stop").json()["state"] == "stopped"
    assert c.post("/api/paper/stop").status_code == 409
    assert c.get("/api/paper/day", params={"day": "2026-10-05"}).status_code == 200
    assert c.get("/api/paper/day", params={"day": "2026-10-06"}).status_code == 404


def test_routes_refuse_unknown_strategies_and_a_disabled_feed(tmp_path) -> None:
    fx = Fixture(tmp_path, {})
    assert client_for(fx.runner).post("/api/paper/start", json={"strategy": "bogus"}).status_code == 400
    assert client_for(fx.runner, enabled=False).post("/api/paper/start", json={"strategy": "log_xz"}).status_code == 409


def test_week_route_totals_the_week(tmp_path) -> None:
    fx = Fixture(tmp_path, {})
    body = client_for(fx.runner).get("/api/paper/week", params={"day": "2026-10-05"}).json()
    assert body["week"] == "2026-W41" and body["days"] == []


def test_the_reconcile_check_waits_until_the_day_has_ended(tmp_path) -> None:
    fx = Fixture(tmp_path, {})
    fx.runner.start(DAY, "log_xz", {})
    assert fx.runner.reconcile_check(store=None, symbol="NIFTY50") is None  # still running: nothing to check


def test_paper_keys_are_subscribed_ahead_of_the_recorder_set() -> None:
    """The option list is capped: a contract the paper strategy needs must not wait behind the recorder's keys."""
    from app.live.spreads.keys import compose_keys

    recorder = [f"NSE_FO|{i}" for i in range(25)]
    paper = ["NSE_FO|PAPER"]
    chart = ["NSE_INDEX|Nifty 50"]
    keys = compose_keys(chart, list(dict.fromkeys([*paper, *recorder])), enabled=True)
    assert "NSE_FO|PAPER" in keys


def test_routes_run_a_second_strategy_in_slot_2_beside_the_first(tmp_path) -> None:
    one, two = Fixture(tmp_path, {}), Fixture(tmp_path / "slot2", {})
    c = client_for(one.runner, second=two.runner)
    assert c.post("/api/paper/start", json={"strategy": "log_xz"}).json()["slot"] == "1"
    r = c.post("/api/paper/start", json={"strategy": "log_xz", "params": {"use_target": True}, "slot": "2"})
    assert r.status_code == 200 and r.json()["slot"] == "2" and r.json()["params"] == {"use_target": True}
    body = c.get("/api/paper/status").json()
    assert body["state"] == "running" and [s["state"] for s in body["slots"]] == ["running", "running", "stopped", "stopped"]
    assert c.post("/api/paper/stop", params={"slot": "2"}).json()["state"] == "stopped"
    assert [s["state"] for s in c.get("/api/paper/status").json()["slots"]] == ["running", "stopped", "stopped", "stopped"]
    assert c.get("/api/paper/day", params={"day": "2026-10-05", "slot": "2"}).status_code == 200
    assert c.post("/api/paper/start", json={"strategy": "log_xz", "slot": "5"}).status_code == 400


def test_a_strategy_with_a_target_or_stop_is_refused_because_paper_does_not_apply_them(tmp_path) -> None:
    """A backtest exits on the bracket; paper acts on BUY/SELL only. Running it would forward-test another strategy."""
    from app.paper.live import PaperError

    def make(day, strategy, params):
        s = tps.session(tps.Scripted({}), model_price=lambda c, sp, ts: 95.0)
        s.strategy.use_target, s.strategy.use_stop = bool(params.get("use_target")), bool(params.get("use_stop"))
        return s

    runner = PaperRunner(tmp_path, make)
    with pytest.raises(PaperError, match="target"):
        runner.start(DAY, "log_xz", {"use_target": True})
    assert runner.state == "stopped" and load_day(tmp_path, DAY) is None
    runner.start(DAY, "log_xz", {})
    assert runner.state == "running"


def test_price_channel_is_offered_for_paper() -> None:
    from app.backtest.catalog import build_strategy

    body = client_for(PaperRunner(__import__("pathlib").Path("/nonexistent"), lambda *a: None)).get("/api/paper/strategies").json()
    names = {s["name"]: s for s in body["strategies"]}
    assert names["price_channel"]["label"] == "Price Channel (length 20, 5m)"
    assert build_strategy({"strategy": "price_channel", "params": names["price_channel"]["params"]}).length == 20


def test_the_strategies_route_offers_the_exit_rules(tmp_path) -> None:
    c = client_for(Fixture(tmp_path, {}).runner)
    body = c.get("/api/paper/strategies").json()
    assert [r["name"] for r in body["exit_rules"]] == ["premium_1to2", "atr_1to2"]
