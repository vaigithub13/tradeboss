"""Keep the Upstox token out of logs, error messages and API responses."""

from __future__ import annotations

import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
# JWT-looking strings (the Analytics Token is one): header.payload.signature
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
_AUTH_HEADER = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^'\",}\s]+(\s+[^'\",}\s]+)?")


def redact(text: object, secrets: Iterable[str | None] = ()) -> str:
    """`text` with every known secret and anything token-shaped replaced by [REDACTED]."""
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, REDACTED)
    out = _BEARER.sub(f"Bearer {REDACTED}", out)
    out = _JWT.sub(REDACTED, out)
    out = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    return out
