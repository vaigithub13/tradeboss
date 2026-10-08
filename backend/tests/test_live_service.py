"""Hub (throttle, protocol), badge states and an end-to-end run: fake socket -> connection ->
recorder -> engine -> backfill -> overlay -> hub -> persistence -> 15:45 reconcile."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.data.history import SymbolMeta, symbol_dir_name, write_meta
from app.data.importer import build_frame, write_parquet
from app.data.store import CandleStore, set_overlay
from app.live.frames import FeedItem, encode_feed, encode_market_info
from app.live.hub import LiveHub, parse_view
from app.live.model import IST, Bar, I1Bar, minute_of
from app.live.persist import ReconcileState, stored_day
from app.live.recorder import iter_records, recording_path
from app.live.service import LiveConfig, LiveService, badge_state

FRI = date(2026, 10, 2)
MON = date(2026, 10, 5)
KEY = "NSE_EQ|INE002A01018"
NIFTY = "NSE_INDEX|Nifty 50"
EQ_DIR = symbol_dir_name(KEY)


def T(h: int, m: int, s: int = 0, day: date = MON) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000


def M(h: int, m: int, day: date = MON) -> int:
    return minute_of(T(h, m, day=day))


def iso(minute: int) -> str:
    return datetime.fromtimestamp(minute * 60, IST).isoformat()


def day_rows(day: date, base: float = 100.0, vol: float = 10.0) -> list[list[Any]]:
    return [[iso(m), base, base + 1, base - 1, base + 0.5, vol, 0] for m in range(M(9, 15, day), M(15, 30, day))][::-1]


@pytest.fixture
def cdir(tmp_path: Path) -> Path:
    raw = [{"t": int(datetime.fromisoformat(r[0]).timestamp()) * 1000, "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5]} for r in day_rows(FRI)]
    df, _ = build_frame(raw, 1)
    write_parquet(df, tmp_path / "candles" / EQ_DIR / "1m.parquet")
    write_meta(tmp_path / "candles" / EQ_DIR, SymbolMeta(instrument={"instrument_key": KEY}))
    return tmp_path / "candles"


# ---------------------------------------------------------------- badge
@pytest.mark.parametrize(
    ("conn", "market", "since", "expected"),
    [
        ("live", True, 1.0, "live"),
        ("live", True, 40.0, "stale"),
        ("live", True, None, "stale"),
        ("live", False, 3.0, "closed"),
        ("live", None, 100.0, "live"),
        ("connecting", True, 1.0, "reconnecting"),
        ("reconnecting", True, 99.0, "reconnecting"),
        ("auth_failed", None, None, "auth_failed"),
        ("off", None, None, "closed"),
        ("closed", False, None, "closed"),
        ("locked_elsewhere", None, None, "locked"),
        ("disabled", None, None, "disabled"),
    ],
)
def test_badge_state(conn: str, market: bool | None, since: float | None, expected: str) -> None:
    assert badge_state(conn, market, since) == expected


# ---------------------------------------------------------------- hub
class Sock:
    def __init__(self) -> None:
        self.msgs: list[dict] = []
        self.fail = False

    async def send_text(self, data: str) -> None:
        if self.fail:
            raise ConnectionError("gone")
        self.msgs.append(json.loads(data))

    def kinds(self, kind: str) -> list[dict]:
        return [m for m in self.msgs if m["type"] == kind]


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_hub(cdir: Path, clock: FakeClock | None = None) -> tuple[LiveHub, CandleStore]:
    store = CandleStore(cdir)
    hub = LiveHub(store, lambda: {"state": "live", "since_last_tick_s": 1.0, "market": "open"},
                  lambda: ("normal", "weekend_full"), clock=clock or FakeClock())
    return hub, store


VIEW = {"type": "view", "symbol": EQ_DIR, "timeframe": "5m", "sessions": ["normal"],
        "indicators": [{"id": "e", "type": "ema", "params": {"length": 5}}]}


async def settle(hub: LiveHub) -> None:
    for _ in range(20):
        await asyncio.sleep(0.005)
        if not any(c.computing for c in hub.clients):
            return


def test_hub_sends_status_on_connect_then_the_tail_for_a_view(cdir: Path) -> None:
    async def go() -> Sock:
        hub, _ = make_hub(cdir)
        s = Sock()
        c = await hub.connect(s)
        assert s.msgs[0]["type"] == "status" and s.msgs[0]["state"] == "live"
        await hub.handle_message(c, json.dumps(VIEW))
        await hub.pump_once()
        await settle(hub)
        return s

    s = asyncio.run(go())
    bar = s.kinds("bar")[0]
    assert bar["symbol"] == EQ_DIR and bar["timeframe"] == "5m" and len(bar["candles"]) == 2 and bar["volume_known"] is True
    assert bar["indicators"][0]["id"] == "e" and len(bar["indicators"][0]["outputs"]["value"] if "value" in bar["indicators"][0]["outputs"] else next(iter(bar["indicators"][0]["outputs"].values()))) == len(bar["times"])


def test_hub_coalesces_updates_to_at_most_five_per_second(cdir: Path) -> None:
    async def go() -> int:
        clock = FakeClock()
        hub, _ = make_hub(cdir, clock)
        s = Sock()
        c = await hub.connect(s)
        await hub.handle_message(c, json.dumps({**VIEW, "indicators": []}))
        for _ in range(200):  # 200 changes in one second of fake time, a pump every 5 ms
            hub.mark_dirty({EQ_DIR})
            await hub.pump_once()
            await asyncio.sleep(0)
            await settle(hub)
            clock.t += 0.005
        return len(s.kinds("bar"))

    n = asyncio.run(go())
    assert 4 <= n <= 6  # 1 s of fake time -> ~5 pushes, not 200


def test_hub_only_updates_tabs_viewing_a_changed_symbol(cdir: Path) -> None:
    async def go() -> tuple[int, int]:
        clock = FakeClock()
        hub, _ = make_hub(cdir, clock)
        a, b = Sock(), Sock()
        ca, cb = await hub.connect(a), await hub.connect(b)
        await hub.handle_message(ca, json.dumps({**VIEW, "indicators": []}))
        await hub.handle_message(cb, json.dumps({**VIEW, "symbol": "NIFTY50", "indicators": []}))
        await hub.pump_once()
        await settle(hub)
        clock.t += 1
        hub.mark_dirty({EQ_DIR})
        await hub.pump_once()
        await settle(hub)
        return len(a.kinds("bar")), len(b.kinds("bar"))

    na, nb = asyncio.run(go())
    assert na == 2 and nb == 0  # b watches another symbol (it does not exist here -> only an error, no bars)


def test_hub_reports_bad_messages_and_unknown_symbols_without_dying(cdir: Path) -> None:
    async def go() -> Sock:
        hub, _ = make_hub(cdir)
        s = Sock()
        c = await hub.connect(s)
        for text in ("not json", json.dumps([1]), json.dumps({"type": "nope"}), json.dumps({"type": "view"}),
                     json.dumps({**VIEW, "indicators": [{"id": "x", "type": "ema", "params": {"length": -3}}]}),
                     json.dumps({"type": "view", "symbol": "NOPE", "timeframe": "5m"})):
            await hub.handle_message(c, text)
        await hub.pump_once()
        await settle(hub)
        await hub.handle_message(c, json.dumps({"type": "ping"}))
        return s

    s = asyncio.run(go())
    assert len(s.kinds("error")) == 6 and s.kinds("pong")


def test_hub_drops_a_client_whose_socket_fails_and_tells_the_service_the_views_changed(cdir: Path) -> None:
    async def go() -> tuple[int, int]:
        hub, _ = make_hub(cdir)
        changes: list[int] = []
        hub.on_views_changed = lambda: changes.append(1)
        s = Sock()
        c = await hub.connect(s)
        await hub.handle_message(c, json.dumps(VIEW))
        s.fail = True
        await hub.pump_once()
        await settle(hub)
        hub.disconnect(c)
        return len(hub.clients), len(changes)

    assert asyncio.run(go()) == (0, 2)


def test_parse_view_validates_indicators_and_defaults_sessions() -> None:
    v = parse_view({"symbol": "S", "timeframe": "5m"}, ("normal",))
    assert v.sessions == ("normal",) and v.indicators == ()
    with pytest.raises(ValueError):
        parse_view({"symbol": "S"}, ("normal",))
    with pytest.raises(ValueError):
        parse_view({"symbol": "S", "timeframe": "5m", "indicators": [{"id": "a", "type": "nope", "params": {}}]}, ("normal",))


# ---------------------------------------------------------------- end to end
class ScriptedWS:
    def __init__(self, script: list[Any], sent: list[bytes]) -> None:
        self.script, self.sent = list(script), sent

    async def recv(self) -> bytes:
        while True:
            if not self.script:
                await asyncio.Event().wait()
            item = self.script.pop(0)
            if callable(item):
                item()  # a clock change between frames
                continue
            await asyncio.sleep(0.002)
            return item

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class OneShotConnector:
    def __init__(self, script: list[Any]) -> None:
        self.script, self.sent, self.calls = script, [], 0

    def __call__(self, uri: str) -> Any:
        self.calls += 1
        outer = self

        class Ctx:
            async def __aenter__(self) -> ScriptedWS:
                return ScriptedWS(outer.script, outer.sent)

            async def __aexit__(self, *a: object) -> None:
                return None

        return Ctx()


class FakeAPI:
    def __init__(self, base: float = 100.0) -> None:
        self.intraday = day_rows(MON, base=base, vol=5.0)
        self.official = day_rows(MON, base=base, vol=7.0)
        self.calls: list[str] = []

    def intraday_candles(self, key: str, *, unit: str = "minutes", interval: int = 1) -> list[Any]:
        self.calls.append(f"intraday {key}")
        return list(self.intraday)

    def historical_candles(self, key: str, d1: date, d2: date, *, unit: str = "minutes", interval: int = 1) -> list[Any]:
        self.calls.append(f"historical {key} {d1}")
        return list(self.official)

    def market_status(self, exchange: str = "NSE") -> dict:
        return {"exchange": exchange}


def eq(ltt: int, px: float, vtt: int, i1: I1Bar | None = None) -> FeedItem:
    return FeedItem(KEY, px, ltt, 1, vtt, None, i1, True)


def nifty(ltt: int, px: float) -> FeedItem:
    return FeedItem(NIFTY, px, ltt, 0, None, None, None, False)


async def until(pred: Any, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.01)):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached in time")


def test_end_to_end_cold_start_backfill_live_bars_persist_and_reconcile(cdir: Path, tmp_path: Path) -> None:
    OPEN = {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN", "NSE_INDEX": "NORMAL_OPEN"}
    CLOSED = {"NSE_EQ": "NORMAL_CLOSE", "NSE_FO": "NORMAL_CLOSE", "NSE_INDEX": "NORMAL_CLOSE"}
    clock = {"t": T(11, 0, 5)}
    api = FakeAPI()
    script: list[Any] = [
        encode_market_info(OPEN, T(11, 0, 5)),
        encode_feed([eq(T(10, 59, 58), 100.0, 50_000), nifty(T(10, 59, 59), 22000.0)], T(11, 0, 5), snapshot=True),
        encode_feed([eq(T(11, 0, 20), 100.4, 50_040), nifty(T(11, 0, 21), 22001.0)], T(11, 0, 21)),
        encode_feed([eq(T(11, 1, 5), 100.6, 50_100, I1Bar(T(11, 0), 100.4, 100.6, 100.4, 100.5, 120)), nifty(T(11, 1, 5), 22002.0)], T(11, 1, 6)),
        lambda: clock.update(t=T(15, 30, 5)),
        encode_market_info(CLOSED, T(15, 30, 2)),
    ]
    conn = OneShotConnector(script)

    async def go() -> LiveService:
        cfg = LiveConfig(candles_dir=cdir, recordings_dir=tmp_path / "rec", state_dir=tmp_path / "state",
                         instruments_dir=tmp_path / "instr", record_start="09:00", record_end="16:05")
        svc = LiveService(cfg, CandleStore(cdir), client_factory=lambda: api, authorize=lambda: "wss://x.invalid/1",
                          connector=conn, now_ms=lambda: clock["t"], sleep=lambda s: asyncio.sleep(0.005))
        sock = Sock()
        await svc.start()
        try:
            c = await svc.hub.connect(sock)
            await svc.hub.handle_message(c, json.dumps({"type": "view", "symbol": EQ_DIR, "timeframe": "1m",
                                                       "sessions": ["normal"], "indicators": []}))
            # the tab's chart symbol joins the subscriptions
            assert KEY in svc.connection._desired and NIFTY in svc.connection._desired  # noqa: SLF001
            await until(lambda: svc.engine.frames >= 5)
            await until(lambda: svc.engine.publishable(KEY) and not svc.engine.outstanding_backfills())
            # --- cold start: history 09:15..10:58 came from the intraday API, live bars after it
            bars = {b.minute: b for b in svc.engine.bars(KEY)}
            assert bars[M(9, 15)].source == "backfill" and bars[M(10, 58)].source == "backfill"
            assert bars[M(10, 59)].partial and bars[M(11, 0)].source in ("tick", "i1")
            assert any("intraday" in x for x in api.calls)
            # --- the REST view includes today's bars through the overlay
            candles, _ = svc.store.load(EQ_DIR, from_time=T(9, 0) // 1000)
            assert candles[0]["time"] == M(9, 15) * 60 and candles[-1]["time"] >= M(11, 0) * 60
            await until(lambda: any(m["type"] == "bar" for m in sock.msgs))
            assert svc.status()["state"] in ("live", "stale", "closed")
            # --- the session ends (market_info closed): persisted, marked unreconciled, minute log written
            await until(lambda: svc.engine.builders[KEY].ended)
            await until(lambda: (MON, KEY) in svc.state.pending())
            assert len(stored_day(cdir, KEY, MON)) == 375
            await until(lambda: (tmp_path / "rec" / "2026-10-05.minutes.jsonl").exists())
            # --- 15:45 intraday reconcile, then the next morning's historical pass
            clock["t"] = T(15, 46)
            await svc.maintenance_once()
            await until(lambda: svc.state.pending() == [])
            assert svc.state.status(MON, KEY) == "intraday_reconciled"
            assert stored_day(cdir, KEY, MON)[M(11, 0)].volume == 5.0
            clock["t"] = T(9, 1, day=date(2026, 10, 6))
            await svc.maintenance_once()
            await until(lambda: svc.state.status(MON, KEY) == "final")
        finally:
            await svc.stop()
        return svc

    svc = asyncio.run(go())
    stored = stored_day(cdir, KEY, MON)
    assert len(stored) == 375 and all(b.volume == 7.0 for b in stored.values())  # historical replaced the intraday bars
    assert len(stored_day(cdir, KEY, FRI)) == 375
    rec = tmp_path / "rec"
    lines = [json.loads(x) for x in (rec / "2026-10-05.minutes.jsonl").read_text().splitlines()]
    phases = {x["phase"] for x in lines}
    assert phases == {"close", "reconciled", "summary"}
    row = next(x for x in lines if x["phase"] == "reconciled" and x["key"] == KEY and x["time"] == "11:00")
    assert row["tick"] and row["official"] and "tick_vs_official" in row
    assert next(x for x in lines if x["phase"] == "summary")["i1_timing"] == {"i1_is_last_completed_bar": 1}  # 11:00 first seen while ltt was in 11:01
    rlines = [json.loads(x) for x in (rec / "2026-10-05.reconcile.jsonl").read_text().splitlines()]
    assert any(x.get("summary") and x["key"] == KEY and x["ok"] for x in rlines)
    # --- the raw feed was recorded: 5 frames + the subscription frame, replayable
    frames = [r for r in iter_records(recording_path(rec, MON)) if r.direction == 0]
    assert len(frames) == 5 and conn.calls == 1
    assert any(json.loads(x)["method"] == "sub" for x in conn.sent)
    set_overlay(None)


def test_a_service_without_a_token_stays_disabled_and_reports_it(cdir: Path, tmp_path: Path) -> None:
    async def go() -> dict:
        cfg = LiveConfig(candles_dir=cdir, recordings_dir=tmp_path / "r", state_dir=tmp_path / "s", instruments_dir=tmp_path / "i")
        svc = LiveService(cfg, CandleStore(cdir), client_factory=lambda: None, enabled=False)
        await svc.start()
        st = svc.status()
        await svc.stop()
        return st

    st = asyncio.run(go())
    assert st["state"] == "disabled" and st["connection"] == "disabled"


def test_reconcile_state_dir_is_created_lazily(tmp_path: Path) -> None:
    s = ReconcileState(tmp_path / "deep" / "dir")
    s.mark(MON, KEY)
    assert (tmp_path / "deep" / "dir" / "reconcile.json").exists()
    assert Bar(1, 1, 1, 1, 1, 1.0, None, "tick").time_s == 60


def test_the_websocket_route_reports_disabled_when_the_live_service_is_off() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/live/status").json()["state"] == "disabled"
        with client.websocket_connect("/api/live/ws") as ws:
            assert ws.receive_json()["state"] == "disabled"


def test_the_websocket_route_serves_a_view_through_a_real_hub(cdir: Path, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    cfg = LiveConfig(candles_dir=cdir, recordings_dir=tmp_path / "r", state_dir=tmp_path / "s", instruments_dir=tmp_path / "i")
    svc = LiveService(cfg, CandleStore(cdir), client_factory=lambda: None, enabled=False)
    app.state.live = svc
    try:
        with TestClient(app) as client:
            app.state.live = svc  # lifespan is off in tests (live_feed_enabled False); set it again
            with client.websocket_connect("/api/live/ws") as ws:
                assert ws.receive_json()["type"] == "status"
                ws.send_text(json.dumps({"type": "view", "symbol": EQ_DIR, "timeframe": "5m"}))
                ws.send_text(json.dumps({"type": "ping"}))
                assert ws.receive_json()["type"] == "pong"  # (the hub pump is not running in this test)
        assert svc.hub.watched_symbols() == set() and svc.hub.clients == []  # the tab left
    finally:
        del app.state.live


def test_after_an_index_backfill_every_final_minute_goes_to_paper_again() -> None:
    """The engine re-sends only its last two bars after a backfill: paper gets the whole day's final minutes."""
    import threading
    from types import SimpleNamespace

    from app.live.model import Bar
    from app.live.service import LiveService
    from app.upstox.instruments import NIFTY_INDEX_KEY

    base = 29857213  # 8 Oct 2026 09:43 IST, in minutes since the epoch
    bars = [Bar(base - 1, 1, 1, 1, 1, 0, None, "i1"), Bar(base, 2, 2, 2, 2, 0, None, "backfill"),
            Bar(base + 1, 3, 3, 3, 3, None, None, "tick")]
    got: list[dict] = []
    stub = SimpleNamespace(
        _elock=threading.Lock(),
        engine=SimpleNamespace(bars=lambda key: bars, current_ts=123),
        paper=SimpleNamespace(on_index_bar=lambda bar, now_ms: got.append({**bar, "now": now_ms})),
    )
    LiveService._resend_index_to_paper(stub, "NSE_FO|1")  # not the index: nothing
    assert got == []
    LiveService._resend_index_to_paper(stub, NIFTY_INDEX_KEY)
    assert [(g["time"], g["source"], g["now"]) for g in got] == [((base - 1) * 60, "i1", 123), (base * 60, "backfill", 123)]


