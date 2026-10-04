"""Option candle history for the calibration (3b case 27/28): Upstox expired + active contracts, a store,
a recorded source, resumable fetching. Everything runs on fakes - no network, no token."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.backtest.expiry import load_default_calendar
from app.options.history import (
    ContractRef,
    OptionHistoryStore,
    RecordedSource,
    UpstoxHistorySource,
    capture_listed_day,
    fetch_expiry,
    ingest_recorded_bars,
    option_source_empty_report,
    plan_strikes,
    trading_days_before,
)
from app.upstox.client import UpstoxApiError, UpstoxClient
from app.upstox.token import DataToken
from tests.upstox_helpers import FAKE_TOKEN, mock_client

IST = timezone(timedelta(hours=5, minutes=30))
D = date.fromisoformat
CAL = load_default_calendar()


def rows_for(day: str, n: int = 3, base: float = 100.0) -> list[list[Any]]:
    """Upstox-shaped rows, NEWEST FIRST: [iso time, o, h, l, c, volume, oi]."""
    t0 = datetime.fromisoformat(f"{day}T09:15:00+05:30")
    out = [[(t0 + timedelta(minutes=i)).isoformat(), base + i, base + i + 1, base + i - 1, base + i + 0.5, 10 + i, 5000] for i in range(n)]
    return list(reversed(out))


def contract_json(strike: float, kind: str, expiry: str, key: str | None = None) -> dict[str, Any]:
    dd, mm, yy = expiry[8:], expiry[5:7], expiry[:4]
    return {"name": "NIFTY", "segment": "NSE_FO", "exchange": "NSE", "expiry": expiry,
            "instrument_key": key or f"NSE_FO|{int(strike)}{kind}|{dd}-{mm}-{yy}", "exchange_token": "1",
            "trading_symbol": f"NIFTY {int(strike)} {kind} X", "tick_size": 5, "lot_size": 25, "instrument_type": kind,
            "underlying_key": "NSE_INDEX|Nifty 50", "strike_price": strike, "weekly": True}


# ---------------------------------------------------------------- the client's new read-only calls
def make_client(handler) -> UpstoxClient:  # noqa: ANN001
    return UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(handler), sleep=lambda s: None)


def test_expired_calls_use_the_documented_urls_and_only_get() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        p = req.url.raw_path.decode()
        if p.startswith("/v2/expired-instruments/expiries"):
            return httpx.Response(200, json={"status": "success", "data": ["2024-10-10", "2024-10-03"]})
        if p.startswith("/v2/expired-instruments/option/contract"):
            return httpx.Response(200, json={"status": "success", "data": [contract_json(25800, "CE", "2024-10-03")]})
        return httpx.Response(200, json={"status": "success", "data": {"candles": rows_for("2024-10-03")}})

    c = make_client(handler)
    assert c.expired_expiries("NSE_INDEX|Nifty 50") == [D("2024-10-03"), D("2024-10-10")]
    assert len(c.expired_option_contracts("NSE_INDEX|Nifty 50", D("2024-10-03"))) == 1
    rows = c.expired_historical_candles("NSE_FO|58548|03-10-2024", D("2024-10-01"), D("2024-10-03"))
    assert len(rows) == 3
    assert {r.method for r in seen} == {"GET"}
    paths = [r.url.raw_path.decode() for r in seen]
    assert paths[0] == "/v2/expired-instruments/expiries?instrument_key=NSE_INDEX%7CNifty+50"
    assert paths[1] == "/v2/expired-instruments/option/contract?instrument_key=NSE_INDEX%7CNifty+50&expiry_date=2024-10-03"
    assert paths[2] == "/v2/expired-instruments/historical-candle/NSE_FO%7C58548%7C03-10-2024/1minute/2024-10-03/2024-10-01"
    assert all(r.headers["Authorization"] == f"Bearer {FAKE_TOKEN}" for r in seen)


def test_the_plus_plan_error_is_reported_with_its_code_and_without_the_token() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"status": "error", "errors": [{"errorCode": "UDAPI1149", "message": "Plus plan only"}]})

    c = make_client(handler)
    with pytest.raises(Exception) as e:  # noqa: PT011  (auth-class error for 403)
        c.expired_historical_candles("NSE_FO|1|03-10-2024", D("2024-10-03"), D("2024-10-03"))
    assert "UDAPI1149" in str(e.value) and FAKE_TOKEN not in str(e.value)
    assert UpstoxApiError  # imported for the 4xx path below

    def bad(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": "error", "errors": [{"errorCode": "UDAPI1149", "message": "Plus plan only"}]})

    with pytest.raises(UpstoxApiError) as e2:
        make_client(bad).expired_historical_candles("NSE_FO|1|03-10-2024", D("2024-10-03"), D("2024-10-03"))
    assert e2.value.code == "UDAPI1149"


# ---------------------------------------------------------------- sources
class FakeExpiredClient:
    """What UpstoxHistorySource needs from the client; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def expired_expiries(self, key: str) -> list[date]:
        self.calls.append(("expiries", key))
        return [D("2026-09-22"), D("2026-09-29")]

    def expired_option_contracts(self, key: str, expiry: date) -> list[dict[str, Any]]:
        self.calls.append(("contracts", expiry))
        return [contract_json(25000, "CE", expiry.isoformat()), contract_json(25000, "PE", expiry.isoformat())]

    def expired_historical_candles(self, key: str, from_date: date, to_date: date, interval: str = "1minute") -> list[list[Any]]:
        self.calls.append(("expired_candles", key, from_date, to_date))
        return rows_for(to_date.isoformat())

    def historical_candles(self, key: str, from_date: date, to_date: date, *, unit: str = "minutes", interval: int = 1):  # noqa: ANN201
        self.calls.append(("active_candles", key, from_date, to_date))
        return rows_for(to_date.isoformat(), base=200.0)


