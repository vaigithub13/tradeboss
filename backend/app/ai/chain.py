"""Nearest weekly expiry and the Nifty option chain. The getter is injected; this module opens no socket."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from app.backtest.expiry import ExpiryCalendar, load_default_calendar

NIFTY_CHAIN_KEY = "NSE_INDEX|Nifty 50"
CHAIN_PATH = "/v2/option/chain"
UNAVAILABLE = "option chain unavailable"


def weekly_expiry(on: date, calendar: ExpiryCalendar | None = None) -> str:
    """Nearest weekly expiry on or after `on`, as YYYY-MM-DD.

    On the expiry day the date is that day. A holiday moves the contract to the
    previous trading day, which is what ExpiryCalendar already records.
    """
    cal = calendar or load_default_calendar()
    return cal.next_expiry(on, "weekly").date.isoformat()


def fetch_option_chain(
    get_json: Callable[[str, dict], dict],
    instrument_key: str,
    expiry: str,
) -> dict[str, Any]:
    """ATM row of GET /v2/option/chain. A tie between strikes takes the lower one."""
    payload = get_json(CHAIN_PATH, {"instrument_key": instrument_key, "expiry_date": expiry})
    rows = payload.get("data") or []
    if not rows:
        return {"available": False, "reason": UNAVAILABLE, "expiry": expiry}
    spot = float(rows[0]["underlying_spot_price"])

    def rank(row: dict) -> tuple[float, float]:
        strike = float(row["strike_price"])
        return (abs(strike - spot), strike)

    row = min(rows, key=rank)

    def leg(name: str) -> dict[str, Any]:
        market = (row.get(name) or {}).get("market_data") or {}
        return {
            "ltp": market.get("ltp"),
            "oi": market.get("oi"),
            "bid": market.get("bid_price"),
            "ask": market.get("ask_price"),
        }

    return {
        "available": True,
        "expiry": expiry,
        "atm": float(row["strike_price"]),
        "spot": spot,
        "pcr": row.get("pcr"),
        "call": leg("call_options"),
        "put": leg("put_options"),
    }
