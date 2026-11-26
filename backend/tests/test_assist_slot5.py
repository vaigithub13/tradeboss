"""Slot 5 and slot 7 assist: the trail, the facts, and a practice read. No broker."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.exits.rules import PREMIUM_TRAIL_10, trailed_stop
from app.paper.assist import AssistJournal, build_ticket, default_settings, guard_banner, normalise_settings, rule_followed
from app.paper.assist_book import (
    after_trade,
    format_dd_why,
    pending_trail,
    price_action,
    score_practice,
    tag_accuracy,
    user_trail_stop,
)
from app.paper.real import paper_trade_key

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-11-27"


def _epoch(day: int, hour: int, minute: int) -> int:
    return int(datetime(2026, 11, day, hour, minute, tzinfo=IST).timestamp())


def test_slot5_ticket_has_a_ten_percent_stop_and_no_bracket() -> None:
    ticket = build_ticket(
        slot="5",
        day=DAY,
        signal={"decided_at_ms": _epoch(27, 10, 30) * 1000, "time": _epoch(27, 10, 30), "side": "BUY",
                "symbol": "NIFTY 25000 CE", "index_price": 24120},
        bars=[{"time": _epoch(27, 10, 30), "open": 24100, "high": 24140, "low": 24080, "close": 24120}],
        quote={"bid": 200.0, "ask": 200.03},
        ltp=200.1,
        lot_size=65,
        lots=1,
        stop_frac=0.10,
        target_frac=None,
        trailing=True,
        market="NIFTY",
        why="DD long confirmations: close above Supertrend.",
    )
    assert ticket["suggested_limit"] == 200.05
    assert ticket["stop"] == round(200.05 * 0.90, 2)
    assert ticket["target"] is None
    assert ticket["exit_note"] == "exit by trailing stop"
    assert ticket["market"] == "NIFTY"
    assert "40%" not in ticket["why"]
    assert ticket["target_frac"] is None
    blob = json.dumps(ticket)
    assert "280.07" not in blob


def test_the_trail_popup_uses_the_user_fill() -> None:
    paper = trailed_stop(100, PREMIUM_TRAIL_10, 116)
    user = user_trail_stop(110, 116)
    assert paper == 100.0
    assert user == 99.0
    record = {
        "key": "5:2026-11-27:1", "kind": "taken", "slot": "5", "contract": "NIFTY 25000 CE",
        "fill_price": 110, "initial_stop": 99.0, "exit_time": None,
    }
    quiet = pending_trail(record, paper_stop=90, best=100, moved_at=1)
    assert quiet is None
    notice = pending_trail(record, paper_stop=100, best=116, moved_at=1_700)
    assert notice is not None
    assert notice["real_stop"] == 99.0
    assert notice["text"] == "Move SL to ₹99.00"
    assert notice["market"] == "NIFTY"
    sensex = pending_trail({**record, "slot": "7", "contract": "SENSEX 80000 CE"}, paper_stop=100, best=116, moved_at=1)
    assert sensex is not None and sensex["market"] == "SENSEX"


def test_a_late_trail_update_breaks_the_rule_and_a_target_is_not_required() -> None:
    base = {
        "trailing": True, "fill_time": 1_000, "entry_time": 1_030, "sl_set": True, "tp_set": False,
        "exit_time": 2_000, "paper_exit_time": 1_900,
        "trail_seen": [{"paper_stop": 100.0, "moved_at": 1_200}],
        "trails": [{"paper_stop": 100.0, "real_stop": 99.0, "moved_at": 1_200, "updated_at": 1_260}],
    }
    assert rule_followed(base)["followed"] is True
    late = {**base, "trails": [{**base["trails"][0], "updated_at": 1_200 + 121}]}
    assert rule_followed(late)["followed"] is False
    missed = {**base, "trails": []}
    assert rule_followed(missed)["trails_on_time"] is False


def test_price_action_facts_on_a_fixture() -> None:
    bars = [{"time": _epoch(26, 15, 15), "open": 24000, "high": 24200, "low": 23900, "close": 24100, "volume": 1}]
    for i in range(40):
        hour, minute = divmod(9 * 60 + 15 + 3 * i, 60)
        high, low, close = 24150.0, 24100.0, 24120.0
        if i == 8:
            high, low, close = 24220, 24100, 24180
        if i == 14:
            high, low, close = 24160, 23900, 24020
        if i == 22:
            high, low, close = 24340, 24120, 24300
        if i == 28:
            high, low, close = 24150, 24000, 24110
        bars.append({
            "time": _epoch(27, hour, minute), "open": close - 10, "high": high, "low": low, "close": close, "volume": 1,
        })
    facts = price_action(bars, signal_index=len(bars) - 1)
    assert facts["structure"] == "HH/HL"
    assert facts["pdh"] == 24200
    assert facts["pdl"] == 23900
    assert facts["cpr"].startswith("BC ")
    assert "CPR" in facts["text"]
    assert any(kind in facts["nearest_100"] for kind in ("break", "retest", "mid-range"))
    assert "body" in facts["candle"]
    assert "PDH" in facts["text"] and "First hour" in facts["text"]
    last = bars[-1]
    prev = bars[-2]
    engulfed = {
        **last,
        "open": float(prev["close"]) + 5,
        "close": float(prev["open"]) - 5,
        "high": float(prev["high"]) + 5,
        "low": float(prev["low"]) - 5,
    }
    anatomy = price_action([*bars[:-1], engulfed], signal_index=len(bars) - 1)
    assert "engulfing" in anatomy["candle"]
    review = after_trade(bars[-8:], entry_time=bars[-8]["time"], exit_time=bars[-1]["time"])
    assert "Max favourable" in review["text"]
    assert "premium trail is not an index price" in review["text"]


def test_practice_scores_the_next_six_and_keeps_a_tag_tally() -> None:
    upcoming = [{"open": 100, "high": 106, "low": 99, "close": 101} for _ in range(5)]
    upcoming.append({"open": 101, "high": 112, "low": 100, "close": 110})
    up = score_practice(100, upcoming, direction="up", level=105)
    assert up["direction_hit"] is True and up["level_hit"] is True
    down = score_practice(100, upcoming, direction="down")
    assert down["direction_hit"] is False and down["level_hit"] is None
    short = score_practice(100, upcoming[:3], direction="up")
    assert short["ready"] is False
    tally = tag_accuracy([
        {"tag": "trend pullback", "direction_hit": True},
        {"tag": "trend pullback", "direction_hit": False},
        {"tag": "level break", "direction_hit": True},
    ])
    by_tag = {row["tag"]: row for row in tally}
    assert by_tag["trend pullback"]["accuracy"] == 0.5
    assert by_tag["level break"]["correct"] == 1
    assert by_tag["range fade"]["reads"] == 0


def test_why_lists_each_confirmation() -> None:
    text = format_dd_why({
        "close": 24120, "supertrend": 24080, "supertrend_up": True, "rsi": 74.2, "rsi_long": 70,
        "bollinger_upper": 24100, "r1": 24050, "williams_r": -18.0, "wr_long": -30, "use_wr": True,
    })
    for piece in ("Supertrend", "RSI 74.2", "Bollinger", "R1", "Williams %R"):
        assert piece in text
    assert "no target" in text


def test_the_guard_adds_slot_5_and_slot_7_together() -> None:
    settings = default_settings()
    assert settings["slots"] == ["5", "7"]
    assert settings["max_trades"] == 3
    assert settings["day_loss"] == 4000
    assert settings["account_pause"] == 12000
    records = [
        {"kind": "taken", "slot": "5", "day": DAY, "net": -2000, "exit_time": 1, "contract": "NIFTY 25000 CE"},
        {"kind": "taken", "slot": "7", "day": DAY, "net": -2000, "exit_time": 2, "contract": "SENSEX 80000 CE"},
        {"kind": "skipped", "slot": "5", "day": DAY},
    ]
    guard = guard_banner(records, today=DAY, settings=settings)
    assert guard["trades_today"] == 2
    assert guard["pnl_today"] == -4000.0
    assert guard["stop_for_today"] is True
    wider = {**settings, "day_loss": 5000, "max_trades": 2, "account_pause": 3000, "start_balance": 100000}
    moved = guard_banner(records, today=DAY, settings=wider)
    assert moved["stop_for_today"] is False
    assert moved["at_trade_cap"] is True
    assert moved["pause"] is True


def test_a_saved_slot_4_setting_watches_5_and_7(tmp_path) -> None:
    assert default_settings()["slots"] == ["5", "7"]
    assert default_settings()["day_loss"] == 4000
    assert default_settings()["account_pause"] == 12000
    journal = AssistJournal(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"slot": "4", "enabled": True}), encoding="utf-8")
    loaded = journal.load_settings()
    assert loaded["slots"] == ["5", "7"]
    assert "4" not in loaded["slots"]
    assert normalise_settings({"slots": ["4"]})["slots"] == ["5", "7"]


def test_taking_a_trail_trade_stores_the_user_stop_and_the_update(tmp_path) -> None:
    journal = AssistJournal(tmp_path)
    key = paper_trade_key(slot="5", day=DAY, fill_time=1_000)
    saved = journal.take({
        "key": key, "slot": "5", "day": DAY, "fill_time": 1_000, "fill_price": 110,
        "entry_time": 1_020, "lot_size": 65, "sl_set": True, "tp_set": False,
        "trailing": True, "stop_frac": 0.10, "contract": "NIFTY 25000 CE", "market": "NIFTY",
    })
    assert saved["stop"] == 99.0
    assert saved["target"] is None
    assert saved["initial_stop"] == 99.0
    journal.note_trail_seen(key, 100.0, 1_200)
    updated = journal.log_trail({
        "key": key, "paper_stop": 100.0, "real_stop": 99.0, "moved_at": 1_200, "updated_at": 1_250,
    })
    assert updated["stop"] == 99.0
    assert updated["trails"][0]["updated_at"] == 1_250
    closed = journal.close({
        "key": key, "exit_price": 120, "exit_time": 2_000,
        "chart": {"candles": [
            {"time": 1_000, "open": 100, "high": 110, "low": 99, "close": 105},
            {"time": 2_000, "open": 105, "high": 112, "low": 104, "close": 108},
        ]},
    })
    assert "Max favourable" in closed["after"]["text"]


def test_practice_hides_the_next_six_until_the_read_is_saved(tmp_path) -> None:
    journal = AssistJournal(tmp_path)
    bars = [
        {"time": 1_000 + i * 60, "open": 100, "high": 100 + i, "low": 99, "close": 100 + (10 if i >= 12 else 0)}
        for i in range(18)
    ]
    opened = journal.open_practice(bars)
    assert "hidden" not in opened
    assert len(opened["shown"]) == 12
    revealed = None
    try:
        revealed = journal.reveal_practice()
    except ValueError as exc:
        assert "save the read" in str(exc)
    assert revealed is None
    journal.save_practice_read({
        "id": opened["id"], "direction": "up", "reason": "higher low", "tag": "trend pullback", "level": 105,
    })
    revealed = journal.reveal_practice()
    assert revealed["score"]["direction_hit"] is True
    assert revealed["accuracy"][0]["tag"] == "trend pullback"
    assert revealed["accuracy"][0]["correct"] == 1
