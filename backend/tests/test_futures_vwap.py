"""Nifty VWAP weighted by futures volume: the formula, the roll, and no look-ahead."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.backtest.expiry import load_default_calendar
from app.data.history import write_meta, SymbolMeta
from app.data.importer import build_frame, write_parquet
from app.data.store import CandleStore, set_overlay
from app.indicators.futures_vwap import (
    active_volumes,
    ist_day,
    project_to_bars,
    roll_start,
    session_vwap,
)
from app.indicators.registry import IndicatorSpec, compute, validate_params
from app.indicators.service import compute_indicators
from app.live.service import symbols_dirtied_by
from tests.conftest import ist_ts

CAL = load_default_calendar()


def _pair(day: date) -> tuple[date, date]:
    front = CAL.next_expiry(day, "monthly").date
    nxt = CAL.next_expiry(front, "monthly", skip_expiry_day=True).date
    return front, nxt


def test_roll_starts_two_trading_days_before_the_october_2026_expiry() -> None:
    assert CAL.monthly_of(2026, 10).date == date(2026, 10, 27)
    assert roll_start(date(2026, 10, 27), 2, CAL.is_trading_day) == date(2026, 10, 23)
    # the session before the roll is still the expiring month; the roll day is the next month
    assert date(2026, 10, 22) < roll_start(date(2026, 10, 27), 2, CAL.is_trading_day)
    assert date(2026, 10, 27) >= roll_start(date(2026, 10, 27), 2, CAL.is_trading_day)


def test_roll_uses_the_shifted_expiry_and_skips_holidays() -> None:
    """March 2026's monthly expiry moved off Tuesday 31st, and 26 March is a holiday, so the roll is the 25th."""
    assert CAL.monthly_of(2026, 3).date == date(2026, 3, 30)
    assert CAL.is_trading_day(date(2026, 3, 26)) is False
    assert roll_start(date(2026, 3, 30), 2, CAL.is_trading_day) == date(2026, 3, 25)


def test_roll_days_zero_is_the_expiry_session() -> None:
    assert roll_start(date(2026, 10, 27), 0, CAL.is_trading_day) == date(2026, 10, 27)


def _minute_frame(start: int, rows: list[tuple[float, float]]) -> pd.DataFrame:
    """rows of (typical price, weight). high=low=close=typical so hlc3 is that price."""
    return pd.DataFrame(
        {
            "time": [start + 60 * i for i in range(len(rows))],
            "open": [p for p, _ in rows],
            "high": [p for p, _ in rows],
            "low": [p for p, _ in rows],
            "close": [p for p, _ in rows],
            "volume": [0.0] * len(rows),
        }
    )


def test_session_vwap_is_typical_price_times_futures_volume_and_carries_gaps() -> None:
    start = ist_ts(2026, 10, 1, 9, 15)
    df = _minute_frame(start, [(10, 1), (12, 1), (99, 0), (16, 2), (18, 1)])
    weights = np.array([1, 1, 0, 2, 1], dtype=float)
    got = session_vwap(df, weights).to_numpy()
    # 10, (10+12)/2 = 11, carry 11, (10+12+32)/4 = 13.5, (54+18)/5 = 14.4
    np.testing.assert_allclose(got, [10, 11, 11, 13.5, 14.4])


def test_session_vwap_resets_the_next_ist_day() -> None:
    start = ist_ts(2026, 10, 1, 15, 29)
    nxt = ist_ts(2026, 10, 2, 9, 15)
    df = _minute_frame(start, [(10, 1)])
    df = pd.concat(
        [df, _minute_frame(nxt, [(40, 1)])],
        ignore_index=True,
    )
    got = session_vwap(df, np.array([2, 4], dtype=float)).to_numpy()
    np.testing.assert_allclose(got, [10, 40])


def test_a_five_minute_bar_shows_the_last_minute_of_the_session_vwap() -> None:
    start = ist_ts(2026, 10, 1, 9, 15)
    df = _minute_frame(start, [(10, 1), (12, 1), (99, 0), (16, 2), (18, 1)])
    values = session_vwap(df, np.array([1, 1, 0, 2, 1], dtype=float)).to_numpy()
    projected = project_to_bars(df["time"].to_numpy(), values, np.array([start]), 300)
    assert projected[0] == pytest.approx(14.4)


