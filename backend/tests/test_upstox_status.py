import base64
import json
from datetime import datetime, timezone

import httpx

from app.upstox.client import UpstoxClient
from app.upstox.status import StatusCache, check_token
from app.upstox.token import DataToken
from tests.upstox_helpers import FAKE_TOKEN, json_response, mock_client

NOW = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)


def factory(handler):
    calls: list[str] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return handler(req)

    return (lambda: UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(wrapped), sleep=lambda s: None, max_attempts=1)), calls


def jwt_with_exp(exp: int) -> str:
    p = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{p}.c2ln"


def test_missing_token_needs_no_call() -> None:
    f, calls = factory(lambda r: json_response("market_status_nse.json"))
    s = check_token(None, f, now=NOW)
    assert s.state == "missing" and calls == []
    assert "UPSTOX_ANALYTICS_TOKEN" in s.message


def test_valid_token_uses_the_market_status_call_not_the_profile_endpoint() -> None:
    f, calls = factory(lambda r: json_response("market_status_nse.json"))
    s = check_token(FAKE_TOKEN, f, now=NOW)
    assert s.state == "valid"
    assert calls == ["/v2/market/status/NSE"]
    assert "/user/profile" not in "".join(calls)
    assert s.market_status == "NORMAL_CLOSE"
    assert s.expires_at is not None and s.days_left is not None and s.days_left > 365  # fake token expires 2100


def test_rejected_token_is_invalid() -> None:
    f, _ = factory(lambda r: httpx.Response(401, json={"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": "Invalid token"}]}))
    s = check_token("not-a-jwt", f, now=NOW)
    assert s.state == "invalid" and s.expires_at is None
    assert "not-a-jwt" not in s.message


def test_jwt_past_its_exp_is_expired_locally_without_any_network_call() -> None:
    f, calls = factory(lambda r: json_response("market_status_nse.json"))
    s = check_token(jwt_with_exp(int(NOW.timestamp()) - 86400), f, now=NOW)
    assert s.state == "expired" and calls == []
    assert "expired" in s.message.lower()


def test_token_close_to_its_exp_is_valid_but_flagged() -> None:
    f, _ = factory(lambda r: json_response("market_status_nse.json"))
    s = check_token(jwt_with_exp(int(NOW.timestamp()) + 5 * 86400 + 60), f, now=NOW)
    assert s.state == "valid" and s.days_left == 5 and s.expires_soon is True


def test_network_trouble_is_unreachable_not_invalid() -> None:
    f, _ = factory(lambda r: httpx.Response(503))
    s = check_token(FAKE_TOKEN, f, now=NOW)
    assert s.state == "unreachable"


def test_status_cache_avoids_repeat_calls_until_ttl_or_refresh() -> None:
    t = [0.0]
    cache = StatusCache(ttl_s=300, clock=lambda: t[0])
    n = 0

    def compute():
        nonlocal n
        n += 1
        return check_token(None, lambda: None, now=NOW)  # type: ignore[arg-type,return-value]

    cache.get(compute)
    cache.get(compute)
    assert n == 1
    t[0] = 301
    cache.get(compute)
    assert n == 2
    cache.get(compute, refresh=True)
    assert n == 3
    cache.invalidate()
    cache.get(compute)
    assert n == 4
