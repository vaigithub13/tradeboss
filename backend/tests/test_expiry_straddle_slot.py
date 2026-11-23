"""Slot 9 wires ExpiryStraddle to the one live feed. No sockets."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtest.expiry import load_default_calendar
from app.backtest.lots import load_default_lot_table, lot_size_for_contract
from app.paper.desk import SLOTS, PaperDesk
from app.paper.expiry_slot import ExpiryStraddleSlot
from app.paper.expiry_straddle import app_cost_fn
from app.paper.live import AlreadyRunning, PaperRunner
from app.routes.paper import router as paper_router
from app.upstox.instruments import NIFTY_INDEX_KEY, Instrument, InstrumentIndex
from tests.test_option_key_priority import service

IST = timezone(timedelta(hours=5, minutes=30))
CE = "NSE_FO|90001"
PE = "NSE_FO|90002"


def expiry_day() -> date:
    """The next weekly expiry on or after 23 Nov 2026, from the real calendar."""
    day = load_default_calendar().next_expiry(date(2026, 11, 23), "weekly").date
    assert load_default_calendar().is_expiry_day(day)
    return day


def plain_day() -> date:
    day = expiry_day() + timedelta(days=1)
    while load_default_calendar().is_expiry_day(day):
        day += timedelta(days=1)
    return day


def epoch(day: date, h: int, m: int, s: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp() * 1000)


def chain(day: date, strike: float = 25000.0) -> InstrumentIndex:
    return InstrumentIndex(
        [
            Instrument(CE, "NIFTY", "NIFTY", "option", "NSE_FO", "CE", expiry=day, strike=strike, underlying_key=NIFTY_INDEX_KEY),
            Instrument(PE, "NIFTY", "NIFTY", "option", "NSE_FO", "PE", expiry=day, strike=strike, underlying_key=NIFTY_INDEX_KEY),
            Instrument("NSE_FO|90003", "NIFTY", "NIFTY", "option", "NSE_FO", "CE", expiry=day, strike=strike + 50, underlying_key=NIFTY_INDEX_KEY),
            Instrument("NSE_FO|90004", "NIFTY", "NIFTY", "option", "NSE_FO", "PE", expiry=day, strike=strike + 50, underlying_key=NIFTY_INDEX_KEY),
        ]
    )


def slot_for(tmp_path, day: date) -> tuple[ExpiryStraddleSlot, list[set[str]]]:
    seen: list[set[str]] = []
    slot = ExpiryStraddleSlot(tmp_path / "slot9", instruments=lambda: chain(expiry_day()), on_wanted=lambda: seen.append(set(slot.wanted_keys())))
    slot.start(day)
    return slot, seen


def test_slots_one_to_seven_stay_the_desk() -> None:
    assert SLOTS == ("1", "2", "3", "4", "5", "6", "7")
    assert ExpiryStraddleSlot.SLOT == "9"


def test_a_non_expiry_day_records_no_trade_and_subscribes_nothing(tmp_path) -> None:
    day = plain_day()
    slot, seen = slot_for(tmp_path, day)
    units = lot_size_for_contract(load_default_lot_table(), "NIFTY", load_default_calendar().next_expiry(day, "weekly").date, "weekly")
    assert slot.book is not None
    assert slot.book.is_expiry is False and slot.book.status == "no_trade" and slot.book.units == units
    assert any(e.get("text") == "no trade today (not expiry)" for e in slot.book.events)
    slot.on_frame(epoch(day, 9, 20), spot=25010.0, quotes={CE: 100.0, PE: 90.0})
    assert slot.book.status == "no_trade" and not slot.book.legs and seen == []
    saved = json.loads((tmp_path / "slot9" / f"{day.isoformat()}.json").read_text())
    assert saved["status"] == "no_trade" and saved["day"] == day.isoformat()


def test_entry_subscribes_both_keys_on_one_feed_then_a_restart_keeps_the_book(tmp_path) -> None:
    day = expiry_day()
    slot, seen = slot_for(tmp_path, day)
    at = epoch(day, 9, 20, 30)
    slot.on_frame(at, spot=25010.0, quotes={})
    assert seen and seen[-1] == {CE, PE}
    assert slot.book is not None and slot.book.status == "waiting"
    slot.on_frame(at, spot=25010.0, quotes={CE: 120.0, PE: 80.0})
    assert slot.book.status == "open"
    assert [leg.key for leg in slot.book.legs] == [CE, PE]
    assert slot.book.legs[0].strike == 25000
    # One CE stop. The file is the book's own dict.
    slot.on_frame(epoch(day, 10, 0), spot=25010.0, quotes={CE: 200.0})
    assert slot.book.legs[0].reason == "stop" and slot.book.legs[1].open
    raw = json.loads((tmp_path / "slot9" / f"{day.isoformat()}.json").read_text())
    assert raw["status"] == "open" and raw["legs"][0]["reason"] == "stop"

    again = ExpiryStraddleSlot(tmp_path / "slot9", instruments=lambda: chain(day))
    assert again.resume(day) is True
    assert again.book is not None and again.book.legs[0].reason == "stop"
    again.on_frame(epoch(day, 9, 21), spot=25010.0, quotes={CE: 10.0})
    assert again.book.legs[1].open  # already in, so the window cannot open a second straddle
    # 15:15 with no PE quote yet leaves that leg; the next minute uses the quote that arrives.
    again.on_frame(epoch(day, 15, 15), spot=25010.0, quotes={})
    assert again.book.status == "open"
    again.on_frame(epoch(day, 15, 16), spot=25010.0, quotes={PE: 40.0})
    assert again.book.status == "done"
    done = json.loads((tmp_path / "slot9" / f"{day.isoformat()}.json").read_text())
    assert done["status"] == "done"
    view = again.status_body()["book"]
    assert view["spot_at_entry"] == 25010.0 and view["strike"] == 25000
    assert [(leg["kind"], leg["reason"]) for leg in view["legs"]] == [("CE", "stop"), ("PE", "time")]
    summary = again.book.summary(app_cost_fn(day.isoformat()))
    assert view["net"] == summary["net_rs"] and view["gross"] == summary["gross_rs"] and view["costs"] == summary["costs_rs"]
    week = again.weekly_summary(day)
    assert week["totals"]["trades"] == 1 and week["totals"]["net"] == summary["net_rs"]


def test_a_late_start_is_missed_and_a_second_start_is_refused(tmp_path) -> None:
    day = expiry_day()
    slot, _seen = slot_for(tmp_path, day)
    slot.on_frame(epoch(day, 9, 22), spot=25010.0, quotes={CE: 100.0, PE: 90.0})
    assert slot.book is not None and slot.book.status == "missed_entry" and not slot.book.legs
    try:
        slot.start(day)
        raised = False
    except AlreadyRunning:
        raised = True
    assert raised


def test_the_existing_connection_holds_both_keys_and_never_opens_a_socket(tmp_path) -> None:
    day = expiry_day()
    svc = service(tmp_path)
    held: list[list[str]] = []
    real = svc.connection.set_keys
    svc.connection.set_keys = lambda keys: (held.append(list(keys)), real(keys))  # type: ignore[method-assign]
    slot, _seen = slot_for(tmp_path, day)
    slot.on_frame(epoch(day, 9, 20), spot=25010.0, quotes={})
    svc.expiry_slot = slot
    svc.refresh_subscriptions()
    assert svc.connection.state == "off" and svc.connection.connections_opened == 0
    assert held and held[-1][0] == NIFTY_INDEX_KEY
    assert CE in held[-1] and PE in held[-1]
    assert CE in svc.subscription_keys() and PE in svc.subscription_keys()


def test_autostart_can_post_slot_nine(tmp_path) -> None:
    day = plain_day()
    slot = ExpiryStraddleSlot(tmp_path / "slot9", instruments=lambda: chain(expiry_day()))
    runners = {}
    for name in SLOTS:
        runners[name] = PaperRunner(tmp_path / name, lambda *_a, **_k: None)
    desk = PaperDesk(runners)
    app = FastAPI()
    app.include_router(paper_router)
    app.state.live = SimpleNamespace(
        enabled=True,
        paper=desk,
        now_ms=lambda: epoch(day, 9, 16),
        expiry_slot=slot,
        refresh_subscriptions=lambda: None,
    )
    client = TestClient(app)
    started = client.post("/api/paper/start", json={"slot": "9", "strategy": "expiry_straddle", "params": {}})
    assert started.status_code == 200
    assert started.json()["slot"] == "9" and started.json()["book_status"] == "no_trade"
    assert started.json()["book"]["status"] == "no_trade"
    assert started.json()["book"]["legs"] == []
    assert started.json()["book"]["net"] == 0 and started.json()["book"]["costs"] == 0
    week = client.get(f"/api/paper/week?slot=9&day={day.isoformat()}")
    assert week.status_code == 200
    assert week.json()["totals"]["net"] == 0 and week.json()["totals"]["trades"] == 0
    again = client.post("/api/paper/start", json={"slot": "9", "strategy": "expiry_straddle", "params": {}})
    assert again.status_code == 409 and "already" in again.json()["detail"].lower()
    listed = client.get("/api/paper/status").json()["slots"]
    assert [row["slot"] for row in listed] == [*SLOTS, "9"]
    refused = client.post("/api/paper/start", json={"slot": "9", "strategy": "log_xz", "params": {}})
    assert refused.status_code == 400