def test_volume_roll_is_sticky_for_the_session_and_does_not_look_ahead() -> None:
    """Next month takes over the minute it first leads, and a later spike cannot change earlier minutes."""
    start = ist_ts(2026, 10, 22, 9, 15)  # before the 23 Oct roll, so October is still current
    assert ist_day(start) < roll_start(date(2026, 10, 27), 2, CAL.is_trading_day)
    times = np.array([start + 60 * i for i in range(4)])
    front, nxt = _pair(date(2026, 10, 22))
    october = {int(times[0]): 5.0, int(times[1]): 5.0, int(times[2]): 1.0, int(times[3]): 100.0}
    november = {int(times[0]): 1.0, int(times[1]): 1.0, int(times[2]): 20.0, int(times[3]): 0.0}
    full = active_volumes(
        times, {front: october, nxt: november},
        front_and_next=_pair, is_trading_day=CAL.is_trading_day, roll_days=2, roll_on_volume=True,
    )
    # minute 2: cum next 22 > cum current 11, so that minute and the rest of the day use November
    np.testing.assert_array_equal(full, [5, 5, 20, 0])
    earlier = active_volumes(
        times[:2], {front: october, nxt: november},
        front_and_next=_pair, is_trading_day=CAL.is_trading_day, roll_days=2, roll_on_volume=True,
    )
    np.testing.assert_array_equal(earlier, full[:2])


def test_calendar_roll_uses_the_next_month_for_the_whole_session() -> None:
    start = ist_ts(2026, 10, 23, 9, 15)
    times = np.array([start, start + 60])
    front, nxt = _pair(date(2026, 10, 23))
    volumes = {
        front: {int(times[0]): 50.0, int(times[1]): 50.0},
        nxt: {int(times[0]): 3.0, int(times[1]): 4.0},
    }
    got = active_volumes(
        times, volumes,
        front_and_next=_pair, is_trading_day=CAL.is_trading_day, roll_days=2, roll_on_volume=True,
    )
    np.testing.assert_array_equal(got, [3, 4])


def test_volume_roll_off_stays_on_the_current_month() -> None:
    start = ist_ts(2026, 10, 22, 9, 15)
    times = np.array([start])
    front, nxt = _pair(date(2026, 10, 22))
    volumes = {front: {int(times[0]): 1.0}, nxt: {int(times[0]): 100.0}}
    got = active_volumes(
        times, volumes,
        front_and_next=_pair, is_trading_day=CAL.is_trading_day, roll_days=2, roll_on_volume=False,
    )
    assert got[0] == 1


def _write_minute(store_dir: Path, symbol: str, bars: list[dict], instrument: dict) -> None:
    df, _ = build_frame(bars, bar_minutes=1, keep_oi=instrument.get("kind") == "future")
    write_parquet(df, store_dir / symbol / "1m.parquet")
    write_meta(store_dir / symbol, SymbolMeta(instrument=instrument, covered=[(date(2026, 10, 1), date(2026, 10, 1))]))


def test_chart_5m_matches_the_hand_computed_minute_vwap_and_a_later_bar_cannot_change_it(tmp_path: Path) -> None:
    start = ist_ts(2026, 10, 1, 9, 15)
    index_bars = []
    future_bars = []
    prices = [10, 12, 99, 16, 18, 20]
    weights = [1, 1, 0, 2, 1, 5]
    for i, (price, weight) in enumerate(zip(prices, weights, strict=True)):
        t = (start + 60 * i) * 1000
        index_bars.append({"t": t, "open": price, "high": price, "low": price, "close": price, "volume": 0})
        if weight:
            future_bars.append({"t": t, "open": 1, "high": 1, "low": 1, "close": 1, "volume": weight, "oi": 1})
    root = tmp_path / "candles"
    _write_minute(root, "NIFTY50", index_bars, {"instrument_key": "NSE_INDEX|Nifty 50", "kind": "index", "name": "NIFTY"})
    _write_minute(
        root, "NSE_FO_OCT", future_bars,
        {
            "instrument_key": "NSE_FO|1", "symbol": "NIFTY FUT 27 OCT 26", "name": "NIFTY",
            "kind": "future", "instrument_type": "FUT", "expiry": "2026-10-27",
            "underlying_key": "NSE_INDEX|Nifty 50",
        },
    )
    store = CandleStore(root)
    spec = IndicatorSpec("v", "vwap_fut", validate_params("vwap_fut", {}))
    sessions = ("normal", "special_short")
    first = compute_indicators(
        store, "NIFTY50", "5m", [spec], from_time=start, to_time=start, session_types=sessions,
    )
    assert first.indicators[0].outputs["vwap"] == [pytest.approx(14.4)]
    # the 09:20 bar exists; asking only for 09:15 must not use the 09:20 weight
    both = compute_indicators(
        store, "NIFTY50", "5m", [spec], from_time=start, to_time=start + 300, session_types=sessions,
    )
    assert both.indicators[0].outputs["vwap"][0] == pytest.approx(14.4)
    assert len(both.indicators[0].outputs["vwap"]) == 2