def test_past_expiries_use_the_expired_api_and_listed_ones_the_normal_one() -> None:
    from app.upstox.instruments import Instrument, InstrumentIndex

    listed = Instrument("NSE_FO|777", "NIFTY 25000 CE 06 OCT 26", "NIFTY", "option", "NSE_FO", "CE", expiry=D("2026-10-06"),
                        strike=25000.0, lot_size=65, underlying_key="NSE_INDEX|Nifty 50")
    fake = FakeExpiredClient()
    src = UpstoxHistorySource(fake, InstrumentIndex([listed]), today=D("2026-10-03"))  # type: ignore[arg-type]

    past = src.contracts(D("2026-09-29"))
    assert [(r.strike, r.kind) for r in past] == [(25000.0, "CE"), (25000.0, "PE")]
    src.candles(past[0], D("2026-09-29"), D("2026-09-23"), D("2026-09-29"))
    assert fake.calls[-1][0] == "expired_candles" and fake.calls[-1][1] == past[0].key

    now = src.contracts(D("2026-10-06"))
    assert [(r.key, r.strike, r.kind, r.lot_size) for r in now] == [("NSE_FO|777", 25000.0, "CE", 65)]
    src.candles(now[0], D("2026-10-06"), D("2026-10-01"), D("2026-10-02"))
    assert fake.calls[-1] == ("active_candles", "NSE_FO|777", D("2026-10-01"), D("2026-10-02"))
    assert src.expiries() == [D("2026-09-22"), D("2026-09-29")]


def test_long_ranges_are_cut_into_windows_of_a_month_or_less() -> None:
    fake = FakeExpiredClient()
    src = UpstoxHistorySource(fake, None, today=D("2026-10-03"))  # type: ignore[arg-type]
    ref = ContractRef(25000.0, "CE", "NSE_FO|1|29-09-2026", "x", 25)
    src.candles(ref, D("2026-09-29"), D("2026-07-01"), D("2026-09-29"))
    spans = [(c[2], c[3]) for c in fake.calls if c[0] == "expired_candles"]
    assert len(spans) >= 3 and all((b - a).days <= 30 for a, b in spans)
    assert spans[0][0] == D("2026-07-01") and spans[-1][1] == D("2026-09-29")


# ---------------------------------------------------------------- store
def bars(day: str, n: int = 3) -> list[dict[str, Any]]:
    from app.upstox.client import parse_candles

    return parse_candles(rows_for(day, n))


