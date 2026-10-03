"""Replay bars from stored 1-minute data. Written before app code.

Public API under test (app/replay/cursor.py, not written yet):

    replay_bars(minutes, timeframe, cursor) -> list[Candle]

Rules
-----
* `cursor` is unix seconds. A source bar is used only when its start time is <= cursor.
* The result is `resample` of that prefix. No bar is invented. A higher-timeframe
  bucket may be partial.
* No network and no live feed. The function only reads the candles it is given.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.data.candle import Candle
from app.replay.cursor import replay_bars

IST = timezone(timedelta(hours=5, minutes=30))


def ist(h: int, mi: int, sec: int = 0) -> int:
    return int(datetime(2026, 6, 15, h, mi, sec, tzinfo=IST).timestamp())


def bar(t: int, o: float, h: float, l: float, c: float, v: float = 1.0) -> Candle:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v, "oi": None}


def morning() -> list[Candle]:
    """09:15 through 09:21. The 09:18 minute is a spike that a 09:17 cursor must not see."""
    rows = [
        (15, 10, 12, 9, 11, 1),
        (16, 11, 14, 10, 13, 1),
        (17, 13, 13, 8, 12, 1),
        (18, 20, 30, 1, 25, 100),
        (19, 1, 1, 1, 1, 1),
        (20, 40, 41, 39, 40, 1),
        (21, 42, 43, 38, 42, 1),
    ]
    return [bar(ist(9, minute), o, h, low, c, v) for minute, o, h, low, c, v in rows]


def test_a_5m_bar_at_09_17_uses_only_the_minutes_up_to_that_moment() -> None:
    candles = replay_bars(morning(), "5m", ist(9, 17))
    assert len(candles) == 1
    candle = candles[0]
    assert candle["time"] == ist(9, 15)
    assert candle["open"] == 10
    assert candle["high"] == 14
    assert candle["low"] == 8
    assert candle["close"] == 12
    assert candle["volume"] == 3


def test_thirty_seconds_into_the_minute_still_includes_that_minute_and_not_the_next() -> None:
    candles = replay_bars(morning(), "5m", ist(9, 17, 30))
    assert candles[0]["close"] == 12
    assert candles[0]["high"] == 14
    assert candles[0]["volume"] == 3


def test_the_cursor_on_09_19_completes_the_5m_bar_and_still_hides_09_20() -> None:
    candles = replay_bars(morning(), "5m", ist(9, 19))
    assert len(candles) == 1
    assert candles[0]["high"] == 30
    assert candles[0]["low"] == 1
    assert candles[0]["close"] == 1
    assert candles[0]["volume"] == 104


def test_a_15m_bucket_is_partial_when_later_minutes_exist_in_storage() -> None:
    candles = replay_bars(morning(), "15m", ist(9, 20))
    assert len(candles) == 1
    assert candles[0]["time"] == ist(9, 15)
    assert candles[0]["close"] == 40
    assert candles[0]["volume"] == 105
    assert all(candle["time"] <= ist(9, 20) for candle in candles)


def test_a_cursor_before_the_session_yields_no_bars() -> None:
    assert replay_bars(morning(), "5m", ist(9, 14)) == []


def test_replay_does_not_open_a_network_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    replay_bars(morning(), "1m", ist(9, 16))
