"""Data-token health: "valid / invalid / expired / missing / unreachable" from a cheap data call.

Uses GET /v2/market/status/NSE (never /user/profile, which needs a static IP). If the token is a
JWT its `exp` is read locally (no network): an expired token is reported without any call.
"""

from __future__ import annotations

import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.upstox.client import UpstoxAuthError, UpstoxClient, UpstoxError
from app.upstox.token import jwt_expiry

TokenState = Literal["valid", "invalid", "expired", "missing", "unreachable"]
EXPIRES_SOON_DAYS = 14
CACHE_TTL_S = 300.0


@dataclass(frozen=True)
class TokenStatus:
    state: TokenState
    message: str
    #: ISO-8601 UTC, only when the token is a JWT with an `exp` claim
    expires_at: str | None = None
    days_left: int | None = None
    expires_soon: bool = False
    #: NSE status from the check call (e.g. NORMAL_OPEN), when it succeeded
    market_status: str | None = None
    checked_at: str | None = None


def _expiry_fields(token: str, now: datetime) -> tuple[str | None, int | None, bool]:
    exp = jwt_expiry(token)
    if exp is None:
        return None, None, False
    days = int((exp - now).total_seconds() // 86400)
    return exp.isoformat(), days, 0 <= days < EXPIRES_SOON_DAYS


def check_token(
    token: str | None,
    client_factory: Callable[[], UpstoxClient],
    *,
    now: datetime | None = None,
) -> TokenStatus:
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    if not token:
        return TokenStatus(
            "missing",
            "No data token. Set UPSTOX_ANALYTICS_TOKEN in .env and restart the backend.",
            checked_at=stamp,
        )
    expires_at, days_left, soon = _expiry_fields(token, now)
    exp = jwt_expiry(token)
    if exp is not None and exp <= now:
        return TokenStatus(
            "expired",
            f"The data token expired on {exp.date().isoformat()}. Generate a new Analytics Token "
            "in the Upstox Developer Apps page and update UPSTOX_ANALYTICS_TOKEN in .env.",
            expires_at=expires_at,
            days_left=days_left,
            checked_at=stamp,
        )
    try:
        data = client_factory().market_status("NSE")
    except UpstoxAuthError:
        return TokenStatus(
            "invalid",
            "Upstox rejected the data token. Generate a new Analytics Token and update "
            "UPSTOX_ANALYTICS_TOKEN in .env.",
            expires_at=expires_at,
            days_left=days_left,
            checked_at=stamp,
        )
    except UpstoxError as e:
        return TokenStatus(
            "unreachable",
            f"Could not verify the data token right now: {e}",
            expires_at=expires_at,
            days_left=days_left,
            expires_soon=soon,
            checked_at=stamp,
        )
    market = data.get("status")
    return TokenStatus(
        "valid",
        "Data token is valid." + (f" Expires in {days_left} days." if days_left is not None else ""),
        expires_at=expires_at,
        days_left=days_left,
        expires_soon=soon,
        market_status=str(market) if market is not None else None,
        checked_at=stamp,
    )


class StatusCache:
    """Remembers the last check for CACHE_TTL_S so polling the UI does not spend API calls."""

    def __init__(self, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = _time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._value: TokenStatus | None = None
        self._at = 0.0

    def get(self, compute: Callable[[], TokenStatus], *, refresh: bool = False) -> TokenStatus:
        with self._lock:
            fresh = self._value is not None and self._clock() - self._at < self._ttl
            if fresh and not refresh:
                assert self._value is not None
                return self._value
        value = compute()
        with self._lock:
            self._value, self._at = value, self._clock()
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._value = None
