"""Point-in-time daily filters. A day uses only bars on or before that day.

Listed means the first stored daily bar is at least a year earlier. History starts
in 2015, so that is not the exchange listing date.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

TURNOVER_MIN = 50_000_000.0  # ₹5 crore
PRICE_MIN = 50.0
LISTED_DAYS = 365
TURNOVER_WINDOW = 20


def apply_filters(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per kept session, in time order. Later rows do not change earlier flags."""
    if frame.empty:
        return pd.DataFrame(columns=[
            "time", "date", "close", "turnover", "turnover_20d", "listed_ok", "price_ok", "turnover_ok", "passes",
        ])
    work = frame.sort_values("time")
    keep = work["session_type"].isin(("normal", "weekend_full"))
    work = work.loc[keep].copy()
    if work.empty:
        return pd.DataFrame(columns=[
            "time", "date", "close", "turnover", "turnover_20d", "listed_ok", "price_ok", "turnover_ok", "passes",
        ])
    work["date"] = pd.to_datetime(work["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    work["turnover"] = work["close"].astype(float) * work["volume"].astype(float)
    work["turnover_20d"] = work["turnover"].rolling(TURNOVER_WINDOW, min_periods=TURNOVER_WINDOW).mean()
    first = work["date"].iloc[0]
    listed_on = first + timedelta(days=LISTED_DAYS)
    work["listed_ok"] = work["date"].map(lambda day: day >= listed_on)
    work["price_ok"] = work["close"].astype(float) >= PRICE_MIN
    work["turnover_ok"] = work["turnover_20d"] >= TURNOVER_MIN
    work["passes"] = work["listed_ok"] & work["price_ok"] & work["turnover_ok"]
    return work[[
        "time", "date", "close", "turnover", "turnover_20d", "listed_ok", "price_ok", "turnover_ok", "passes",
    ]].reset_index(drop=True)


def passes_on(frame: pd.DataFrame, day: date) -> bool | None:
    """The flag for `day`, or None when that session is not in the frame."""
    flags = apply_filters(frame)
    hit = flags.loc[flags["date"] == day]
    if hit.empty:
        return None
    return bool(hit.iloc[-1]["passes"])