def test_the_store_round_trips_and_remembers_what_it_has(tmp_path: Path) -> None:
    store = OptionHistoryStore(tmp_path)
    ref = ContractRef(25000.0, "CE", "NSE_FO|1|29-09-2026", "NIFTY 25000 CE 29 SEP 26", 65)
    store.write(D("2026-09-29"), ref, bars("2026-09-29"), "upstox-expired")
    assert store.have(D("2026-09-29")) == {(25000.0, "CE")}
    got = store.read(D("2026-09-29"))
    assert [(b.strike, b.kind, b.open, b.volume, b.source) for b in got] == [
        (25000.0, "CE", 100.0, 10.0, "upstox-expired"), (25000.0, "CE", 101.0, 11.0, "upstox-expired"),
        (25000.0, "CE", 102.0, 12.0, "upstox-expired")]
    assert got[0].time < got[1].time  # ascending unix seconds
    store.write(D("2026-09-29"), ref, bars("2026-09-29"), "upstox-expired")  # again: no duplicates
    assert len(store.read(D("2026-09-29"))) == 3
    assert store.expiries() == [D("2026-09-29")]


def test_an_empty_answer_is_a_hole_and_an_unlisted_strike_stays_skipped(tmp_path: Path) -> None:
    store = OptionHistoryStore(tmp_path)
    ref = ContractRef(25000.0, "PE", "NSE_FO|2|29-09-2026", "x", 65)
    store.write(D("2026-09-29"), ref, [], "upstox-expired")  # nothing to store, and not a hole by itself
    store.note_source_empty(D("2026-09-29"), 25000.0, "PE", D("2026-09-23"), D("2026-09-29"), D("2026-10-04"))
    store.mark(D("2026-09-29"), 99999.0, "CE", "not_listed")
    assert store.have(D("2026-09-29"), today=D("2026-10-04")) == {(25000.0, "PE"), (99999.0, "CE")}
    assert store.have(D("2026-09-29"), today=D("2026-10-11")) == {(99999.0, "CE")}
    assert store.read(D("2026-09-29")) == []


# ---------------------------------------------------------------- planning
def test_strikes_cover_atm_plus_minus_one_over_every_days_range() -> None:
    ranges = {D("2026-09-28"): (25010.0, 25190.0), D("2026-09-29"): (25240.0, 25330.0)}
    strikes = plan_strikes(ranges, step=50, window=1)
    # day 1: ATM(low)=25000 .. ATM(high)=25200 -> 24950..25250 ; day 2: ATM 25250..25350 -> 25200..25400
    assert strikes == [float(k) for k in range(24950, 25401, 50)]


def test_the_days_of_a_contracts_last_week_are_trading_days_only() -> None:
    days = trading_days_before(D("2026-10-06"), 6, CAL.is_trading_day)
    assert days == [D("2026-09-28"), D("2026-09-29"), D("2026-09-30"), D("2026-10-01"), D("2026-10-05"), D("2026-10-06")]  # 2-Oct holiday


# ---------------------------------------------------------------- fetching
def test_fetch_expiry_fetches_each_wanted_contract_once_and_resumes(tmp_path: Path) -> None:
    fake = FakeExpiredClient()
    src = UpstoxHistorySource(fake, None, today=D("2026-10-03"))  # type: ignore[arg-type]
    store = OptionHistoryStore(tmp_path)
    days = [D("2026-09-28"), D("2026-09-29")]
    stats = fetch_expiry(src, store, D("2026-09-29"), [25000.0, 25050.0], days)
    assert (stats.fetched, stats.skipped, stats.not_listed) == (2, 0, 2)  # 25050 does not exist in the fake list
    assert {(b.strike, b.kind) for b in store.read(D("2026-09-29"))} == {(25000.0, "CE"), (25000.0, "PE")}
    n_calls = len(fake.calls)
    again = fetch_expiry(src, store, D("2026-09-29"), [25000.0, 25050.0], days)
    assert (again.fetched, again.skipped) == (0, 4) and len(fake.calls) == n_calls  # nothing asked twice, not even the list


