"""GEX from the option chain: maths, the client call and the route. No test opens a socket."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.options.bs import bs_forward_delta
from app.options.gex import CRORE, bs_gamma, compute_gex, parse_chain
from app.routes import gex as gex_route
from app.upstox.client import UpstoxApiError, UpstoxClient
from app.upstox.token import DataToken
from tests.upstox_helpers import FAKE_TOKEN, mock_client

IST = timezone(timedelta(hours=5, minutes=30))
EXPIRY = date(2026, 11, 10)


def ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


NOW = ts(2026, 11, 6, 11, 0)


def leg(oi: float, iv: float | None = 15.0, gamma: float | None = 0.0005) -> dict[str, Any]:
    greeks: dict[str, Any] = {}
    if iv is not None:
        greeks["iv"] = iv
    if gamma is not None:
        greeks["gamma"] = gamma
    return {"instrument_key": "NSE_FO|1", "market_data": {"oi": oi, "ltp": 10.0}, "option_greeks": greeks}


def row(strike: float, call: dict, put: dict, spot: float = 25000.0) -> dict[str, Any]:
    return {
        "expiry": EXPIRY.isoformat(),
        "strike_price": strike,
        "underlying_key": "NSE_INDEX|Nifty 50",
        "underlying_spot_price": spot,
        "call_options": call,
        "put_options": put,
    }


# ------------------------------------------------------------------ maths
def test_bs_gamma_is_the_slope_of_delta() -> None:
    S, K, T, sigma = 25000.0, 25100.0, 4 / 365, 0.14
    h = 1.0
    up = bs_forward_delta("CE", S + h, K, T, T, sigma)
    down = bs_forward_delta("CE", S - h, K, T, T, sigma)
    assert bs_gamma(S, K, T, sigma) == pytest.approx((up - down) / (2 * h), rel=1e-4)


def test_bs_gamma_is_zero_for_bad_inputs() -> None:
    assert bs_gamma(25000, 25000, 0.0, 0.15) == 0.0
    assert bs_gamma(25000, 25000, 0.01, 0.0) == 0.0
    assert bs_gamma(0, 25000, 0.01, 0.15) == 0.0


def test_empty_legs_are_zero_oi_and_rows_are_sorted() -> None:
    rows = [
        row(25100, leg(100), {"market_data": {}, "option_greeks": {}}),
        row(24900, {"instrument_key": "x"}, leg(50)),
    ]
    spot, parsed = parse_chain(rows)
    assert spot == 25000.0
    assert [r.strike for r in parsed] == [24900.0, 25100.0]
    assert parsed[0].call.oi == 0.0 and parsed[1].put.oi == 0.0


def test_one_strike_matches_the_formula_by_hand() -> None:
    out = compute_gex([row(25000, leg(1_000_000, iv=15.0), leg(0))], now=NOW, expiry=EXPIRY)
    minutes = int((datetime(2026, 11, 10, 15, 30, tzinfo=IST) - datetime(2026, 11, 6, 11, 0, tzinfo=IST)).total_seconds() // 60)
    T = minutes / (365 * 24 * 60)
    gamma = bs_gamma(25000, 25000, T, 0.15)
    expected = gamma * 1_000_000 * 25000**2 * 0.01 / CRORE
    assert out["available"] is True
    assert out["strikes"][0]["call"] == pytest.approx(expected, rel=1e-6)
    assert out["strikes"][0]["put"] == 0.0
    assert out["total_net"] == pytest.approx(expected, rel=1e-6)
    assert out["regime"] == "positive"


def test_puts_are_negative_and_walls_are_the_biggest_strikes() -> None:
    rows = [
        row(24800, leg(10_000), leg(2_000_000)),
        row(24900, leg(50_000), leg(900_000)),
        row(25000, leg(400_000), leg(400_000)),
        row(25100, leg(800_000), leg(60_000)),
        row(25200, leg(1_500_000), leg(10_000)),
    ]
    out = compute_gex(rows, now=NOW, expiry=EXPIRY)
    by = {s["strike"]: s for s in out["strikes"]}
    assert all(s["put"] <= 0 <= s["call"] for s in out["strikes"])
    assert by[25000]["net"] == pytest.approx(by[25000]["call"] + by[25000]["put"])
    assert out["total_net"] == pytest.approx(sum(s["net"] for s in out["strikes"]), rel=1e-9)
    # the heaviest OI sits away from spot, but gamma is largest at the money: the walls use GEX, not OI
    big_call = max(out["strikes"], key=lambda s: s["call"])["strike"]
    big_put = min(out["strikes"], key=lambda s: s["put"])["strike"]
    assert out["call_wall"] == big_call and out["put_wall"] == big_put


def test_the_gamma_flip_sits_between_the_put_side_and_the_call_side() -> None:
    rows = [
        row(24500, leg(0), leg(3_000_000)),
        row(24750, leg(0), leg(3_000_000)),
        row(25250, leg(3_000_000), leg(0)),
        row(25500, leg(3_000_000), leg(0)),
    ]
    out = compute_gex(rows, now=NOW, expiry=EXPIRY)
    flip = out["gamma_flip"]
    assert flip is not None and 24750 < flip < 25250
    assert flip == pytest.approx(25000, abs=30)  # symmetric chain: the flip is at the middle


def test_no_flip_when_every_strike_is_on_one_side() -> None:
    rows = [row(k, leg(1_000_000), leg(0)) for k in (24800, 25000, 25200)]
    out = compute_gex(rows, now=NOW, expiry=EXPIRY)
    assert out["gamma_flip"] is None and out["put_wall"] is None


def test_api_gamma_is_used_when_iv_is_missing() -> None:
    out = compute_gex([row(25000, leg(1_000_000, iv=0.0, gamma=0.0007), leg(0))], now=NOW, expiry=EXPIRY)
    assert out["strikes"][0]["call"] == pytest.approx(0.0007 * 1_000_000 * 25000**2 * 0.01 / CRORE)


def test_after_the_close_on_expiry_day_time_is_floored_so_gamma_stays_finite() -> None:
    out = compute_gex([row(25000, leg(1_000_000), leg(1_000_000))], now=ts(2026, 11, 10, 16, 0), expiry=EXPIRY)
    assert all(math.isfinite(s["net"]) for s in out["strikes"])


def test_an_empty_chain_is_unavailable() -> None:
    out = compute_gex([], now=NOW, expiry=EXPIRY)
    assert out["available"] is False and out["strikes"] == []


# ------------------------------------------------------------------ client
def test_client_option_chain_sends_key_and_expiry() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"status": "success", "data": [row(25000, leg(1), leg(1))]})

    client = UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(handler), sleep=lambda _: None)
    rows = client.option_chain("NSE_INDEX|Nifty 50", "2026-11-10")
    assert len(rows) == 1
    assert seen[0].url.path == "/v2/option/chain"
    assert seen[0].url.params["instrument_key"] == "NSE_INDEX|Nifty 50"
    assert seen[0].url.params["expiry_date"] == "2026-11-10"


def test_client_option_chain_rejects_a_strange_shape() -> None:
    client = UpstoxClient(
        DataToken(FAKE_TOKEN),
        http=mock_client(lambda _: httpx.Response(200, json={"status": "success", "data": {"x": 1}})),
        sleep=lambda _: None,
    )
    with pytest.raises(UpstoxApiError):
        client.option_chain("NSE_INDEX|Nifty 50", "2026-11-10")


# ------------------------------------------------------------------ route
class FakeChain:
    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def option_chain(self, key: str, expiry: str) -> list[dict]:
        self.calls.append((key, expiry))
        if self.error:
            raise self.error
        return self.rows


@pytest.fixture
def route():
    state: dict[str, Any] = {"now": float(NOW), "client": FakeChain([row(25000, leg(100_000), leg(50_000))])}
    app.dependency_overrides[gex_route.get_gex_client] = lambda: state["client"]
    app.dependency_overrides[gex_route.get_gex_clock] = lambda: (lambda: state["now"])
    app.dependency_overrides[gex_route.get_gex_cache] = lambda: state.setdefault("cache", gex_route.GexCache())
    state["http"] = TestClient(app)
    yield state
    app.dependency_overrides.clear()


def test_route_uses_the_nearest_weekly_expiry_and_caches(route: dict) -> None:
    first = route["http"].get("/api/gex")
    assert first.status_code == 200
    body = first.json()
    assert body["available"] is True and body["expiry"] == "2026-11-10"
    route["http"].get("/api/gex")
    assert route["client"].calls == [("NSE_INDEX|Nifty 50", "2026-11-10")]
    route["now"] += gex_route.CACHE_SECONDS + 1
    route["http"].get("/api/gex")
    assert len(route["client"].calls) == 2


def test_route_moves_to_the_next_expiry_after_the_close_on_expiry_day(route: dict) -> None:
    route["now"] = float(ts(2026, 11, 10, 15, 45))
    assert route["http"].get("/api/gex").json()["expiry"] == "2026-11-17"


def test_route_takes_an_explicit_expiry(route: dict) -> None:
    route["http"].get("/api/gex?expiry=2026-11-24")
    assert route["client"].calls[-1] == ("NSE_INDEX|Nifty 50", "2026-11-24")
    assert route["http"].get("/api/gex?expiry=24-11-2026").status_code == 422


def test_route_without_a_token_is_503(route: dict) -> None:
    route["client"] = None
    res = route["http"].get("/api/gex")
    assert res.status_code == 503 and "token" in res.json()["detail"]


def test_route_upstox_error_is_502_without_the_token(route: dict) -> None:
    route["client"] = FakeChain(error=UpstoxApiError("bad expiry", status=400))
    res = route["http"].get("/api/gex")
    assert res.status_code == 502
    assert FAKE_TOKEN not in res.text
