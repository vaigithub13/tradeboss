"""UpstoxClient against saved sample responses (httpx.MockTransport): no network, no real token."""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.upstox import client as client_module
from app.upstox.client import (
    UpstoxApiError,
    UpstoxAuthError,
    UpstoxClient,
    UpstoxRateLimited,
    UpstoxUnavailable,
    parse_candles,
)
from app.upstox.ratelimit import SlidingWindowLimiter
from app.upstox.token import DataToken
from tests.upstox_helpers import FAKE_TOKEN, fixture, json_response, mock_client


def make(handler, **kw) -> tuple[UpstoxClient, list[float]]:
    sleeps: list[float] = []
    c = UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(handler), sleep=sleeps.append, **kw)
    return c, sleeps


# ------------------------------------------------------------------ request shape
def test_historical_url_encodes_the_key_puts_to_before_from_and_sends_the_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return json_response("historical_1m_index.json")

    c, _ = make(handler)
    rows = c.historical_candles("NSE_INDEX|Nifty 50", date(2026, 9, 1), date(2026, 9, 28))
    assert len(rows) == 8
    (req,) = seen
    assert req.method == "GET"
    assert req.url.host == "api.upstox.com"
    assert req.url.raw_path.decode() == (
        "/v3/historical-candle/NSE_INDEX%7CNifty%2050/minutes/1/2026-09-28/2026-09-01"
    )
    assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"


def test_intraday_and_market_status_urls() -> None:
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.raw_path.decode())
        if "market/status" in paths[-1]:
            return json_response("market_status_nse.json")
        return json_response("historical_1m_index.json")

    c, _ = make(handler)
    c.intraday_candles("NSE_FO|48704")
    assert c.market_status("NSE")["status"] == "NORMAL_CLOSE"
    assert paths == ["/v3/historical-candle/intraday/NSE_FO%7C48704/minutes/1", "/v2/market/status/NSE"]


# ------------------------------------------------------------------ candle parsing
def test_parse_candles_sorts_ascending_converts_ist_to_unix_ms_and_keeps_oi() -> None:
    bars = parse_candles(fixture("historical_1m_index.json")["data"]["candles"])
    assert [b["t"] for b in bars] == sorted(b["t"] for b in bars)  # API sent newest first
    first = bars[0]  # 2026-09-29 09:15 IST = 03:45 UTC
    assert first["t"] == 1790653500000  # 2026-09-29T03:45:00Z
    assert first["open"] == 24950.0 and first["volume"] == 0.0 and first["oi"] == 0.0  # index: no volume
    fut = parse_candles(fixture("historical_1m_future.json")["data"]["candles"])
    assert fut[0]["oi"] == 9000000 and fut[0]["volume"] == 2400


def test_parse_candles_duplicate_timestamps_keep_the_last_and_bad_rows_raise() -> None:
    row = ["2026-09-30T09:15:00+05:30", 1, 2, 0, 1, 5, 0]
    out = parse_candles([row, ["2026-09-30T09:15:00+05:30", 9, 9, 9, 9, 7, 0]])
    assert len(out) == 1 and out[0]["open"] == 9
    with pytest.raises(ValueError):
        parse_candles([["2026-09-30T09:15:00+05:30", 1, 2]])
    with pytest.raises(ValueError):
        parse_candles([["2026-09-30T09:15:00", 1, 2, 0, 1, 5, 0]])  # no timezone: refuse to guess


# ------------------------------------------------------------------ errors + retries
def test_401_raises_auth_error_without_retrying_and_never_leaks_the_token() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = fixture("error_401.json")
        body["errors"][0]["message"] = f"bad token {FAKE_TOKEN} sent as Bearer {FAKE_TOKEN}"
        return httpx.Response(401, json=body)

    c, sleeps = make(handler)
    with pytest.raises(UpstoxAuthError) as ei:
        c.market_status()
    assert calls == 1 and sleeps == []
    assert FAKE_TOKEN not in str(ei.value)
    assert "UDAPI100050" in str(ei.value)


def test_429_is_retried_with_retry_after_then_succeeds() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        if n < 3:
            return httpx.Response(429, json=fixture("error_429.json"), headers={"Retry-After": "2"})
        return json_response("market_status_nse.json")

    c, sleeps = make(handler)
    assert c.market_status()["exchange"] == "NSE"
    assert n == 3
    assert sleeps == [2.0, 2.0]


