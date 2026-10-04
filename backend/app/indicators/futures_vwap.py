"""VWAP of the Nifty index weighted by Nifty futures volume (Phase 2c).

The line is on the index price scale. Each IST session starts again at zero.

    VWAP = sum(index typical price × futures 1m volume) / sum(futures 1m volume)

The weight for a minute is the 1m volume of one futures contract:

* the current-month future (the monthly expiry on or after the session, from the expiry calendar);
* from `roll_days` trading days before that expiry (default 2), the next month;
* or, when `roll_on_volume` is on, the next month from the first minute of the session where its
  cumulative volume exceeds the current month's. That choice stays for the rest of the session.
  The decision at a minute uses only volume through that minute.

A minute with no futures volume keeps the previous VWAP. Before the day's first positive weight
the value is empty. Higher-timeframe bars show the VWAP of the last 1m bar inside them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.expiry import ExpiryCalendar, load_default_calendar
from app.data.history import day_start_ts
from app.data.service import get_candles
from app.data.store import CandleStore
from app.indicators.basic import source_series
from app.indicators.frame import candles_to_frame
from app.indicators.volume import DAY_S, IST_OFFSET_S
from app.upstox.instruments import NIFTY_INDEX_KEY

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_SYMBOL = "NIFTY50"
HISTORY_FROM = date(2024, 10, 1)


def ist_day(ts: int) -> date:
    return datetime.fromtimestamp(int(ts), IST).date()


def roll_start(expiry: date, roll_days: int, is_trading_day: Callable[[date], bool]) -> date:
    """First session that uses the next month: `roll_days` trading days before `expiry`.

    `roll_days` 0 is the expiry session itself. The walk counts trading days only, so a holiday
    between the roll and the expiry pushes the roll earlier. `expiry` is the calendar's shifted
    date (the day the contract actually expires), not the nominal weekday.
    """
    if roll_days < 0:
        raise ValueError("roll_days must be >= 0")
    day = expiry
    left = roll_days
    while left > 0:
        day -= timedelta(days=1)
        if is_trading_day(day):
            left -= 1
    return day


def session_vwap(df: pd.DataFrame, weights: np.ndarray, source: str = "hlc3") -> pd.Series:
    """cumulative(source × weight) / cumulative(weight), reset at each IST midnight.

    A zero or missing weight adds nothing, so the value stays where it was. Empty until the
    day's cumulative weight is positive.
    """
    src = source_series(df, source).to_numpy(dtype=float)
    w = np.asarray(weights, dtype=float)
    if len(w) != len(df):
        raise ValueError(f"fut_volume length {len(w)} does not match {len(df)} bars")
    w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
    day = (df["time"].to_numpy(dtype="int64") + IST_OFFSET_S) // DAY_S
    frame = pd.DataFrame({"day": day, "pv": src * w, "v": w})
    cum = frame.groupby("day", sort=False)[["pv", "v"]].cumsum()
    cum_v = cum["v"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(cum_v > 0, cum["pv"].to_numpy() / cum_v, np.nan)
    return pd.Series(out, index=df.index)


def project_to_bars(
    minute_times: np.ndarray, values: np.ndarray, bar_times: np.ndarray, bar_seconds: int
) -> np.ndarray:
    """The minute value at the last minute that starts inside each bar; empty when the bar has none."""
    out = np.full(len(bar_times), np.nan)
    if len(minute_times) == 0 or len(bar_times) == 0:
        return out
    idx = np.searchsorted(minute_times, np.asarray(bar_times) + bar_seconds, side="left") - 1
    ok = idx >= 0
    picked = minute_times[np.clip(idx, 0, None)]
    ok &= picked >= bar_times
    out[ok] = values[idx[ok]]
    return out


def active_volumes(
    times: np.ndarray,
    volume_by_expiry: Mapping[date, Mapping[int, float]],
    *,
    front_and_next: Callable[[date], tuple[date, date]],
    is_trading_day: Callable[[date], bool],
    roll_days: int,
    roll_on_volume: bool,
) -> np.ndarray:
    """Per-minute futures volume of the contract in force. `times` must be ascending."""
    out = np.zeros(len(times), dtype=float)
    i = 0
    n = len(times)
    while i < n:
        day = ist_day(int(times[i]))
        front, nxt = front_and_next(day)
        sticky = day >= roll_start(front, roll_days, is_trading_day)
        cum_current = 0.0
        cum_next = 0.0
        current = volume_by_expiry.get(front, {})
        following = volume_by_expiry.get(nxt, {})
        while i < n and ist_day(int(times[i])) == day:
            t = int(times[i])
            vc = float(current.get(t, 0.0) or 0.0)
            vn = float(following.get(t, 0.0) or 0.0)
            cum_current += vc
            cum_next += vn
            if roll_on_volume and not sticky and cum_next > cum_current:
                sticky = True
            out[i] = vn if sticky else vc
            i += 1
    return out


def calendar_front_and_next(calendar: ExpiryCalendar, day: date) -> tuple[date, date]:
    front = calendar.next_expiry(day, "monthly")
    nxt = calendar.next_expiry(front.date, "monthly", skip_expiry_day=True)
    return front.date, nxt.date


def is_nifty_future(instrument: Mapping[str, Any]) -> bool:
    kind = instrument.get("kind")
    typ = instrument.get("instrument_type")
    if kind != "future" and typ != "FUT":
        return False
    underlying = instrument.get("underlying_key")
    name = str(instrument.get("name") or "")
    symbol = str(instrument.get("symbol") or "")
    return underlying == NIFTY_INDEX_KEY or name == "NIFTY" or symbol.startswith("NIFTY FUT")


def load_future_volumes(
    store: CandleStore,
    from_time: int,
    to_time: int | None,
    session_types: Sequence[str],
    cursor: int | None = None,
) -> dict[date, dict[int, float]]:
    """1m volume of every stored Nifty future, keyed by its expiry date, inside the time range."""
    out: dict[date, dict[int, float]] = {}
    end = to_time if cursor is None else (to_time if to_time is not None and to_time < cursor else cursor)
    for symbol in store.symbols():
        instrument = store.meta(symbol).instrument
        if not is_nifty_future(instrument):
            continue
        raw = instrument.get("expiry")
        if not isinstance(raw, str):
            continue
        try:
            expiry = date.fromisoformat(raw)
        except ValueError:
            continue
        candles = get_candles(
            store, symbol, "1m", from_time=from_time, to_time=end, session_types=session_types, cursor=cursor
        ).candles
        bucket = out.setdefault(expiry, {})
        for candle in candles:
            bucket[int(candle["time"])] = float(candle["volume"])
    return out


def values_for_chart(
    store: CandleStore,
    chart_candles: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    session_types: Sequence[str],
    bar_seconds: int,
    *,
    to_time: int | None,
    cursor: int | None,
    calendar: ExpiryCalendar | None = None,
) -> np.ndarray:
    """Session VWAP aligned to `chart_candles` (warm-up bars included)."""
    if not chart_candles:
        return np.zeros(0, dtype=float)
    calendar = calendar or load_default_calendar()
    start = day_start_ts(ist_day(int(chart_candles[0]["time"])))
    # A chart bar's start is the API bound, but its VWAP uses every 1m bar inside it.
    # `chart_candles` is already clipped at the replay cursor, so this does not read a later bar.
    last_minute = int(chart_candles[-1]["time"]) + bar_seconds - 60
    if to_time is not None:
        last_minute = min(last_minute, to_time + bar_seconds - 60)
    # A replay cursor is the last source minute that may be seen. Minutes later in the
    # same higher-timeframe bar are still in the future.
    if cursor is not None:
        last_minute = min(last_minute, int(cursor))
    minutes = get_candles(
        store, NIFTY_SYMBOL, "1m",
        from_time=start, to_time=last_minute, session_types=session_types, cursor=cursor,
    ).candles
    frame = candles_to_frame(minutes)
    if frame.empty:
        return np.full(len(chart_candles), np.nan)
    volumes = load_future_volumes(store, start, last_minute, session_types, cursor=cursor)
    weights = active_volumes(
        frame["time"].to_numpy(),
        volumes,
        front_and_next=lambda day: calendar_front_and_next(calendar, day),
        is_trading_day=calendar.is_trading_day,
        roll_days=int(params["roll_days"]),
        roll_on_volume=params["roll_on_volume"] == "on",
    )
    minute_vwap = session_vwap(frame, weights, str(params["source"])).to_numpy(dtype=float)
    bar_times = np.array([int(c["time"]) for c in chart_candles], dtype="int64")
    return project_to_bars(frame["time"].to_numpy(dtype="int64"), minute_vwap, bar_times, bar_seconds)