def test_after_the_reconcile_the_days_options_are_captured_once(monkeypatch, tmp_path) -> None:
    import asyncio
    import threading
    from datetime import date
    from types import SimpleNamespace

    from app.live import service as svc
    from app.live.model import Bar

    calls: list[tuple] = []

    def fake_capture(day, day_range, **kw):  # noqa: ANN001, ANN003, ANN202
        calls.append((day, day_range, kw["today"]))
        return {"expiry": "2026-10-13", "strikes": [22500.0], "fetched": 2, "empty": 0, "failed": 0}

    monkeypatch.setattr(svc, "capture_session", fake_capture)
    monkeypatch.setattr(svc, "current_index", lambda _dir: object())
    bars = [Bar(29857000, 22500, 22599.05, 22490, 22520, 0, None, "official"),
            Bar(29857001, 22300, 22310, 22179.9, 22200, 0, None, "official")]
    stub = SimpleNamespace(
        cfg=SimpleNamespace(option_history_dir=tmp_path, instruments_dir=tmp_path),
        client_factory=lambda: object(), _elock=threading.Lock(), _flags=svc._Flags(),
        engine=SimpleNamespace(bars=lambda key, include_withheld=False: bars),
    )
    day = date(2026, 10, 8)
    result = asyncio.run(svc.LiveService.capture_options(stub, day))
    assert result is not None and calls == [(day, (22179.9, 22599.05), day)]
    assert asyncio.run(svc.LiveService.capture_options(stub, day)) is None and len(calls) == 1  # once a day
    stub.cfg.option_history_dir = None
    stub._flags = svc._Flags()
    assert asyncio.run(svc.LiveService.capture_options(stub, day)) is None and len(calls) == 1  # off