def test_5xx_uses_exponential_backoff_then_gives_up_as_unavailable() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(503, text="upstream down")

    c, sleeps = make(handler, max_attempts=4, backoff_base_s=1.0)
    with pytest.raises(UpstoxUnavailable):
        c.market_status()
    assert n == 4 and len(sleeps) == 3
    # 1s, 2s, 4s each with +-25% jitter
    for s, base in zip(sleeps, (1.0, 2.0, 4.0), strict=True):
        assert 0.75 * base <= s <= 1.25 * base


def test_persistent_429_ends_as_rate_limited() -> None:
    c, _ = make(lambda r: httpx.Response(429, json=fixture("error_429.json")), max_attempts=2)
    with pytest.raises(UpstoxRateLimited):
        c.market_status()


def test_network_errors_are_retried_then_reported_without_details_that_could_leak() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        raise httpx.ConnectError(f"boom for Bearer {FAKE_TOKEN}")

    c, _ = make(handler, max_attempts=3)
    with pytest.raises(UpstoxUnavailable) as ei:
        c.market_status()
    assert n == 3
    assert FAKE_TOKEN not in str(ei.value)


def test_other_4xx_is_not_retried_and_carries_the_upstox_error_code() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(400, json=fixture("error_range_1148.json"))

    c, sleeps = make(handler)
    with pytest.raises(UpstoxApiError) as ei:
        c.historical_candles("NSE_INDEX|Nifty 50", date(2026, 1, 1), date(2026, 9, 1))
    assert n == 1 and sleeps == []
    assert ei.value.code == "UDAPI1148" and ei.value.status == 400


def test_a_200_with_an_error_body_or_odd_shape_is_an_api_error() -> None:
    c, _ = make(lambda r: httpx.Response(200, json={"status": "error", "errors": [{"errorCode": "X1", "message": "no"}]}))
    with pytest.raises(UpstoxApiError):
        c.market_status()
    c2, _ = make(lambda r: httpx.Response(200, json={"status": "success", "data": {"nope": 1}}))
    with pytest.raises(UpstoxApiError):
        c2.intraday_candles("NSE_FO|1")


def test_every_attempt_goes_through_the_shared_rate_limiter() -> None:
    acquired = 0

    class Counting(SlidingWindowLimiter):
        def acquire(self) -> None:
            nonlocal acquired
            acquired += 1

    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(500) if n == 1 else json_response("market_status_nse.json")

    c, _ = make(handler, limiter=Counting([(1, 1.0)]))
    c.market_status()
    assert acquired == 2  # the retry also counts against the budget


def test_redirects_are_never_followed_so_the_token_cannot_travel_to_another_host() -> None:
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(str(req.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    c, _ = make(handler, max_attempts=1)
    with pytest.raises(UpstoxApiError):
        c.market_status()
    assert hits == ["https://api.upstox.com/v2/market/status/NSE"]


def test_default_client_is_pinned_to_api_upstox_com_and_does_not_follow_redirects() -> None:
    c = UpstoxClient(DataToken(FAKE_TOKEN))
    assert str(c._http.base_url).startswith("https://api.upstox.com")
    assert c._http.follow_redirects is False


# ------------------------------------------------------------------ read-only guarantee
def test_client_exposes_only_read_methods_and_only_issues_GET() -> None:
    public = {n for n, _ in inspect.getmembers(UpstoxClient, inspect.isfunction) if not n.startswith("_")}
    assert public == {
        "market_status", "historical_candles", "intraday_candles",
        "expired_historical_candles", "expired_expiries",
        "expired_future_contracts", "expired_option_contracts",
    }
    src = inspect.getsource(client_module)
    for verb in (".post(", ".put(", ".delete(", ".patch(", ".request("):
        assert verb not in src


def test_no_order_code_exists_anywhere_in_the_backend() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    forbidden = ("place_order", "/order/", "modify_order", "cancel_order", "v2/order", "v3/order", "/portfolio/")
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text()
        offenders += [(path.name, f) for f in forbidden if f in text]
    assert offenders == []
