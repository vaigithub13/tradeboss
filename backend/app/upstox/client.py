"""Read-only Upstox client (plain httpx).

SAFETY: this class has GET methods only and no order code exists anywhere in this phase
(`_get` is the only network primitive; there is no POST/PUT/DELETE helper).

* The Analytics Token is sent only to api.upstox.com; redirects are never followed, so it cannot
  be forwarded to another host.
* Every error text is redacted (token, Bearer headers, JWT-shaped strings).
* All calls go through one shared rate limiter; 429 / 5xx / network errors are retried with
  exponential backoff (Retry-After honoured). 401 and other 4xx are NOT retried.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.upstox.ratelimit import SlidingWindowLimiter
from app.upstox.redact import redact
from app.upstox.token import DataToken

log = logging.getLogger(__name__)

API_BASE = "https://api.upstox.com"

#: raw candle row: [iso timestamp, open, high, low, close, volume, open interest]
RawCandle = list[Any]


class UpstoxError(Exception):
    """Base class. Messages are always redacted."""


class UpstoxAuthError(UpstoxError):
    """401: the token is missing, invalid or expired."""


class UpstoxRateLimited(UpstoxError):
    """Still 429 after all retries."""


class UpstoxUnavailable(UpstoxError):
    """Network error / 5xx after all retries."""


class UpstoxApiError(UpstoxError):
    """Other 4xx (bad request, invalid range, ...): not retried."""

    def __init__(self, message: str, *, status: int, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _error_text(body: Any) -> tuple[str | None, str | None]:
    """(errorCode, message) from an Upstox error body, tolerant of other shapes."""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            e = errors[0]
            return e.get("errorCode"), e.get("message")
        if isinstance(body.get("message"), str):
            return body.get("errorCode"), body["message"]
    return None, None


class UpstoxClient:
    def __init__(
        self,
        token: DataToken,
        *,
        http: httpx.Client | None = None,
        limiter: SlidingWindowLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 5,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 30.0,
        timeout_s: float = 30.0,
    ) -> None:
        self._token = token
        self._http = http or httpx.Client(base_url=API_BASE, timeout=timeout_s, follow_redirects=False)
        self._limiter = limiter
        self._sleep = sleep
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base_s
        self._backoff_max = backoff_max_s

    # ---------------------------------------------------------------- plumbing
    def _redact(self, text: object) -> str:
        return redact(text, [self._token.reveal()])

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(self._backoff_max, max(0.0, float(retry_after)))
            except ValueError:
                pass
        delay = min(self._backoff_max, self._backoff_base * (2**attempt))
        return delay * (0.75 + random.random() * 0.5)

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """GET `path` (relative to https://api.upstox.com). Returns the decoded JSON body."""
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token.reveal()}"}
        last: UpstoxError | None = None
        for attempt in range(self._max_attempts):
            if self._limiter is not None:
                self._limiter.acquire()
            try:
                resp = self._http.get(path, params=params, headers=headers)
            except httpx.HTTPError as e:
                last = UpstoxUnavailable(f"network error: {self._redact(type(e).__name__)}")
                retry_after = None
            else:
                status = resp.status_code
                try:
                    body: Any = resp.json()
                except ValueError:
                    body = None
                if status == 200:
                    if isinstance(body, dict) and body.get("status") != "error":
                        return body
                    code, msg = _error_text(body)
                    raise UpstoxApiError(
                        self._redact(msg or "Upstox returned an error"), status=status, code=code
                    )
                code, msg = _error_text(body)
                text = self._redact(msg or f"HTTP {status}")
                if status in (401, 403):
                    raise UpstoxAuthError(
                        f"Upstox rejected the data token (HTTP {status}"
                        f"{', ' + code if code else ''}): {text}"
                    )
                if status == 429:
                    last = UpstoxRateLimited(f"rate limited by Upstox (HTTP 429): {text}")
                    retry_after = resp.headers.get("Retry-After")
                elif status >= 500:
                    last = UpstoxUnavailable(f"Upstox server error (HTTP {status}): {text}")
                    retry_after = resp.headers.get("Retry-After")
                else:
                    raise UpstoxApiError(
                        f"{text}" + (f" [{code}]" if code else ""), status=status, code=code
                    )
            if attempt + 1 < self._max_attempts:
                delay = self._backoff(attempt, retry_after)
                log.warning("upstox retry %d/%d in %.1fs: %s", attempt + 1, self._max_attempts, delay, last)
                self._sleep(delay)
        assert last is not None
        raise last

    # ---------------------------------------------------------------- read-only API
    def market_status(self, exchange: str = "NSE") -> dict[str, Any]:
        """GET /v2/market/status/{exchange}: cheap, needs no static IP with an Analytics Token."""
        body = self._get(f"/v2/market/status/{quote(exchange, safe='')}")
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    def historical_candles(
        self,
        instrument_key: str,
        from_date: date,
        to_date: date,
        *,
        unit: str = "minutes",
        interval: int = 1,
    ) -> list[RawCandle]:
        """GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from} (dates inclusive).

        Upstox limits 1-15 minute data to ONE MONTH per request; callers must window."""
        key = quote(instrument_key, safe="")
        body = self._get(
            f"/v3/historical-candle/{key}/{unit}/{interval}/{to_date.isoformat()}/{from_date.isoformat()}"
        )
        return _candles_of(body)

    def intraday_candles(
        self, instrument_key: str, *, unit: str = "minutes", interval: int = 1
    ) -> list[RawCandle]:
        """GET /v3/historical-candle/intraday/{key}/{unit}/{interval}: the current trading day."""
        key = quote(instrument_key, safe="")
        body = self._get(f"/v3/historical-candle/intraday/{key}/{unit}/{interval}")
        return _candles_of(body)

    def expired_historical_candles(
        self, expired_key: str, from_date: date, to_date: date, interval: str = "1minute"
    ) -> list[RawCandle]:
        """GET /v2/expired-instruments/historical-candle/{expired_key}/{interval}/{to}/{from} (inclusive).

        `expired_key` looks like NSE_FO|58548|03-10-2024 (instrument key + expiry). Newest candle first."""
        key = quote(expired_key, safe="")
        body = self._get(
            f"/v2/expired-instruments/historical-candle/{key}/{interval}/{to_date.isoformat()}/{from_date.isoformat()}"
        )
        return _candles_of(body)

    def expired_expiries(self, instrument_key: str) -> list[date]:
        """GET /v2/expired-instruments/expiries: the past expiry dates of an underlying (ascending)."""
        body = self._get("/v2/expired-instruments/expiries", {"instrument_key": instrument_key})
        data = body.get("data")
        if not isinstance(data, list):
            raise UpstoxApiError("unexpected response shape (no data list)", status=200)
        return sorted(date.fromisoformat(str(x)) for x in data)

    def expired_option_contracts(self, instrument_key: str, expiry: date) -> list[dict[str, Any]]:
        """GET /v2/expired-instruments/option/contract: the option contracts that settled on `expiry`."""
        body = self._get(
            "/v2/expired-instruments/option/contract",
            {"instrument_key": instrument_key, "expiry_date": expiry.isoformat()},
        )
        data = body.get("data")
        if not isinstance(data, list):
            raise UpstoxApiError("unexpected response shape (no data list)", status=200)
        return [x for x in data if isinstance(x, dict)]


def _candles_of(body: dict[str, Any]) -> list[RawCandle]:
    data = body.get("data")
    candles = data.get("candles") if isinstance(data, dict) else None
    if not isinstance(candles, list):
        raise UpstoxApiError("unexpected response shape (no data.candles)", status=200)
    return candles


def parse_candles(rows: list[RawCandle]) -> list[dict[str, Any]]:
    """Raw Upstox rows -> raw bars {"t": unix ms, open, high, low, close, volume, oi}, ASCENDING.

    The docs do not say which order the API returns, so we always sort. Duplicate timestamps keep
    the last one seen. Malformed rows raise ValueError (never silently dropped)."""
    by_t: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError(f"malformed candle row: {row!r}")
        ts = datetime.fromisoformat(str(row[0]))
        if ts.tzinfo is None:
            raise ValueError(f"candle timestamp without timezone: {row[0]!r}")
        t = int(ts.timestamp()) * 1000
        by_t[t] = {
            "t": t,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0),
            "oi": float(row[6]) if len(row) > 6 and row[6] is not None else None,
        }
    return [by_t[t] for t in sorted(by_t)]
