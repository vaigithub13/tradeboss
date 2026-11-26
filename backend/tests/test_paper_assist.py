"""Manual assist: ticket math, the channel sentence, the guard, and the journal. No broker, no network."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.paper.assist import (
    AssistJournal,
    build_ticket,
    chart_for_close,
    explain_channel,
    guard_banner,
    max_loss,
    option_charges,
    premium_bracket,
    reminder_due,
    rule_followed,
    story_due,
    suggested_limit,
)
from app.paper.real import paper_trade_key

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-11-27"


def _epoch(hour: int, minute: int) -> int:
    return int(datetime(2026, 11, 27, hour, minute, tzinfo=IST).timestamp())


def _bar(hour: int, minute: int, high: float, low: float, close: float) -> dict:
    return {
        "time": _epoch(hour, minute), "open": close, "high": high, "low": low, "close": close, "volume": 1,
    }


def test_stop_target_and_the_suggested_limit() -> None:
    assert premium_bracket(100) == (80.0, 140.0)
    assert premium_bracket(100.05) == (80.04, 140.07)
    assert suggested_limit(100.03, None) == 100.05
    assert suggested_limit(None, 100.02) == 100.0
    assert max_loss(100, 80, 75, 50) == 1550.0
    charges = option_charges(100, 80, 65, date(2026, 11, 27))
    assert charges >= 0


def test_the_channel_sentence_uses_the_bar_that_set_the_high() -> None:
    bars = [
        _bar(9, 15, 100, 98, 99),
        _bar(9, 20, 110, 99, 108),
        _bar(9, 25, 105, 97, 100),
        _bar(9, 30, 104, 96, 101),
        _bar(9, 35, 103, 95, 102),
    ]
    facts = explain_channel(
        bars, side="BUY", index_price=111, order_price=110.05, fill_time=bars[-1]["time"], length=3, tick=0.05,
    )
    assert "5m price 111.00 crossed the 3-bar channel high 110.00 set at 09:20" in facts["why"]
    assert "channel low 96.00" in facts["why"]
    assert "above VWAP" in facts["why"]
    assert facts["chart"]["signal_time"] == bars[-1]["time"]
    assert any(c["time"] == bars[1]["time"] for c in facts["chart"]["candles"])


def test_the_close_chart_runs_from_the_signal_bar_to_the_exit() -> None:
    bars = [
        _bar(9, 15, 100, 98, 99),
        _bar(9, 20, 110, 99, 108),
        _bar(9, 35, 103, 95, 102),
    ]
    chart = chart_for_close(
        {"chart": {"signal_time": bars[1]["time"], "channel_high": 110.0, "channel_low": 96.0}},
        bars,
        bars[-1]["time"],
    )
    assert chart is not None
    assert [c["time"] for c in chart["candles"]] == [bars[1]["time"], bars[-1]["time"]]
    assert chart["channel_high"] == 110.0
    assert chart["exit_time"] == bars[-1]["time"]


def test_a_ticket_prices_the_ask_and_one_lot() -> None:
    decided = _epoch(10, 5) * 1000 + 200
    ticket = build_ticket(
        slot="4", day=DAY,
        signal={
            "decided_at_ms": decided, "side": "BUY", "status": "filled", "symbol": "NIFTY 24500 CE 27 NOV 26",
            "fill_price": 198.5, "index_price": 24510, "order_price": 24508.05, "time": _epoch(10, 5),
        },
        bars=[], quote={"bid": 199.5, "ask": 200.03}, ltp=199.8, lot_size=65, lots=1,
    )
    assert ticket["key"] == paper_trade_key(slot="4", day=DAY, fill_time=decided // 1000)
    assert ticket["action"] == "BUY"
    assert ticket["option_side"] == "CE"
    assert ticket["suggested_limit"] == 200.05
    assert ticket["stop"] == premium_bracket(200.05)[0]
    assert ticket["target"] == premium_bracket(200.05)[1]
    assert ticket["quantity"] == 65
    assert ticket["max_loss"] == max_loss(200.05, ticket["stop"], 65, ticket["charges"])
    assert ticket["ltp"] == 199.8


def test_guard_banner_counts_takes_and_does_not_count_skips() -> None:
    taken = {"kind": "taken", "day": DAY, "net": -1000.0, "exit_time": 1}
    settings = {
        "max_trades": 3, "day_loss": 3000.0, "account_pause": 10000.0, "start_balance": None,
    }
    banner = guard_banner([taken, taken, {"kind": "skipped", "day": DAY}], today=DAY, settings=settings)
    assert banner["trades_today"] == 2
    assert banner["at_trade_cap"] is False
    assert banner["stop_for_today"] is False
    assert banner["pause"] is False
    assert banner["needs_start_balance"] is True
    stopped = guard_banner(
        [{"kind": "taken", "day": DAY, "net": -3000.0, "exit_time": 1}], today=DAY, settings=settings,
    )
    assert stopped["stop_for_today"] is True
    full = guard_banner([taken, taken, taken], today=DAY, settings=settings)
    assert full["at_trade_cap"] is True
    paused = guard_banner(
        [{"kind": "taken", "day": DAY, "net": -10000.0, "exit_time": 1}],
        today=DAY,
        settings={**settings, "start_balance": 200000.0},
    )
    assert paused["pause"] is True
    assert paused["equity"] == 190000.0


def test_journal_take_skip_and_close_recompute_from_the_fill(tmp_path: Path) -> None:
    journal = AssistJournal(tmp_path)
    key = paper_trade_key(slot="4", day=DAY, fill_time=1_000)
    saved = journal.take({
        "key": key, "slot": "4", "day": DAY, "fill_time": 1_000, "fill_price": 100,
        "entry_time": 1_030, "lot_size": 65, "sl_set": True, "tp_set": True,
        "contract": "NIFTY 24500 CE 27 NOV 26", "option_side": "CE", "paper_fill": 99.5,
    })
    assert saved["stop"] == 80.0
    assert saved["target"] == 140.0
    assert saved["units"] == 65
    with pytest.raises(ValueError, match="taken"):
        journal.skip({"key": key, "slot": "4", "day": DAY, "fill_time": 1_000, "reason": "late"})
    closed = journal.close({
        "key": key, "exit_price": 140, "exit_time": 2_000,
        "paper_exit_price": 141, "paper_exit_time": 1_900, "paper_exit_reason": "target", "paper_net": 2500,
    })
    assert closed["net"] == pytest.approx(
        (140 - 100) * 65 - closed["charges"],
    )
    assert rule_followed(closed)["followed"] is True
    late = {**closed, "entry_time": 1_000 + 90}
    assert rule_followed(late)["entered_on_time"] is False
    early = {**closed, "exit_time": 1_800, "paper_exit_time": 1_900}
    assert rule_followed(early)["no_early_exit"] is False
    state = journal.state(today=DAY)
    assert state["running"][0]["entry_slip"] == pytest.approx(0.5)
    assert state["guard"]["trades_today"] == 1


def test_a_skip_is_logged_and_a_second_signal_is_its_own_row(tmp_path: Path) -> None:
    journal = AssistJournal(tmp_path)
    key = paper_trade_key(slot="4", day=DAY, fill_time=50)
    assert journal.skip({"key": key, "slot": "4", "day": DAY, "fill_time": 50, "reason": "afraid"})["kind"] == "skipped"
    with pytest.raises(ValueError, match="reason"):
        journal.skip({"key": key, "slot": "4", "day": DAY, "fill_time": 50, "reason": "bored"})
    with pytest.raises(ValueError, match="skipped"):
        journal.take({
            "key": key, "slot": "4", "day": DAY, "fill_time": 50, "fill_price": 10,
            "entry_time": 50, "lot_size": 65,
        })


def test_the_reminder_and_the_story_use_the_clock() -> None:
    assert reminder_due(15 * 60 + 10, 1, False) is True
    assert reminder_due(15 * 60 + 9, 1, False) is False
    assert reminder_due(15 * 60 + 10, 0, False) is False
    assert reminder_due(15 * 60 + 10, 1, True) is False
    assert reminder_due(15 * 60 + 30, 1, False) is False
    assert story_due(15 * 60 + 30) is True
    assert story_due(15 * 60 + 29) is False


def test_there_is_no_order_path() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("app/paper/assist.py", "app/routes/paper.py"):
        text = (root / name).read_text(encoding="utf-8")
        for banned in ("place_order", "modify_order", "cancel_order", "httpx", "requests."):
            assert banned not in text
