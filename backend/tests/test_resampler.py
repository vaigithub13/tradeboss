"""Resampler spec (tests written BEFORE the implementation).

Public API under test (app/data/resampler.py, not written yet):

    TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W")
    resample(candles, timeframe, source_minutes=1) -> list[Candle]

Rules being specified
---------------------
* Times are unix seconds of the bar START. All calendar logic is IST (UTC+5:30).
* NSE session = 09:15 <= bar start < 15:30 IST. Bars outside it are ignored.
* Intraday buckets are anchored to 09:15:  bucket = floor(minutes_since_0915 / N).
  Candle time = 09:15 + bucket * N minutes. The last bucket of the day may be
  partial (1h -> 15:15-15:30, 30m -> 15:15-15:30).
* 1D  = one candle per IST date that has bars; time = first bar time of the day.
* 1W  = one candle per IST Mon..Sun week that has bars; time = first bar time of
  the week's first trading day.
* Only buckets that contain at least one source bar are emitted (no fake candles
  for holidays / weekends / missing minutes).
* open = first, high = max, low = min, close = last, volume = sum, oi = last bar's oi.
* The target timeframe must be a multiple of the source bar size (5m source can
  produce 5m/15m/30m/1h/1D/1W but NOT 1m/3m) -> ValueError.
* Muhurat sessions (stored with a label, see sessions.py): the caller passes their
  IST dates in `anchor_to_first_bar_dates`. For those dates ONLY, bars are not
  clipped to 09:15-15:30 and intraday buckets are anchored to that day's first
  bar (a Muhurat can run 18:00-19:00, so 09:15 anchoring makes no sense). 1D/1W
  treat them like any other day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.data.candle import Candle
from app.data.resampler import TIMEFRAMES, available_timeframes, resample

IST = timezone(timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- helpers
def ist(y: int, mo: int, d: int, h: int, mi: int) -> int:
    """Unix seconds for an IST wall-clock time."""
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def bar(
    t: int,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 1.0,
    oi: float | None = None,
) -> Candle:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v, "oi": oi}


def session_1m(y: int, mo: int, d: int, base: float) -> list[Candle]:
    """A full NSE session of 375 one-minute bars, 09:15..15:29.

    minute index i = 0..374:
        open = base + i, high = base + i + 2, low = base + i - 1,
        close = base + i + 1, volume = 10
    """
    start = ist(y, mo, d, 9, 15)
    return [
        bar(start + 60 * i, base + i, base + i + 2, base + i - 1, base + i + 1, 10.0)
        for i in range(375)
    ]


def hhmm(c: Candle) -> str:
    return datetime.fromtimestamp(c["time"], IST).strftime("%H:%M")


def ohlcv(c: Candle) -> tuple[float, float, float, float, float]:
    return (c["open"], c["high"], c["low"], c["close"], c["volume"])


MON = (2024, 1, 1)  # Monday 2024-01-01


# --------------------------------------------------------------------------- 1m -> N
def test_timeframes_constant() -> None:
    assert TIMEFRAMES == ("1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W")


def test_1m_to_1m_is_identity() -> None:
    src = session_1m(*MON, base=100)
    assert resample(src, "1m") == src


def test_1m_to_3m() -> None:
    out = resample(session_1m(*MON, base=100), "3m")
    assert len(out) == 125
    assert hhmm(out[0]) == "09:15"
    assert ohlcv(out[0]) == (100, 104, 99, 103, 30)
    assert hhmm(out[1]) == "09:18"
    assert ohlcv(out[1]) == (103, 107, 102, 106, 30)
    assert hhmm(out[-1]) == "15:27"
    assert ohlcv(out[-1]) == (472, 476, 471, 475, 30)


def test_1m_to_5m() -> None:
    out = resample(session_1m(*MON, base=100), "5m")
    assert len(out) == 75
    assert (hhmm(out[0]), ohlcv(out[0])) == ("09:15", (100, 106, 99, 105, 50))
    assert (hhmm(out[-1]), ohlcv(out[-1])) == ("15:25", (470, 476, 469, 475, 50))


def test_1m_to_15m() -> None:
    out = resample(session_1m(*MON, base=100), "15m")
    assert len(out) == 25
    assert (hhmm(out[0]), ohlcv(out[0])) == ("09:15", (100, 116, 99, 115, 150))
    assert (hhmm(out[1]), ohlcv(out[1])) == ("09:30", (115, 131, 114, 130, 150))
    assert (hhmm(out[-1]), ohlcv(out[-1])) == ("15:15", (460, 476, 459, 475, 150))


def test_1m_to_30m_last_candle_is_partial_1515() -> None:
    out = resample(session_1m(*MON, base=100), "30m")
    assert [hhmm(c) for c in out] == [
        "09:15", "09:45", "10:15", "10:45", "11:15", "11:45", "12:15",
        "12:45", "13:15", "13:45", "14:15", "14:45", "15:15",
    ]  # fmt: skip
    assert ohlcv(out[0]) == (100, 131, 99, 130, 300)
    assert ohlcv(out[-1]) == (460, 476, 459, 475, 150)  # only 15 minutes of data


def test_1m_to_1h_anchored_to_0915_last_candle_1515_1530() -> None:
    out = resample(session_1m(*MON, base=100), "1h")
    assert [hhmm(c) for c in out] == [
        "09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15",
    ]  # fmt: skip
    assert ohlcv(out[0]) == (100, 161, 99, 160, 600)
    assert ohlcv(out[1]) == (160, 221, 159, 220, 600)
    assert ohlcv(out[5]) == (400, 461, 399, 460, 600)
    assert ohlcv(out[6]) == (460, 476, 459, 475, 150)  # 15:15-15:30 only


def test_1m_to_1d() -> None:
    out = resample(session_1m(*MON, base=100), "1D")
    assert len(out) == 1
    assert hhmm(out[0]) == "09:15"
    assert ohlcv(out[0]) == (100, 476, 99, 475, 3750)


def test_1m_to_1w_full_week() -> None:
    # Mon..Fri, day bases 100, 200, 300, 400, 500
    src: list[Candle] = []
    for i in range(5):
        src += session_1m(2024, 1, 1 + i, base=100 * (i + 1))

    daily = resample(src, "1D")
    assert [ohlcv(c) for c in daily] == [
        (100, 476, 99, 475, 3750),
        (200, 576, 199, 575, 3750),
        (300, 676, 299, 675, 3750),
        (400, 776, 399, 775, 3750),
        (500, 876, 499, 875, 3750),
    ]

    weekly = resample(src, "1W")
    assert len(weekly) == 1
    assert weekly[0]["time"] == ist(2024, 1, 1, 9, 15)
    assert ohlcv(weekly[0]) == (100, 876, 99, 875, 18750)


# --------------------------------------------------------------------------- OHLCV rules
def test_ohlcv_rules_non_monotone_data() -> None:
    t = ist(*MON, 9, 15)
    src = [
        bar(t, 10, 15, 9, 12, 1),         # first: open comes from here
        bar(t + 60, 12, 20, 11, 13, 2),   # highest high is in the MIDDLE
        bar(t + 120, 13, 14, 5, 6, 3),    # lowest low + last close are here
    ]
    out = resample(src, "3m")
    assert out == [bar(t, 10, 20, 5, 6, 6)]


def test_oi_is_taken_from_last_bar() -> None:
    t = ist(*MON, 9, 15)
    src = [
        bar(t, 1, 1, 1, 1, 1, oi=100),
        bar(t + 60, 1, 1, 1, 1, 1, oi=120),
        bar(t + 120, 1, 1, 1, 1, 1, oi=150),
    ]
    assert resample(src, "3m")[0]["oi"] == 150
    assert resample(src[:1], "3m")[0]["oi"] == 100


def test_oi_none_stays_none() -> None:
    t = ist(*MON, 9, 15)
    out = resample([bar(t, 1, 1, 1, 1), bar(t + 60, 1, 1, 1, 1)], "3m")
    assert out[0]["oi"] is None


def test_unsorted_input_gives_same_result_and_input_not_mutated() -> None:
    src = session_1m(*MON, base=100)[:30]
    shuffled = list(reversed(src))
    snapshot = [dict(c) for c in shuffled]
    assert resample(shuffled, "15m") == resample(src, "15m")
    assert [dict(c) for c in shuffled] == snapshot


def test_empty_input() -> None:
    for tf in TIMEFRAMES:
        assert resample([], tf) == []


# --------------------------------------------------------------------------- IST + session
def test_ist_anchoring_uses_literal_epoch_values() -> None:
    # 2024-01-01 09:15 IST == 03:45 UTC == 1704080700 (checked by hand)
    assert ist(2024, 1, 1, 9, 15) == 1704080700
    out = resample(session_1m(*MON, base=100), "1h")
    assert out[0]["time"] == 1704080700            # 09:15 IST
    assert out[1]["time"] == 1704080700 + 3600     # 10:15 IST
    assert out[6]["time"] == 1704080700 + 6 * 3600  # 15:15 IST


def test_bars_outside_session_are_ignored() -> None:
    src = [
        bar(ist(*MON, 9, 10), 1, 1, 1, 1, 5),     # pre-open print  -> ignored
        bar(ist(*MON, 9, 15), 10, 11, 9, 10.5, 1),
        bar(ist(*MON, 15, 29), 20, 21, 19, 20.5, 1),
        bar(ist(*MON, 15, 30), 99, 99, 99, 99, 7),  # close print    -> ignored
        bar(ist(*MON, 18, 15), 99, 99, 99, 99, 7),  # evening Muhurat -> ignored
    ]
    out = resample(src, "5m")
    assert [hhmm(c) for c in out] == ["09:15", "15:25"]
    assert out[1] == bar(ist(*MON, 15, 25), 20, 21, 19, 20.5, 1)
    daily = resample(src, "1D")
    assert len(daily) == 1 and ohlcv(daily[0]) == (10, 21, 9, 20.5, 2)


def test_short_session_keeps_0915_anchoring() -> None:
    # Generic short session 13:45..14:40 in 1m bars (all inside 09:15-15:30),
    # NOT flagged as Muhurat -> still anchored to 09:15
    t0 = ist(*MON, 13, 45)
    src = [bar(t0 + 60 * i, 100 + i, 100 + i, 100 + i, 100 + i, 1) for i in range(56)]
    out = resample(src, "1h")
    assert [hhmm(c) for c in out] == ["13:15", "14:15"]
    assert ohlcv(out[0]) == (100, 129, 100, 129, 30)   # 13:45..14:14 -> 30 bars
    assert ohlcv(out[1]) == (130, 155, 130, 155, 26)   # 14:15..14:40 -> 26 bars


# --------------------------------------------------------------------------- gaps / holidays
def test_missing_minute_inside_bucket_uses_available_bars() -> None:
    t = ist(*MON, 9, 15)
    src = [
        bar(t, 10, 12, 9, 11, 1),
        # 09:16 missing
        bar(t + 120, 11, 13, 10, 12, 2),
    ]
    out = resample(src, "3m")
    assert out == [bar(t, 10, 13, 9, 12, 3)]


def test_fully_missing_bucket_creates_no_fake_candle() -> None:
    src = [
        bar(ist(*MON, 9, 15), 1, 2, 0, 1, 1),   # 1h bucket 09:15
        bar(ist(*MON, 11, 20), 5, 6, 4, 5, 1),  # 1h bucket 11:15  (10:15 is empty)
    ]
    out = resample(src, "1h")
    assert [hhmm(c) for c in out] == ["09:15", "11:15"]
    out5 = resample(src, "5m")
    assert [hhmm(c) for c in out5] == ["09:15", "11:20"]


def test_holiday_and_weekend_produce_no_daily_candles() -> None:
    # Mon 01-01 and Tue 01-02 trade, Wed 01-03 is a holiday, Thu 01-04 trades,
    # Sat/Sun absent.
    src: list[Candle] = []
    src += session_1m(2024, 1, 1, base=100)
    src += session_1m(2024, 1, 2, base=200)
    src += session_1m(2024, 1, 4, base=400)
    out = resample(src, "1D")
    assert [datetime.fromtimestamp(c["time"], IST).strftime("%a %d") for c in out] == [
        "Mon 01", "Tue 02", "Thu 04",
    ]  # fmt: skip


# --------------------------------------------------------------------------- weekly
def test_week_starts_on_first_trading_day_when_monday_is_holiday() -> None:
    # week 1: Mon 01-01 holiday-closed (no bars); Tue 02, Wed 03, Fri 05 trade (Thu closed)
    # week 2: Mon 01-08 trades
    src: list[Candle] = []
    src += session_1m(2024, 1, 2, base=200)
    src += session_1m(2024, 1, 3, base=300)
    src += session_1m(2024, 1, 5, base=500)
    src += session_1m(2024, 1, 8, base=100)
    out = resample(src, "1W")
    assert len(out) == 2
    assert out[0]["time"] == ist(2024, 1, 2, 9, 15)   # Tuesday, not Monday
    assert ohlcv(out[0]) == (200, 876, 199, 875, 11250)
    assert out[1]["time"] == ist(2024, 1, 8, 9, 15)   # next Monday
    assert ohlcv(out[1]) == (100, 476, 99, 475, 3750)


def test_friday_and_next_monday_are_different_weeks() -> None:
    src = [
        bar(ist(2024, 1, 5, 9, 15), 1, 2, 0, 1, 1),   # Fri
        bar(ist(2024, 1, 8, 9, 15), 3, 4, 2, 3, 1),   # Mon (next week)
    ]
    out = resample(src, "1W")
    assert [c["time"] for c in out] == [ist(2024, 1, 5, 9, 15), ist(2024, 1, 8, 9, 15)]


def test_saturday_special_session_belongs_to_that_weeks_candle() -> None:
    # Only Fri 2024-01-19 and a Saturday special session 2024-01-20 traded that week.
    src = [
        bar(ist(2024, 1, 19, 9, 15), 10, 12, 9, 11, 1),
        bar(ist(2024, 1, 20, 9, 15), 11, 15, 8, 14, 2),
    ]
    out = resample(src, "1W")
    assert out == [bar(ist(2024, 1, 19, 9, 15), 10, 15, 8, 14, 3)]
    daily = resample(src, "1D")
    assert len(daily) == 2


def test_sunday_budget_session_2026_02_01_joins_week_of_mon_2026_01_26() -> None:
    # Mon 2026-01-26 is a holiday (no bars). Mon-Sun weeks => Sunday 2026-02-01
    # belongs to the week that STARTED Mon 2026-01-26. (Rule to be confirmed
    # against TradingView by the user.)
    src = [
        bar(ist(2026, 1, 27, 9, 15), 10, 12, 9, 11, 1),     # Tue
        bar(ist(2026, 1, 30, 9, 15), 15, 16, 14, 15.5, 2),  # Fri
        bar(ist(2026, 2, 1, 9, 15), 20, 25, 18, 22, 3),     # Sun (Budget)
        bar(ist(2026, 2, 2, 9, 15), 30, 31, 29, 30.5, 4),   # Mon -> next week
    ]
    weekly = resample(src, "1W")
    assert weekly == [
        bar(ist(2026, 1, 27, 9, 15), 10, 25, 9, 22, 6),  # Tue..Sun
        bar(ist(2026, 2, 2, 9, 15), 30, 31, 29, 30.5, 4),
    ]
    assert len(resample(src, "1D")) == 4


# --------------------------------------------------------------------------- Muhurat anchoring
MUHURAT_EVENING = date(2024, 11, 1)  # Friday, bars 18:00..18:55


def muhurat_evening_1m() -> list[Candle]:
    t0 = ist(2024, 11, 1, 18, 0)
    return [bar(t0 + 60 * i, 100 + i, 100 + i, 100 + i, 100 + i, 1) for i in range(56)]


def test_muhurat_evening_bars_are_ignored_unless_date_is_anchored() -> None:
    assert resample(muhurat_evening_1m(), "1h") == []


def test_anchored_date_keeps_evening_bars_and_anchors_to_first_bar() -> None:
    out = resample(
        muhurat_evening_1m(), "30m", anchor_to_first_bar_dates=frozenset({MUHURAT_EVENING})
    )
    assert [hhmm(c) for c in out] == ["18:00", "18:30"]
    assert ohlcv(out[0]) == (100, 129, 100, 129, 30)
    assert ohlcv(out[1]) == (130, 155, 130, 155, 26)


def test_anchored_date_afternoon_muhurat_anchors_to_its_own_open() -> None:
    # 2025-10-21 Muhurat 13:45..14:40. Anchored => 1h buckets 13:45 (all 56 bars).
    t0 = ist(2025, 10, 21, 13, 45)
    src = [bar(t0 + 60 * i, 100 + i, 100 + i, 100 + i, 100 + i, 1) for i in range(56)]
    out = resample(src, "1h", anchor_to_first_bar_dates=frozenset({date(2025, 10, 21)}))
    assert [hhmm(c) for c in out] == ["13:45"]
    assert ohlcv(out[0]) == (100, 155, 100, 155, 56)


def test_anchoring_does_not_affect_other_dates() -> None:
    src = session_1m(2024, 10, 31, base=100) + muhurat_evening_1m()  # Thu normal + Fri Muhurat
    out = resample(src, "1h", anchor_to_first_bar_dates=frozenset({MUHURAT_EVENING}))
    assert [hhmm(c) for c in out] == [
        "09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15",  # Thursday, 09:15 grid
        "18:00",  # Friday Muhurat, own grid
    ]  # fmt: skip
    assert ohlcv(out[0]) == (100, 161, 99, 160, 600)


def test_muhurat_day_in_daily_and_weekly() -> None:
    # Mon 2024-10-28 normal session + Fri 2024-11-01 evening Muhurat, same Mon-Sun week
    src = session_1m(2024, 10, 28, base=100) + muhurat_evening_1m()
    anchored = frozenset({MUHURAT_EVENING})

    daily = resample(src, "1D", anchor_to_first_bar_dates=anchored)
    assert [hhmm(c) for c in daily] == ["09:15", "18:00"]
    assert ohlcv(daily[1]) == (100, 155, 100, 155, 56)

    weekly = resample(src, "1W", anchor_to_first_bar_dates=anchored)
    assert len(weekly) == 1
    assert weekly[0]["time"] == ist(2024, 10, 28, 9, 15)
    assert ohlcv(weekly[0]) == (100, 476, 99, 155, 3750 + 56)  # close = last (Muhurat) bar


# --------------------------------------------------------------------------- source size
def test_5m_source_to_15m_and_1h() -> None:
    t = ist(*MON, 9, 15)
    src = [bar(t + 300 * i, 100 + i, 105 + i, 99 + i, 101 + i, 5) for i in range(3)]
    out = resample(src, "15m", source_minutes=5)
    assert out == [bar(t, 100, 107, 99, 103, 15)]
    assert len(resample(src, "1h", source_minutes=5)) == 1


def test_5m_source_to_1d_and_1w_work() -> None:
    t = ist(*MON, 9, 15)
    src = [bar(t, 1, 2, 0, 1, 1), bar(t + 300, 1, 3, 1, 2, 1)]
    assert len(resample(src, "1D", source_minutes=5)) == 1
    assert len(resample(src, "1W", source_minutes=5)) == 1


@pytest.mark.parametrize("tf", ["1m", "3m"])
def test_5m_source_cannot_make_finer_or_non_multiple_timeframes(tf: str) -> None:
    t = ist(*MON, 9, 15)
    with pytest.raises(ValueError):
        resample([bar(t, 1, 1, 1, 1)], tf, source_minutes=5)


@pytest.mark.parametrize("tf", ["2m", "4h", "1M", "", "5"])
def test_unsupported_timeframe_raises(tf: str) -> None:
    with pytest.raises(ValueError):
        resample([], tf)


def test_available_timeframes_by_source_size() -> None:
    assert available_timeframes(1) == TIMEFRAMES
    assert available_timeframes(5) == ("5m", "15m", "30m", "1h", "1D", "1W")
    assert available_timeframes(3) == ("3m", "15m", "30m", "1h", "1D", "1W")
