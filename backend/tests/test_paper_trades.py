"""All paper trades, one row per trade over the slots and days: the Trades view's data (GET /api/paper/trades)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.paper.store import save_day
from app.paper.trades import trade_rows

IST = timezone(timedelta(hours=5, minutes=30))


def t(day: date, h: int, m: int, s: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp())


D8 = date(2026, 10, 8)


def trade(**over):
    base = {
        "direction": "SHORT", "symbol": "NIFTY 22500 PE 13 OCT 26", "key": "NSE_FO|44613", "kind": "PE",
        "strike": 22500.0, "expiry": "2026-10-13", "lots": 1, "units": 65, "lot_size": 65,
        "index_entry_time": t(D8, 9, 24, 37), "index_exit_time": t(D8, 10, 40, 49),
        "entry_at_ms": t(D8, 9, 24, 37) * 1000 + 30, "exit_at_ms": t(D8, 10, 40, 49) * 1000,
        "entry_price": 126.25, "exit_price": 175.5, "entry_source": "quote", "exit_source": "quote",
        "entry_mid": None, "exit_mid": None, "entry_slippage": None, "exit_slippage": None,
        "gross": 3201.25, "charges_entry": 27.3, "charges_exit": 45.51, "charges": 72.81, "net": 3128.44, "win": True,
        "entry_reason": "sell stop SE at 22521.05", "exit_reason": "premium_target",
        "levels": {"index_stop": None, "index_target": None, "premium_stop": 101.0, "premium_target": 176.75},
        "signal_time": t(D8, 9, 15), "trigger_index": 22521.05, "index_entry": 22521.05, "delta": -0.5,
        "index_exit": 22420.1, "source": "live",
    }
    return {**base, **over}


@pytest.fixture
def dirs(tmp_path):
    slots = {s: (tmp_path if s == "1" else tmp_path / f"slot{s}") for s in ("1", "2", "3", "4")}
    save_day(slots["4"], D8, {"source": "live", "exit_rule": {"kind": "premium", "stop": 0.2, "target": 0.4},
                              "requested_params": {"exit_rule": "premium_1to2"}, "trades": [trade()], "signals": []})
    # a pre-exit-rule day: no stored levels or entry details; the signal record and the candles fill them in
    old = trade(levels=None, signal_time=None, trigger_index=None, index_entry=None, delta=None, index_exit=None,
                source=None, exit_reason="square_off", entry_at_ms=t(date(2026, 10, 7), 9, 26) * 1000 + 137,
                exit_at_ms=t(date(2026, 10, 7), 15, 15) * 1000, net=-500.0)
    del old["source"]
    save_day(slots["1"], date(2026, 10, 7), {"trades": [old], "signals": [
        {"time": t(date(2026, 10, 7), 9, 20), "decided_at_ms": old["entry_at_ms"], "side": "SELL",
         "index_price": 22610.0, "status": "filled"}]})
    save_day(slots["3"] / "replay", D8, {"source": "replay", "trades": [trade(net=1.0)], "signals": []})
    save_day(slots["2"], date(2026, 10, 6), {"source": "replay", "trades": [trade(net=2.0)], "signals": []})
    return slots


class Candles:
    def load(self, symbol, from_time=None, to_time=None, session_types=None):
        bars = [{"time": t(date(2026, 10, 7), 15, 15), "open": 22555.0, "high": 1, "low": 1, "close": 22560.0}]
        return [b for b in bars if from_time <= b["time"] < to_time], None


def test_rows_cover_the_live_days_of_every_slot_in_entry_order(dirs) -> None:
    rows = trade_rows(dirs, candles=Candles())
    assert [(r["date"], r["slot"]) for r in rows] == [("2026-10-07", "1"), ("2026-10-08", "4")]
    r = rows[1]
    assert (r["direction"], r["side"], r["contract"], r["lots"], r["units"]) == ("PE", "SHORT", "NIFTY 22500 PE 13 OCT 26", 1, 65)
    assert r["signal_time"] == t(D8, 9, 15) and r["trigger_index"] == 22521.05 and r["entry_time"] == t(D8, 9, 24, 37)
    assert (r["entry_premium"], r["entry_source"]) == (126.25, "real")
    assert r["premium_stop"] == 101.0 and r["premium_target"] == 176.75 and r["estimated"] == ["index"]
    assert r["index_stop"] == pytest.approx(22521.05 + 25.25 / 0.5)
    assert (r["exit_time"], r["index_exit"], r["exit_premium"]) == (t(D8, 10, 40, 49), 22420.1, 175.5)
    assert r["nifty_points"] == pytest.approx(22420.1 - 22521.05) and r["premium_points"] == pytest.approx(49.25)
    assert (r["gross"], r["charges"], r["net"]) == (3201.25, 72.81, 3128.44)
    assert r["exit_reason"] == "target" and r["r_multiple"] == pytest.approx(3128.44 / (25.25 * 65), abs=1e-3)
    assert r["source"] == "live" and r["index_from_candles"] == []


def test_an_older_trade_is_filled_from_its_signal_and_the_candles(dirs) -> None:
    r = trade_rows(dirs, candles=Candles())[0]
    assert r["signal_time"] == t(date(2026, 10, 7), 9, 20) and r["trigger_index"] == 22610.0
    assert r["index_entry"] == 22610.0 and r["index_exit"] == 22555.0 and r["index_from_candles"] == ["exit"]
    assert r["exit_reason"] == "square-off" and r["r_multiple"] is None and r["source"] == "live"


def test_filters_and_replays(dirs) -> None:
    assert [r["slot"] for r in trade_rows(dirs, slot="4")] == ["4"]
    assert trade_rows(dirs, from_day=D8, to_day=D8)[0]["date"] == "2026-10-08"
    assert len(trade_rows(dirs, from_day=date(2026, 10, 9))) == 0
    with_replay = trade_rows(dirs, include_replay=True)
    assert sorted((r["slot"], r["source"]) for r in with_replay) == [
        ("1", "live"), ("2", "replay"), ("3", "replay"), ("4", "live")]
