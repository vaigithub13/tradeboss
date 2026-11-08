"""Options chain view: maths, client contracts call, routes. No test opens a socket."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.options import chain_history, chain_view
from app.routes import options as options_route
from app.upstox.client import UpstoxApiError, UpstoxClient
from app.upstox.token import DataToken
from tests.upstox_helpers import FAKE_TOKEN, mock_client

IST = timezone(timedelta(hours=5, minutes=30))
EXPIRY = "2026-11-10"
SPOT = 25023.0


def ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def leg(
    *,
    oi: float = 0.0,
    prev_oi: float = 0.0,
    volume: float = 0.0,
    ltp: float = 0.0,
    close: float = 0.0,
    bid: float = 0.0,
    ask: float = 0.0,
    iv: float = 12.0,
    delta: float = 0.5,
) -> dict[str, Any]:
    return {
        "instrument_key": "NSE_FO|1",
        "market_data": {
            "ltp": ltp,
            "close_price": close,
            "volume": volume,
            "oi": oi,
            "prev_oi": prev_oi,
            "bid_price": bid,
            "bid_qty": 65,
            "ask_price": ask,
            "ask_qty": 65,
        },
        "option_greeks": {
            "iv": iv,
            "delta": delta,
            "gamma": 0.001,
            "theta": -10.0,
            "vega": 5.0,
            "pop": 50.0,
        },
    }


def row(strike: float, call: dict, put: dict, spot: float = SPOT) -> dict[str, Any]:
    return {
        "expiry": EXPIRY,
        "strike_price": strike,
        "underlying_spot_price": spot,
        "pcr": 9.99,  # must be ignored for summary PCR
        "call_options": call,
        "put_options": put,
    }


def full_chain(spot: float = SPOT) -> list[dict[str, Any]]:
    """Strikes 24800..25200 step 50, ATM 25000."""
    rows = []
    for strike in range(24800, 25250, 50):
        call_oi = 100_000 + (strike - 24800) * 10
        put_oi = 200_000 - (strike - 24800) * 5
        rows.append(
            row(
                float(strike),
                leg(oi=call_oi, prev_oi=call_oi - 1_000, volume=500, ltp=100, close=95, bid=99, ask=101),
                leg(oi=put_oi, prev_oi=put_oi - 2_000, volume=400, ltp=80, close=85, bid=79, ask=81),
                spot=spot,
            )
        )
    return rows


# ------------------------------------------------------------------ maths
def test_atm_is_nearest_strike_to_spot() -> None:
    assert chain_view.atm_strike(25023.0, [24950.0, 25000.0, 25050.0]) == 25000.0
    assert chain_view.atm_strike(25026.0, [24950.0, 25000.0, 25050.0]) == 25050.0
    # tie → lower strike
    assert chain_view.atm_strike(25025.0, [25000.0, 25050.0]) == 25000.0


def test_totals_and_pcr_use_full_chain_not_upstox_pcr() -> None:
    out = chain_view.build_chain_view(full_chain(), now=ts(2026, 11, 7, 16, 0), expiry=EXPIRY, window=2)
    assert out["available"] is True
    assert out["atm"] == 25000.0
    assert out["spot"] == SPOT
    # full chain totals (9 strikes in fixture)
    assert out["total_call_oi"] == sum(100_000 + (s - 24800) * 10 for s in range(24800, 25250, 50))
    assert out["total_put_oi"] == sum(200_000 - (s - 24800) * 5 for s in range(24800, 25250, 50))
    assert out["pcr"] == pytest.approx(out["total_put_oi"] / out["total_call_oi"])
    assert out["pcr"] != 9.99
    # window ±2 around ATM → 5 rows
    assert len(out["rows"]) == 5
    assert [r["strike"] for r in out["rows"]] == [24900.0, 24950.0, 25000.0, 25050.0, 25100.0]


def test_resistance_and_support_top3_within_window() -> None:
    # Hand-built: call OI highest above spot at 25100, 25200, 25050; put OI below at 24900, 24800, 25000
    rows = [
        row(24800, leg(oi=10), leg(oi=800)),
        row(24900, leg(oi=20), leg(oi=900)),
        row(25000, leg(oi=30), leg(oi=700), spot=25010),
        row(25050, leg(oi=500), leg(oi=40), spot=25010),
        row(25100, leg(oi=900), leg(oi=30), spot=25010),
        row(25200, leg(oi=800), leg(oi=20), spot=25010),
    ]
    out = chain_view.build_chain_view(rows, now=ts(2026, 11, 7, 16, 0), expiry=EXPIRY, window=15)
    assert [r["strike"] for r in out["resistance"]] == [25100.0, 25200.0, 25050.0]
    assert [r["tag"] for r in out["resistance"]] == ["R1", "R2", "R3"]
    assert [r["strike"] for r in out["support"]] == [24900.0, 24800.0, 25000.0]
    assert [r["tag"] for r in out["support"]] == ["S1", "S2", "S3"]
    visible = {r["strike"] for r in out["rows"]}
    assert all(r["strike"] in visible for r in out["resistance"] + out["support"])


def test_sr_ignores_oi_outside_display_window_major_walls_use_full_chain() -> None:
    """Far strikes with huge OI must not become R1/S1; they only appear as major walls."""
    rows = []
    # Far put wall well below ATM window
    rows.append(row(24000, leg(oi=1), leg(oi=9_000_000), spot=25010))
    # Far call wall well above ATM window
    rows.append(row(26000, leg(oi=8_000_000), leg(oi=1), spot=25010))
    # Near strikes (± a few of ATM 25000) — modest OI that should win R/S
    for strike, call_oi, put_oi in (
        (24900, 10, 900),
        (24950, 20, 800),
        (25000, 30, 700),
        (25050, 500, 40),
        (25100, 900, 30),
        (25150, 800, 20),
    ):
        rows.append(row(float(strike), leg(oi=call_oi), leg(oi=put_oi), spot=25010))
    out = chain_view.build_chain_view(rows, now=ts(2026, 11, 7, 16, 0), expiry=EXPIRY, window=2)
    visible = {r["strike"] for r in out["rows"]}
    assert visible == {24900.0, 24950.0, 25000.0, 25050.0, 25100.0}
    assert 24000.0 not in visible and 26000.0 not in visible
    assert [r["strike"] for r in out["resistance"]] == [25100.0, 25050.0]
    assert [r["strike"] for r in out["support"]] == [24900.0, 24950.0, 25000.0]
    assert all(r["strike"] in visible for r in out["resistance"] + out["support"])
    assert out["major_walls"]["call"] == {"strike": 26000.0, "oi": 8_000_000.0}
    assert out["major_walls"]["put"] == {"strike": 24000.0, "oi": 9_000_000.0}
    # PCR still from the full chain (includes the far walls)
    assert out["total_call_oi"] == sum(r["call"]["oi"] for r in out["all_strikes"])
    assert out["total_put_oi"] == sum(r["put"]["oi"] for r in out["all_strikes"])


def test_leg_derived_fields() -> None:
    out = chain_view.build_chain_view(
        [row(25000, leg(oi=110, prev_oi=100, ltp=105, close=100, bid=104, ask=106), leg(oi=50, prev_oi=60))],
        now=ts(2026, 11, 7, 15, 30),
        expiry=EXPIRY,
    )
    call = out["rows"][0]["call"]
    assert call["oi_chg"] == 10.0
    assert call["oi_chg_pct"] == pytest.approx(10.0)
    assert call["price_chg"] == 5.0
    assert call["spread_pct"] == pytest.approx((106 - 104) / 105 * 100)


def test_empty_chain_is_unavailable() -> None:
    out = chain_view.build_chain_view([], now=ts(2026, 11, 7, 16, 0), expiry=EXPIRY)
    assert out["available"] is False
    assert out["reason"]


def test_as_of_ist_is_offset_aware() -> None:
    now = ts(2026, 11, 7, 15, 30)
    out = chain_view.build_chain_view(full_chain(), now=now, expiry=EXPIRY)
    assert out["as_of"] == now
    assert out["as_of_ist"] == "2026-11-07T15:30:00+05:30"


def test_market_status() -> None:
    # Friday during session
    assert chain_view.market_status(ts(2026, 11, 6, 10, 0), is_session=lambda d: d.weekday() < 5) == "LIVE"
    # Friday after close
    assert chain_view.market_status(ts(2026, 11, 6, 16, 0), is_session=lambda d: d.weekday() < 5) == "MARKET_CLOSED"
    # Saturday
    assert chain_view.market_status(ts(2026, 11, 7, 11, 0), is_session=lambda d: d.weekday() < 5) == "MARKET_CLOSED"


# ------------------------------------------------------------------ history
def test_append_and_load_latest_snapshot(tmp_path: Path) -> None:
    view = chain_view.build_chain_view(full_chain(), now=ts(2026, 11, 6, 15, 30), expiry=EXPIRY)
    path = chain_history.append_snapshot(tmp_path, chain_history.snapshot_row(view))
    assert path.name == "2026-11-06.jsonl"
    latest = chain_history.latest_snapshot(tmp_path, expiry=EXPIRY)
    assert latest is not None
    assert latest["expiry"] == EXPIRY
    assert latest["spot"] == SPOT
    assert latest["as_of_ist"].endswith("+05:30")
    assert len(latest["strikes"]) == len(full_chain())


def test_latest_prefers_matching_expiry(tmp_path: Path) -> None:
    a = chain_view.build_chain_view(full_chain(), now=ts(2026, 11, 6, 15, 25), expiry="2026-11-10")
    b = chain_view.build_chain_view(full_chain(), now=ts(2026, 11, 6, 15, 30), expiry="2026-11-17")
    chain_history.append_snapshot(tmp_path, chain_history.snapshot_row(a))
    chain_history.append_snapshot(tmp_path, chain_history.snapshot_row(b))
    got = chain_history.latest_snapshot(tmp_path, expiry="2026-11-10")
    assert got is not None and got["expiry"] == "2026-11-10"


# ------------------------------------------------------------------ client
def test_client_option_contracts_unique_sorted_expiries() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v2/option/contract"
        assert req.url.params["instrument_key"] == "NSE_INDEX|Nifty 50"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"expiry": "2026-11-17"},
                    {"expiry": "2026-11-10"},
                    {"expiry": "2026-11-10"},
                    {"expiry": "2026-11-24"},
                ],
            },
        )

    client = UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(handler), max_attempts=1)
    assert client.option_contracts("NSE_INDEX|Nifty 50") == [
        date(2026, 11, 10),
        date(2026, 11, 17),
        date(2026, 11, 24),
    ]


def test_client_option_contracts_rejects_strange_shape() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"nope": True}})

    client = UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(handler), max_attempts=1)
    with pytest.raises(UpstoxApiError):
        client.option_contracts("NSE_INDEX|Nifty 50")


# ------------------------------------------------------------------ routes
class FakeOptionsClient:
    def __init__(self, rows: list[dict] | None = None, expiries: list[date] | None = None) -> None:
        self.rows = rows if rows is not None else full_chain()
        self.expiries = expiries or [date(2026, 11, 10), date(2026, 11, 17)]
        self.chain_calls: list[tuple[str, str]] = []

    def option_chain(self, instrument_key: str, expiry: str) -> list[dict[str, Any]]:
        self.chain_calls.append((instrument_key, expiry))
        return self.rows

    def option_contracts(self, instrument_key: str) -> list[date]:
        return list(self.expiries)


@pytest.fixture
def options_client():
    fake = FakeOptionsClient()
    app.dependency_overrides[options_route.get_options_client] = lambda: fake
    app.dependency_overrides[options_route.get_options_clock] = lambda: (lambda: float(ts(2026, 11, 8, 12, 0)))
    app.dependency_overrides[options_route.get_options_dir] = lambda: Path("/tmp/tradeboss-options-test-unused")
    yield fake
    app.dependency_overrides.pop(options_route.get_options_client, None)
    app.dependency_overrides.pop(options_route.get_options_clock, None)
    app.dependency_overrides.pop(options_route.get_options_dir, None)
    options_route._CACHE.clear()


def test_route_chain_off_hours_fetches_live_when_no_snapshot(options_client, tmp_path: Path) -> None:
    app.dependency_overrides[options_route.get_options_dir] = lambda: tmp_path
    with TestClient(app) as c:
        res = c.get("/api/options/chain")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    assert body["status"] == "MARKET_CLOSED"
    assert body["atm"] == 25000.0
    assert body["as_of_ist"].endswith("+05:30")
    assert options_client.chain_calls  # live fetch used as fallback


def test_route_chain_uses_snapshot_when_present(options_client, tmp_path: Path) -> None:
    view = chain_view.build_chain_view(full_chain(), now=ts(2026, 11, 6, 15, 30), expiry=EXPIRY)
    view["status"] = "MARKET_CLOSED"
    chain_history.append_snapshot(tmp_path, chain_history.snapshot_row(view))
    app.dependency_overrides[options_route.get_options_dir] = lambda: tmp_path
    options_client.chain_calls.clear()
    with TestClient(app) as c:
        res = c.get(f"/api/options/chain?expiry={EXPIRY}")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    assert body["status"] == "MARKET_CLOSED"
    assert body["from_snapshot"] is True
    assert options_client.chain_calls == []


def test_route_expiries(options_client) -> None:
    with TestClient(app) as c:
        res = c.get("/api/options/expiries")
    assert res.status_code == 200
    assert res.json()["expiries"] == ["2026-11-10", "2026-11-17"]


def test_route_missing_token() -> None:
    app.dependency_overrides[options_route.get_options_client] = lambda: None
    try:
        with TestClient(app) as c:
            res = c.get("/api/options/chain")
        assert res.status_code == 503
        assert "token" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(options_route.get_options_client, None)