def test_replay_cursor_ignores_later_minutes_inside_the_bar(tmp_path: Path) -> None:
    """A cursor on the first minute of a 5m bar must not see the other four minutes."""
    start = ist_ts(2026, 10, 1, 9, 15)
    index_bars = []
    future_bars = []
    for i, (price, weight) in enumerate(zip([10, 12, 99, 16, 18], [1, 1, 0, 2, 1], strict=True)):
        t = (start + 60 * i) * 1000
        index_bars.append({"t": t, "open": price, "high": price, "low": price, "close": price, "volume": 0})
        if weight:
            future_bars.append({"t": t, "open": 1, "high": 1, "low": 1, "close": 1, "volume": weight, "oi": 1})
    root = tmp_path / "candles"
    _write_minute(root, "NIFTY50", index_bars, {"instrument_key": "NSE_INDEX|Nifty 50", "kind": "index", "name": "NIFTY"})
    _write_minute(
        root, "NSE_FO_OCT", future_bars,
        {
            "instrument_key": "NSE_FO|1", "symbol": "NIFTY FUT 27 OCT 26", "name": "NIFTY",
            "kind": "future", "instrument_type": "FUT", "expiry": "2026-10-27",
            "underlying_key": "NSE_INDEX|Nifty 50",
        },
    )
    store = CandleStore(root)
    spec = IndicatorSpec("v", "vwap_fut", validate_params("vwap_fut", {}))
    sessions = ("normal", "special_short")
    opened = compute_indicators(
        store, "NIFTY50", "5m", [spec], from_time=start, to_time=start, session_types=sessions, cursor=start,
    )
    assert opened.indicators[0].outputs["vwap"] == [pytest.approx(10)]
    closed = compute_indicators(
        store, "NIFTY50", "5m", [spec],
        from_time=start, to_time=start, session_types=sessions, cursor=start + 240,
    )
    assert closed.indicators[0].outputs["vwap"] == [pytest.approx(14.4)]


def test_live_overlay_volume_changes_the_vwap(tmp_path: Path) -> None:
    start = ist_ts(2026, 10, 1, 9, 15)
    t_ms = start * 1000
    root = tmp_path / "candles"
    _write_minute(
        root, "NIFTY50",
        [{"t": t_ms, "open": 10, "high": 10, "low": 10, "close": 10, "volume": 0}],
        {"kind": "index", "name": "NIFTY"},
    )
    _write_minute(
        root, "NSE_FO_OCT",
        [{"t": t_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "oi": 1}],
        {"kind": "future", "instrument_type": "FUT", "expiry": "2026-10-27", "underlying_key": "NSE_INDEX|Nifty 50", "name": "NIFTY"},
    )
    store = CandleStore(root)
    spec = IndicatorSpec("v", "vwap_fut", validate_params("vwap_fut", {}))
    sessions = ("normal", "special_short")
    before = compute_indicators(
        store, "NIFTY50", "1m", [spec], from_time=start, to_time=start, session_types=sessions,
    )
    assert before.indicators[0].outputs["vwap"] == [pytest.approx(10)]
    set_overlay(lambda symbol: [
        {"time": start, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "oi": 1, "session_type": "normal"},
        {"time": start + 60, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 3, "oi": 1, "session_type": "normal"},
    ] if symbol == "NSE_FO_OCT" else [])
    try:
        # index overlay minute so the 09:16 bar exists on the index too
        set_overlay(lambda symbol: (
            [{"time": start + 60, "open": 16, "high": 16, "low": 16, "close": 16, "volume": 0, "oi": None, "session_type": "normal"}]
            if symbol == "NIFTY50"
            else [{"time": start + 60, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 3, "oi": 1, "session_type": "normal"}]
            if symbol == "NSE_FO_OCT"
            else []
        ))
        after = compute_indicators(
            store, "NIFTY50", "1m", [spec], from_time=start, to_time=start + 60, session_types=sessions,
        )
    finally:
        set_overlay(None)
    # 09:15 weight 1 at 10, 09:16 weight 3 at 16 → (10 + 48) / 4 = 14.5
    assert after.indicators[0].outputs["vwap"][-1] == pytest.approx(14.5)


