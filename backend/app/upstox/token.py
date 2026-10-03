"""The Analytics Token: wrapper that never prints itself, plus a local-only expiry hint.

The token is only ever sent to api.upstox.com by UpstoxClient. `jwt_expiry` decodes the payload
LOCALLY (base64, no network, no signature check) and only if the token looks like a JWT.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone


class DataToken:
    """Holds the secret; repr/str are redacted so it cannot leak through logging."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("empty token")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "DataToken([REDACTED])"

    __str__ = __repr__


def jwt_expiry(token: str) -> datetime | None:
    """`exp` claim of a JWT as an aware UTC datetime; None if not a JWT / no usable exp.

    Never validates the signature and never sends the token anywhere."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if isinstance(exp, bool) or not isinstance(exp, int | float):
            return None
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