def test_an_empty_option_fetch_is_a_hole_retried_after_a_week(tmp_path: Path) -> None:
    class Empty(FakeExpiredClient):
        def __init__(self) -> None:
            super().__init__()
            self.empty = True

        def expired_historical_candles(self, key, from_date, to_date, interval="1minute"):  # noqa: ANN001, ANN201
            self.calls.append(("expired_candles", key, from_date, to_date))
            if self.empty:
                return []
            return rows_for(to_date.isoformat())

    fake = Empty()
    src = UpstoxHistorySource(fake, None, today=D("2026-10-04"))  # type: ignore[arg-type]
    store = OptionHistoryStore(tmp_path)
    exp = D("2024-12-26")
    days = [D("2024-12-20"), D("2024-12-23"), D("2024-12-26")]
    when = D("2026-10-04")
    stats = fetch_expiry(src, store, exp, [25000.0], days, today=when)
    assert (stats.fetched, stats.empty, stats.skipped) == (0, 2, 0)
    assert store.read(exp) == []
    assert not (tmp_path / "NIFTY_2024-12-26.parquet").exists()

    def candle_calls() -> list[tuple[Any, ...]]:
        return [c for c in fake.calls if c[0] == "expired_candles"]

    assert len(candle_calls()) == 2
    again = fetch_expiry(src, store, exp, [25000.0], days, today=when)
    assert (again.fetched, again.empty, again.skipped) == (0, 0, 2)
    assert len(candle_calls()) == 2  # tried today: wait a week
    rows = option_source_empty_report(tmp_path)
    assert {(r["instrument_key"], r["from"], r["to"], r["tried"], r["name"]) for r in rows} == {
        ("NIFTY|25000|CE|2024-12-26", "2024-12-20", "2024-12-26", "2026-10-04", "NIFTY 25000 CE"),
        ("NIFTY|25000|PE|2024-12-26", "2024-12-20", "2024-12-26", "2026-10-04", "NIFTY 25000 PE"),
    }
    fake.empty = False
    got = fetch_expiry(src, store, exp, [25000.0], days, today=when + timedelta(days=7))
    assert got.fetched == 2 and got.empty == 0 and len(candle_calls()) == 4
    assert {(b.strike, b.kind) for b in store.read(exp)} == {(25000.0, "CE"), (25000.0, "PE")}
    assert option_source_empty_report(tmp_path) == []
    assert store.have(exp, today=when + timedelta(days=7)) == {(25000.0, "CE"), (25000.0, "PE")}


def test_the_option_source_empty_report_reclassifies_done_contracts_with_no_candles(tmp_path: Path) -> None:
    store = OptionHistoryStore(tmp_path)
    exp = D("2024-12-26")
    ref = ContractRef(25000.0, "CE", "NSE_FO|1|26-12-2024", "NIFTY 25000 CE", 25)
    store.write(exp, ref, bars("2024-12-26"), "upstox-expired")
    store.mark(exp, 23500.0, "CE", "done")
    store.mark(exp, 23500.0, "PE", "done")
    store.mark(exp, 99999.0, "PE", "not_listed")
    stamp = datetime(2026, 10, 3, 12, 0, tzinfo=IST).timestamp()
    os.utime(tmp_path / "NIFTY_2024-12-26.done.json", (stamp, stamp))
    week = trading_days_before(exp, 7, CAL.is_trading_day)
    rows = option_source_empty_report(tmp_path)
    assert [(r["instrument_key"], r["from"], r["to"], r["tried"]) for r in rows] == [
        (f"NIFTY|23500|CE|{exp.isoformat()}", week[0].isoformat(), week[-1].isoformat(), "2026-10-03"),
        (f"NIFTY|23500|PE|{exp.isoformat()}", week[0].isoformat(), week[-1].isoformat(), "2026-10-03"),
    ]
    assert store.have(exp, today=D("2026-10-04")) == {(25000.0, "CE"), (23500.0, "CE"), (23500.0, "PE"), (99999.0, "PE")}
    assert store.have(exp, today=D("2026-10-10")) == {(25000.0, "CE"), (99999.0, "PE")}
    assert option_source_empty_report(tmp_path) == rows  # a second look does not move the tried date