def test_compute_with_fut_volume_matches_session_vwap() -> None:
    start = ist_ts(2026, 10, 1, 9, 15)
    df = _minute_frame(start, [(10, 1), (20, 1)])
    df["fut_volume"] = [1.0, 3.0]
    params = validate_params("vwap_fut", {})
    got = compute(df, "vwap_fut", params)["vwap"]
    np.testing.assert_allclose(got, [10, 17.5])


def test_a_future_tick_dirties_the_nifty_chart() -> None:
    assert symbols_dirtied_by({"NSE_FO_48704"}, future_dir="NSE_FO_48704") == {"NSE_FO_48704", "NIFTY50"}
    assert symbols_dirtied_by({"NIFTY50"}, future_dir="NSE_FO_48704") == {"NIFTY50"}


def test_expired_future_volume_is_fetched_once_and_not_again(tmp_path: Path) -> None:
    from app.data.futures_volume import sync_expired_future
    from app.data.history import read_parquet, symbol_dir_name

    class Fake:
        def __init__(self) -> None:
            self.calls: list[tuple[str, date, date]] = []

        def expired_historical_candles(self, key: str, start: date, end: date, interval: str = "1minute"):
            assert interval == "1minute"
            self.calls.append((key, start, end))
            return [["2024-10-03T09:15:00+05:30", 10, 11, 9, 10.5, 100, 1]]

    key = "NSE_FO|9|31-10-2024"
    first = Fake()
    sync_expired_future(
        first, tmp_path, expiry=date(2024, 10, 31), expired_key=key,
        symbol="NIFTY FUT 31 OCT 24", now_date=date(2026, 10, 4),
    )
    assert first.calls
    assert first.calls[0][0] == key
    stored = read_parquet(tmp_path / symbol_dir_name(key) / "1m.parquet")
    assert int(stored["volume"].iloc[0]) == 100
    second = Fake()
    sync_expired_future(
        second, tmp_path, expiry=date(2024, 10, 31), expired_key=key,
        symbol="NIFTY FUT 31 OCT 24", now_date=date(2026, 10, 4),
    )
    assert second.calls == []


def test_an_expired_future_with_no_candles_does_not_leave_a_parquet(tmp_path: Path) -> None:
    from app.data.futures_volume import sync_expired_future
    from app.data.history import read_meta, symbol_dir_name

    class Empty:
        def expired_historical_candles(self, key: str, start: date, end: date, interval: str = "1minute"):
            assert interval == "1minute"
            return []

    key = "NSE_FO|35005|26-12-2024"
    sync_expired_future(
        Empty(), tmp_path, expiry=date(2024, 12, 26), expired_key=key,
        symbol="NIFTY FUT 26 DEC 24", now_date=date(2026, 10, 4),
    )
    folder = tmp_path / symbol_dir_name(key)
    assert not (folder / "1m.parquet").exists()
    assert list(folder.glob("*.tmp")) == []
    assert read_meta(folder).covered  # the empty range is remembered, so it is not fetched again


def test_registry_rejects_a_bad_roll_setting() -> None:
    with pytest.raises(ValueError):
        validate_params("vwap_fut", {"roll_days": -1})
    with pytest.raises(ValueError):
        validate_params("vwap_fut", {"roll_on_volume": "maybe"})
