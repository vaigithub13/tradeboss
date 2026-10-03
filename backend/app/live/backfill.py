"""Backfill: completed minutes from the intraday API after a gap (blocking; run in a thread)."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from app.live.model import MS_MIN, Bar, ist_date
from app.upstox.client import parse_candles


class _Intraday(Protocol):
    def intraday_candles(self, instrument_key: str, *, unit: str = ..., interval: int = ...) -> list[Any]: ...


def raw_to_bars(raw: list[dict], day: date, source: str, *, first: int | None = None, last: int | None = None) -> list[Bar]:
    out: list[Bar] = []
    for r in raw:
        t = int(r["t"])
        if t % MS_MIN or ist_date(t) != day:
            continue
        m = t // MS_MIN
        if (first is not None and m < first) or (last is not None and m > last):
            continue
        out.append(Bar(m, r["open"], r["high"], r["low"], r["close"], float(r["volume"]), r.get("oi"), source))  # type: ignore[arg-type]
    return out


def fetch_backfill(client: _Intraday, key: str, day: date, first_minute: int, last_minute: int) -> list[Bar]:
    """Minutes [first_minute, last_minute] of `day` from the intraday API (the current trading day)."""
    rows = client.intraday_candles(key, unit="minutes", interval=1)
    return raw_to_bars(parse_candles(rows), day, "backfill", first=first_minute, last=last_minute)
