"""Slot 8 paper book: selection, exits, charges, replay, and no broker path."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.live.spreads.keys import compose_keys
from app.main import app
from app.scanner.book import on_bar, take_signals
from app.scanner.config import ScannerSettings
from app.scanner.runner import fresh_state, walk_day
from app.scanner.service import ScannerService, ranking_is_stale
from tests.scanner_bars import IST, at, bar, flat_session

DAY = date(2026, 1, 6)  # Tuesday


def _state() -> dict:
    state = fresh_state(DAY)
    return state


def _pass_row(symbol: str, edge: float) -> dict:
    return {
        "id": f"orb15:{symbol}:1:long",
        "symbol": symbol,
        "setup": "orb15",
        "side": "long",
        "instrument": "equity_intraday",
        "status": "triggered",
        "decision": "PASS",
        "qty": 10,
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "entry_time": at(DAY, 9, 30),
        "target_r": 2,
        "risk_per_share": 1.0,
        "target_pct": 50 + edge,
        "stop_pct": 40,
        "open_pct": 10,
        "avg_rupees": 10,
        "win_rate": 0.5,
        "verdict": "GOOD",
        "proven": False,
    }


def test_one_open_slot_then_the_next_name_after_the_exit() -> None:
    state = _state()
    settings = ScannerSettings(max_open_positions=1, max_trades_per_day=3, auto_trade=True)
    best = _pass_row("TCS", 20)
    second = _pass_row("INFY", 5)
    too_late = _pass_row("SBIN", 40)
    too_late["entry_time"] = at(DAY, 14, 30)
    rows = [best, second, too_late]
    state["signals"] = rows
    taken = take_signals(rows, settings, state)
    assert [row["symbol"] for row in taken] == ["TCS"]
    assert len(state["open"]) == 1
    closed = on_bar(state, "TCS", bar(DAY, 10, 0, 100.2, 102.2, 100.1, 102.0, 1000), settings)
    assert closed[0]["exit_reason"] == "target"
    assert state["open"] == []
    nxt = take_signals(rows, settings, state)
    assert [row["symbol"] for row in nxt] == ["INFY"]
    assert state["open"][0]["symbol"] == "INFY"


def test_book_takes_the_better_edge_and_stops_at_the_daily_max() -> None:
    state = _state()
    settings = ScannerSettings(max_trades_per_day=1, auto_trade=True)
    rows = [_pass_row("INFY", 5), _pass_row("TCS", 20)]
    state["signals"] = rows
    taken = take_signals(rows, settings, state)
    assert [row["symbol"] for row in taken] == ["TCS"]
    assert state["open"][0]["symbol"] == "TCS"
    # the same name cannot be added twice, and the cap is already used
    again = take_signals(rows, settings, state)
    assert again == []


def test_target_stop_time_and_trail() -> None:
    settings = ScannerSettings(trail_breakeven=False, auto_trade=True)
    state = _state()
    state["signals"] = [_pass_row("INFY", 10)]
    take_signals(state["signals"], settings, state)
    target_bar = bar(DAY, 10, 0, 100.2, 102.2, 100.1, 102.0, 1000)
    closed = on_bar(state, "INFY", target_bar, settings)
    assert closed[0]["exit_reason"] == "target"
    assert closed[0]["r"] == 2
    assert closed[0]["charges"] > 0
    assert closed[0]["net"] < closed[0]["gross"]

    state = _state()
    state["signals"] = [_pass_row("TCS", 10)]
    take_signals(state["signals"], settings, state)
    stop_bar = bar(DAY, 10, 0, 99.8, 99.9, 98.5, 99.0, 1000)
    assert on_bar(state, "TCS", stop_bar, settings)[0]["exit_reason"] == "stop"

    trail = ScannerSettings(trail_breakeven=True, auto_trade=True)
    state = _state()
    state["signals"] = [_pass_row("SBIN", 10)]
    take_signals(state["signals"], trail, state)
    # 1R is 101. The stop stays at 99 on this bar, then moves to entry for the next one.
    on_bar(state, "SBIN", bar(DAY, 10, 0, 100.2, 101.2, 100.1, 100.8, 1000), trail)
    assert state["open"][0]["stop"] == 100.0
    closed = on_bar(state, "SBIN", bar(DAY, 10, 5, 100.4, 100.5, 99.9, 100.1, 1000), trail)
    assert closed[0]["exit_reason"] == "stop"
    assert closed[0]["r"] == 0


def test_auto_take_stays_off_until_a_current_replay_exists(tmp_path: Path) -> None:
    from app.scanner.service import replay_gate
    from app.scanner.version import SCANNER_CODE_VERSION

    assert ScannerSettings().auto_trade is False
    service = ScannerService(tmp_path)
    assert service.settings().auto_trade is False
    ready, reason = replay_gate(tmp_path)
    assert ready is False
    assert reason
    try:
        service.update_settings({"auto_trade": True})
        raise AssertionError("auto-take turned on with no replay")
    except ValueError as exc:
        assert reason in str(exc) or str(exc)
    old = {
        "source": "replay",
        "completed": True,
        "error": None,
        "code_version": "stale",
        "reported": True,
    }
    (tmp_path / "replay").mkdir()
    (tmp_path / "replay" / "2026-01-05.json").write_text(__import__("json").dumps(old), encoding="utf-8")
    ready, reason = replay_gate(tmp_path)
    assert ready is False
    assert "older" in reason
    current = dict(old, code_version=SCANNER_CODE_VERSION)
    (tmp_path / "replay" / "2026-01-06.json").write_text(__import__("json").dumps(current), encoding="utf-8")
    ready, reason = replay_gate(tmp_path)
    assert ready is True
    assert reason == ""
    updated = service.update_settings({"auto_trade": True})
    assert updated.auto_trade is True


def _inside_range_day(day: date) -> list[dict]:
    """A valid opening range that never breaks, so the signal stays untriggered."""
    bars = [
        bar(day, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(day, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
    ]
    hour, minute = 9, 30
    while (hour, minute) <= (15, 10):
        bars.append(bar(day, hour, minute, 100.4, 100.7, 100.2, 100.5, 1000))
        minute += 5
        if minute >= 60:
            hour += 1
            minute -= 60
    return bars


def test_untriggered_signal_is_expired_at_1430_not_forming() -> None:
    from app.scanner.config import SETUP_IDS

    day = date(2026, 1, 6)
    settings = ScannerSettings(setups={name: name == "orb15" for name in SETUP_IDS}, auto_trade=False, tm_paths=5)
    state = walk_day({"FLAT": _inside_range_day(day)}, {"FLAT": "Test"}, [], day=day, settings=settings)
    rows = [row for row in state["signals"] if row["symbol"] == "FLAT"]
    assert rows
    assert all(row["status"] == "expired" for row in rows)
    assert all(row["forming"] is False for row in rows)
    assert all(row["status"] != "waiting" for row in rows)


def test_replay_day_finishes_and_stays_out_of_the_live_book(tmp_path: Path) -> None:
    settings = ScannerSettings(tm_paths=40, tm_seed=20261123, max_trades_per_day=3, auto_trade=True)
    symbols = {"INFY": _trend_tape()}
    state = walk_day(symbols, {"INFY": "IT"}, [], day=DAY, settings=settings, source="replay")
    assert state["source"] == "replay"
    assert state["report"]["day"] == DAY.isoformat()
    assert state["flattened"] is True
    service = ScannerService(tmp_path)
    saved = service.replay(DAY, symbols, {"INFY": "IT"}, [], settings)
    assert (tmp_path / "replay" / f"{DAY.isoformat()}.json").is_file()
    assert saved["source"] == "replay"
    assert not (tmp_path / "days").exists()


def test_scanner_package_has_no_order_route() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "scanner"
    forbidden = ("place_order", "/order/", "modify_order", "cancel_order", "v2/order", "v3/order", "/portfolio/")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"


def test_scanner_keys_are_appended_after_options() -> None:
    chart = ["NSE_INDEX|Nifty 50"] + [f"chart-{i}" for i in range(20)]
    options = [f"NSE_FO|{i}" for i in range(45)]
    stocks = [f"NSE_EQ|{i}" for i in range(5)]
    keys = compose_keys(chart, options, enabled=True, scanner=stocks)
    assert keys[:12] == chart[:12]
    assert keys[12:52] == options[:40]
    assert keys[52:] == stocks


def test_rank_staleness_and_http_roundtrip(tmp_path: Path) -> None:
    assert ranking_is_stale(tmp_path / "missing.json", date(2026, 1, 7))
    symbols = {"INFY": _trend_tape()}

    def feed(_now: datetime):
        return symbols, {"INFY": "IT"}, [], []

    service = ScannerService(tmp_path, feed=feed)
    app.state.scanner = service
    try:
        _http(client_checks := None)
    finally:
        if getattr(app.state, "scanner", None) is service:
            del app.state.scanner


def _http(_marker: object) -> None:
    with TestClient(app) as client:
        put = client.put("/api/scanner/settings", json={"max_loss_per_trade": 400, "universe": "banknifty"})
        assert put.status_code == 200
        assert put.json()["max_loss_per_trade"] == 400
        assert client.get("/api/scanner/settings").json()["universe"] == "banknifty"
        scanned = client.post("/api/scanner/scan")
        assert scanned.status_code == 200
        body = client.get("/api/scanner/status").json()
        assert body["universe"] == "banknifty"
        assert "signals" in client.get("/api/scanner/signals").json()
        assert "rows" in client.get("/api/scanner/heatmap").json()


def _trend_tape() -> list[dict]:
    """Several sessions that break a valid opening range and run to the 2R target, then one more."""
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(12)]
    days = [d for d in days if d.weekday() < 5][:10]
    tape: list[dict] = []
    for day in days:
        tape.extend(_orb_day(day))
    return tape


def _orb_day(day: date) -> list[dict]:
    bars = []
    # quiet prior bars live in the previous part of the tape; this session is the range plus a rally
    bars.append(bar(day, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000))
    bars.append(bar(day, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000))
    # hold inside, then break, then rally through 2R (entry ~101.4, stop 100.5, target ~103.3)
    price = 100.5
    hour, minute = 9, 30
    first = True
    while (hour, minute) <= (15, 10):
        if first:
            bars.append(bar(day, hour, minute, 100.8, 101.8, 100.6, 101.6, 3000))
            price = 101.6
            first = False
        else:
            price = min(price + 0.4, 106)
            bars.append(bar(day, hour, minute, price - 0.1, price + 0.3, price - 0.2, price, 1000))
        minute += 5
        if minute >= 60:
            hour += 1
            minute -= 60
    return bars