def test_an_empty_listed_day_is_a_hole_for_that_day(tmp_path: Path) -> None:
    class Empty(FakeExpiredClient):
        def expired_historical_candles(self, key, from_date, to_date, interval="1minute"):  # noqa: ANN001, ANN201
            self.calls.append(("expired_candles", key, from_date, to_date))
            return []

    src = UpstoxHistorySource(Empty(), None, today=D("2026-10-04"))  # type: ignore[arg-type]
    store = OptionHistoryStore(tmp_path)
    stats = capture_listed_day(src, store, D("2026-09-29"), D("2026-09-29"), [25000.0], today=D("2026-10-04"))
    assert (stats.fetched, stats.empty) == (0, 2)
    assert store.have(D("2026-09-29"), today=D("2026-10-04")) == {(25000.0, "CE"), (25000.0, "PE")}
    rows = option_source_empty_report(tmp_path)
    assert {r["from"] for r in rows} == {"2026-09-29"} and {r["to"] for r in rows} == {"2026-09-29"}


def test_a_failing_contract_is_reported_and_retried_later_not_remembered(tmp_path: Path) -> None:
    class Flaky(FakeExpiredClient):
        def expired_historical_candles(self, key, from_date, to_date, interval="1minute"):  # noqa: ANN001, ANN201
            if "CE" in key:
                raise UpstoxApiError("boom", status=400, code="X")
            return super().expired_historical_candles(key, from_date, to_date, interval)

    src = UpstoxHistorySource(Flaky(), None, today=D("2026-10-03"))  # type: ignore[arg-type]
    store = OptionHistoryStore(tmp_path)
    stats = fetch_expiry(src, store, D("2026-09-29"), [25000.0], [D("2026-09-29")])
    assert (stats.fetched, stats.failed) == (1, 1)
    assert store.have(D("2026-09-29")) == {(25000.0, "PE")}  # the CE will be tried again next run


# ---------------------------------------------------------------- recorded source
def test_the_recorded_source_reads_what_was_stored_and_prefers_upstox_history_for_the_same_minute(tmp_path: Path) -> None:
    store = OptionHistoryStore(tmp_path)
    ref = ContractRef(25000.0, "CE", "NSE_FO|1|29-09-2026", "x", 65)
    store.write(D("2026-09-29"), ref, bars("2026-09-29", 2), "live-recorded")
    rec = RecordedSource(store)
    assert rec.expiries() == [D("2026-09-29")]
    assert [(r.strike, r.kind) for r in rec.contracts(D("2026-09-29"))] == [(25000.0, "CE")]
    assert len(rec.candles(rec.contracts(D("2026-09-29"))[0], D("2026-09-29"), D("2026-09-29"), D("2026-09-29"))) == 2

    from dataclasses import replace

    from app.options.history import merge_bars

    first = store.read(D("2026-09-29"))[0]
    upstox = replace(first, open=999.0, source="upstox-expired")
    merged = merge_bars([[upstox], [first]])  # earlier list = preferred source
    assert [(b.time, b.open, b.source) for b in merged if b.time == first.time] == [(first.time, 999.0, "upstox-expired")]
    assert len(merge_bars([store.read(D("2026-09-29")), [upstox]])) == 2  # other minutes of the recorded list stay


def test_capture_listed_day_merges_a_new_session_into_the_recorded_store(tmp_path: Path) -> None:
    fake = FakeExpiredClient()
    src = UpstoxHistorySource(fake, None, today=D("2026-10-03"))  # type: ignore[arg-type]
    store = OptionHistoryStore(tmp_path)
    # first Monday
    stats = capture_listed_day(src, store, D("2026-09-28"), D("2026-09-29"), [25000.0], label="live-recorded")
    assert stats.fetched == 2 and stats.not_listed == 0
    n = len(store.read(D("2026-09-29")))
    # a later day appends (fake returns the to_date's bars); resume does not drop what we had
    capture_listed_day(src, store, D("2026-09-29"), D("2026-09-29"), [25000.0], label="live-recorded")
    assert len(store.read(D("2026-09-29"))) >= n
    ingest_recorded_bars(
        store, D("2026-09-29"), ContractRef(25100.0, "CE", "NSE_FO|9", "x", 65), bars("2026-09-29", 1),
    )
    assert (25100.0, "CE") in {(b.strike, b.kind) for b in store.read(D("2026-09-29"))}
