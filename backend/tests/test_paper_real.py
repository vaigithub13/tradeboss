"""Manual real-trades log (app/paper/real.py): compare, order helper, store. No paper engine, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.paper.real import (
    DEFAULT_ACCOUNT_STOP,
    RealTradeStore,
    account_stop_progress,
    compare_trade,
    join_real_rows,
    order_helper,
    paper_trade_key,
    real_net,
    real_totals,
)


def test_paper_trade_key_and_net() -> None:
    assert paper_trade_key(slot="3", day="2026-11-11", fill_time=1794380160) == "3:2026-11-11:1794380160"
    assert real_net(entry_price=212.65, exit_price=296.15, units=65, charges=90.37) == pytest.approx(5337.13)


def test_compare_trade_entry_exit_delay_and_net_diff() -> None:
    paper = {
        "entry_premium": 212.65, "exit_premium": 296.15, "net": 5337.13,
        "fill_time": 1000, "exit_time": 2000, "units": 65,
    }
    real = {
        "entry_price": 213.0, "exit_price": 295.0, "charges": 100.0,
        "entry_time": 1005, "exit_time": 2010, "units": 65,
    }
    c = compare_trade(paper, real)
    assert c["entry_price_diff"] == pytest.approx(0.35)
    assert c["exit_price_diff"] == pytest.approx(-1.15)
    assert c["entry_delay_s"] == 5
    assert c["exit_delay_s"] == 10
    assert c["net"] == real_net(entry_price=213.0, exit_price=295.0, units=65, charges=100.0)
    assert c["net_diff"] == pytest.approx(c["net"] - 5337.13)


def test_order_helper_from_open_premium_position() -> None:
    open_pos = {
        "symbol": "NIFTY 25300 PE 17 NOV 26",
        "units": 65, "lots": 1, "lot_size": 65,
        "entry": {"price": 212.65},
        "levels": {"premium_stop": 170.12, "premium_target": 297.71},
    }
    h = order_helper(open_pos)
    assert h == {
        "contract": "NIFTY 25300 PE 17 NOV 26",
        "quantity": 65, "lots": 1, "lot_size": 65, "side": "BUY",
        "entry_premium": 212.65,
        "stop_trigger": 170.12,
        "stop_limit": 170.07,
        "target_limit": 297.71,
    }
    assert order_helper(None) is None
    assert order_helper({"symbol": "X", "units": 65, "levels": {}}) is None


def test_account_stop_progress_warns_when_loss_reaches_limit() -> None:
    ok = account_stop_progress(real_net_total=-1200, account_stop=5000)
    assert ok["reached"] is False and ok["fraction"] == pytest.approx(0.24) and ok["loss_used"] == 1200
    hit = account_stop_progress(real_net_total=-5000, account_stop=5000)
    assert hit["reached"] is True and hit["fraction"] == 1.0
    profit = account_stop_progress(real_net_total=800, account_stop=5000)
    assert profit["reached"] is False and profit["fraction"] == 0.0 and profit["loss_used"] == 0


def test_store_upsert_replace_delete_and_settings(tmp_path: Path) -> None:
    store = RealTradeStore(tmp_path)
    assert store.load_settings()["account_stop"] == DEFAULT_ACCOUNT_STOP
    assert store.save_settings(account_stop=4000) == {"account_stop": 4000.0}
    assert store.load_settings()["account_stop"] == 4000.0
    with pytest.raises(ValueError, match="positive"):
        store.save_settings(account_stop=0)

    key = paper_trade_key(slot="3", day="2026-11-11", fill_time=1000)
    saved = store.upsert({
        "key": key, "slot": "3", "day": "2026-11-11", "fill_time": 1000,
        "contract": "NIFTY 25300 PE 17 NOV 26",
        "entry_time": 1005, "entry_price": 213.0,
        "exit_time": 2000, "exit_price": 295.0,
        "exit_reason": "target", "charges": 100.0, "units": 65,
    })
    assert saved["net"] == real_net(entry_price=213.0, exit_price=295.0, units=65, charges=100.0)
    assert len(store.load_trades()) == 1

    store.upsert({**saved, "exit_price": 300.0, "charges": 90.0})
    assert len(store.load_trades()) == 1
    assert store.load_trades()[0]["exit_price"] == 300.0

    with pytest.raises(ValueError, match="exit_reason"):
        store.upsert({**saved, "exit_reason": "oops"})
    with pytest.raises(ValueError, match="key does not match"):
        store.upsert({**saved, "key": "1:2026-11-11:1000"})

    assert store.delete(key) is True
    assert store.load_trades() == []
    assert store.delete(key) is False


def test_join_and_totals_daily_and_running(tmp_path: Path) -> None:
    store = RealTradeStore(tmp_path)
    paper = [
        {"slot": "3", "date": "2026-11-11", "fill_time": 1000, "entry_premium": 100.0,
         "exit_premium": 140.0, "exit_time": 2000, "net": 2500.0, "units": 65},
        {"slot": "3", "date": "2026-11-09", "fill_time": 500, "entry_premium": 80.0,
         "exit_premium": 60.0, "exit_time": 1500, "net": -1400.0, "units": 65},
        {"slot": "1", "date": "2026-11-11", "fill_time": 1100, "entry_premium": 50.0,
         "exit_premium": 55.0, "exit_time": 2100, "net": 200.0, "units": 65},
    ]
    store.upsert({
        "key": paper_trade_key(slot="3", day="2026-11-11", fill_time=1000),
        "slot": "3", "day": "2026-11-11", "fill_time": 1000, "contract": "A",
        "entry_time": 1010, "entry_price": 101.0, "exit_time": 2010, "exit_price": 139.0,
        "exit_reason": "target", "charges": 80.0, "units": 65,
    })
    store.upsert({
        "key": paper_trade_key(slot="3", day="2026-11-09", fill_time=500),
        "slot": "3", "day": "2026-11-09", "fill_time": 500, "contract": "B",
        "entry_time": 510, "entry_price": 81.0, "exit_time": 1510, "exit_price": 59.0,
        "exit_reason": "stop", "charges": 70.0, "units": 65,
    })
    joined = join_real_rows(paper, store.load_trades())
    assert joined[0]["real"] is not None and joined[0]["real_vs_paper"]["entry_delay_s"] == 10
    assert joined[2]["real"] is None
    totals = real_totals(joined, today="2026-11-11", account_stop=5000)
    assert totals["logged"] == 2
    assert totals["day"]["trades"] == 1
    assert totals["running"]["trades"] == 2
    assert totals["stop"]["account_stop"] == 5000
