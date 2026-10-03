"""Helpers for the Upstox tests. NOTHING here touches the network or a real token."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "upstox"
IST = timezone(timedelta(hours=5, minutes=30))

#: a fake token that looks like a JWT (so redaction of JWT-shaped strings is exercised too)
FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXIiLCJleHAiOjQxMDI0NDQ4MDB9.c2lnbmF0dXJlLXNpZ25hdHVyZQ"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def master_rows() -> list[dict[str, Any]]:
    return fixture("instruments_nse_sample.json")


def make_master_bytes(extra: list[dict[str, Any]] | None = None, pad: int = 1100) -> bytes:
    """A gzipped instrument file: the saved real sample rows + filler so it passes the size check."""
    rows = master_rows() + (extra or [])
    rows += [
        {"segment": "NSE_EQ", "instrument_type": "SG", "instrument_key": f"NSE_EQ|FILL{i:05d}",
         "trading_symbol": f"FILL{i}", "name": f"Filler {i}"}
        for i in range(pad)
    ]
    return gzip.compress(json.dumps(rows).encode())


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.upstox.com", transport=httpx.MockTransport(handler), follow_redirects=False
    )


def json_response(name: str, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=fixture(name), headers=headers)


def ist(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def session_rows(day: date, *, base: float = 25000.0, bars: int = 375, oi: float | None = None,
                 start_minute: int = 9 * 60 + 15) -> list[list[Any]]:
    """Upstox-shaped candle rows for one full 1m session, NEWEST FIRST like the API."""
    rows: list[list[Any]] = []
    for i in range(bars):
        m = start_minute + i
        t = datetime(day.year, day.month, day.day, m // 60, m % 60, tzinfo=IST)
        px = base + i * 0.5
        rows.append([t.isoformat(), px, px + 2, px - 1, px + 1, 100.0 + i, oi if oi is not None else 0])
    return rows[::-1]


class FakeClient:
    """Stands in for UpstoxClient in history tests: a full session for every Mon-Fri in the range."""

    def __init__(self, *, today_rows: Callable[[], list[list[Any]]] | None = None, oi: float | None = None) -> None:
        self.historical_calls: list[tuple[str, date, date]] = []
        self.intraday_calls: list[str] = []
        self._today_rows = today_rows
        self._oi = oi

    def historical_candles(self, key: str, from_date: date, to_date: date, **_: Any) -> list[list[Any]]:
        self.historical_calls.append((key, from_date, to_date))
        assert (to_date - from_date).days < 31, "Upstox limits 1m requests to one month"
        rows: list[list[Any]] = []
        d = from_date
        while d <= to_date:
            if d.weekday() < 5:
                rows += session_rows(d, oi=self._oi)
            d += timedelta(days=1)
        return rows

    def intraday_candles(self, key: str, **_: Any) -> list[list[Any]]:
        self.intraday_calls.append(key)
        return self._today_rows() if self._today_rows else []
