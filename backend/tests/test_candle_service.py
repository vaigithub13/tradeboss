"""get_candles = store + resampler + range / session-type rules."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.data.service import (
    TimeframeUnavailable,
    UnknownSessionType,
    UnknownTimeframe,
    get_candles,
)
from app.data.store import CandleStore
from tests.conftest import IST, ist_ts

ALL_TYPES = ["normal", "weekend_full", "special_short", "muhurat"]


def hhmm(t: int) -> str:
    return datetime.fromtimestamp(t, IST).strftime("%m-%d %H:%M")


def test_5m_passthrough_and_1h(store: CandleStore) -> None:
    res = get_candles(store, "NIFTY50", "5m")
    assert res.source_minutes == 5
    assert len(res.candles) == 300

    hourly = get_candles(store, "NIFTY50", "1h").candles
    assert len(hourly) == 7 * 4
    # Monday base 100, 12 five-minute bars: O=100 H=100+11+2 L=99 C=100+11+1 V=120
    first = hourly[0]
    assert first["time"] == ist_ts(2024, 10, 28, 9, 15)
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (
        100, 113, 99, 112, 120,
    )  # fmt: skip
    # partial last candle 15:15-15:30 = 3 five-minute bars (i = 72..74)
    last_mon = hourly[6]
    assert hhmm(last_mon["time"]) == "10-28 15:15"
    assert (last_mon["open"], last_mon["high"], last_mon["low"], last_mon["close"],
            last_mon["volume"]) == (172, 176, 171, 175, 30)  # fmt: skip


def test_unavailable_unknown_timeframes_and_unknown_session_type(store: CandleStore) -> None:
    for tf in ("1m", "3m"):
        with pytest.raises(TimeframeUnavailable) as exc:
            get_candles(store, "NIFTY50", tf)
        assert "5m" in str(exc.value)  # tells the user what the base data is
    with pytest.raises(UnknownTimeframe):
        get_candles(store, "NIFTY50", "2m")
    with pytest.raises(UnknownSessionType):
        get_candles(store, "NIFTY50", "5m", session_types=["normal", "weekend"])


def test_default_sessions_are_normal_plus_weekend_full(store: CandleStore) -> None:
    daily = get_candles(store, "NIFTY50", "1D").candles
    assert [hhmm(c["time"]) for c in daily] == [
        "10-28 09:15", "10-29 09:15", "11-04 09:15", "11-16 09:15",
    ]  # fmt: skip


def test_normal_only_excludes_weekend_full(store: CandleStore) -> None:
    daily = get_candles(store, "NIFTY50", "1D", session_types=["normal"]).candles
    assert [hhmm(c["time"]) for c in daily] == ["10-28 09:15", "10-29 09:15", "11-04 09:15"]


def test_special_short_and_muhurat_included_when_requested(store: CandleStore) -> None:
    daily = get_candles(store, "NIFTY50", "1D", session_types=ALL_TYPES).candles
    assert [hhmm(c["time"]) for c in daily] == [
        "10-28 09:15", "10-29 09:15", "11-01 18:00", "11-04 09:15", "11-16 09:15", "11-17 10:00",
    ]  # fmt: skip


def test_muhurat_uses_its_own_anchor(store: CandleStore) -> None:
    hourly = get_candles(store, "NIFTY50", "1h", session_types=["normal", "muhurat"]).candles
    muhurat = [c for c in hourly if hhmm(c["time"]).startswith("11-01")]
    assert [hhmm(c["time"]) for c in muhurat] == ["11-01 18:00"]
    assert muhurat[0]["open"] == 1000 and muhurat[0]["volume"] == 120  # 12 bars


def test_special_short_keeps_0915_anchor(store: CandleStore) -> None:
    # Sunday 11-17: 12 five-minute bars 10:00..10:55 -> 1h buckets anchored to 09:15 => 09:15, 10:15
    hourly = get_candles(store, "NIFTY50", "1h", session_types=["special_short"]).candles
    assert [hhmm(c["time"]) for c in hourly] == ["11-17 09:15", "11-17 10:15"]
    assert hourly[0]["volume"] == 30 and hourly[1]["volume"] == 90  # 10:00-10:10 | 10:15-10:55


def test_weekly_close_depends_on_muhurat_setting(store: CandleStore) -> None:
    w_off = get_candles(store, "NIFTY50", "1W").candles
    w_on = get_candles(store, "NIFTY50", "1W", session_types=["normal", "weekend_full",
                                                              "muhurat"]).candles  # fmt: skip
    assert [hhmm(c["time"]) for c in w_off] == ["10-28 09:15", "11-04 09:15", "11-16 09:15"]
    assert len(w_on) == 3
    assert w_off[0]["close"] == 275  # Tue 10-29 last 5m bar: 200+74+1
    assert w_on[0]["close"] == 1012  # Muhurat Fri last bar: 1000+11+1
    # Saturday budget session forms its own week (Mon 11-11 week had no weekday sessions)
    assert w_off[2]["open"] == 500 and w_off[2]["volume"] == 750


def test_range_filters_on_candle_start_time(store: CandleStore) -> None:
    tue_open = ist_ts(2024, 10, 29, 9, 15)
    tue_close = ist_ts(2024, 10, 29, 15, 25)
    day = get_candles(store, "NIFTY50", "1h", from_time=tue_open, to_time=tue_close).candles
    assert len(day) == 7 and hhmm(day[0]["time"]) == "10-29 09:15"

    # from mid-bucket: the 09:15 hourly candle started before `from`, so it is excluded,
    # but the 10:15 candle (and later) keep their FULL data.
    mid = get_candles(store, "NIFTY50", "1h", from_time=ist_ts(2024, 10, 29, 10, 0),
                      to_time=tue_close).candles  # fmt: skip
    assert [hhmm(c["time"]) for c in mid][0] == "10-29 10:15"
    assert len(mid) == 6

    # weekly: week candle starting Mon 10-28 is before `from` (Tue) -> only later weeks
    wk = get_candles(store, "NIFTY50", "1W", from_time=tue_open).candles
    assert [hhmm(c["time"]) for c in wk] == ["11-04 09:15", "11-16 09:15"]

    # weekly with `to` mid-week still returns the full-week candle that starts before `to`
    wk2 = get_candles(store, "NIFTY50", "1W", to_time=tue_open).candles
    assert [hhmm(c["time"]) for c in wk2] == ["10-28 09:15"]
    assert wk2[0]["close"] == 275  # includes all of Tuesday, not cut at `to`
