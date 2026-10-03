"""Candle contract (see PROJECT_PLAN.md section 4). Do not change the shape."""

from typing import TypedDict


class Candle(TypedDict):
    time: int  # unix seconds (UTC epoch); the bar START time. IST = UTC+5:30
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float | None
