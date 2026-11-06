"""Gamma exposure (GEX) of the Nifty option chain. Pure maths: no socket, no clock.

Per strike, in ₹ crore of hedging per 1% move of the index:

    call GEX = +gamma_call · OI_call · S² · 0.01 / 1e7
    put  GEX = −gamma_put  · OI_put  · S² · 0.01 / 1e7

The signs follow the usual dealer convention (dealers long calls, short puts). In Nifty that
assumption is rough: read the result as "pinning vs trending", not as an exact dealer book.

Upstox reports OI in units (quantity), so there is no lot-size multiplier. Upstox rounds gamma
to 4 decimals, which zeroes most strikes, so gamma is recomputed with Black-Scholes from each
leg's IV (calendar time to 15:30 IST on expiry, r = q = 0). The Upstox gamma is the fallback
for a leg with no IV.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.options.time import CALENDAR_MIN_PER_YEAR, calendar_minutes_to_expiry

CRORE = 1e7
#: time-to-expiry floor (15 minutes) so gamma stays finite on expiry afternoon
MIN_YEARS = 15 / CALENDAR_MIN_PER_YEAR
#: the gamma flip is searched within ±FLIP_RANGE of spot
FLIP_RANGE = 0.05
FLIP_STEPS = 401
UNIT = "₹ crore per 1% move"
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def bs_gamma(S: float, K: float, T: float, sigma: float) -> float:
    """Black-Scholes gamma (same for calls and puts). 0 for inputs that have no gamma."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vs
    return math.exp(-0.5 * d1 * d1) * _INV_SQRT_2PI / (S * vs)


@dataclass(frozen=True)
class Leg:
    oi: float
    #: implied volatility as a decimal (0.15), None when Upstox has none
    iv: float | None
    #: the gamma Upstox reported (rounded), the fallback when there is no IV
    gamma_api: float | None


@dataclass(frozen=True)
class StrikeRow:
    strike: float
    call: Leg
    put: Leg


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _leg(raw: Any) -> Leg:
    raw = raw if isinstance(raw, dict) else {}
    market = raw.get("market_data") if isinstance(raw.get("market_data"), dict) else {}
    greeks = raw.get("option_greeks") if isinstance(raw.get("option_greeks"), dict) else {}
    oi = _num(market.get("oi")) or 0.0
    iv = _num(greeks.get("iv"))
    gamma = _num(greeks.get("gamma"))
    return Leg(
        oi=max(oi, 0.0),
        iv=iv / 100.0 if iv is not None and iv > 0 else None,
        gamma_api=gamma if gamma is not None and gamma > 0 else None,
    )


def parse_chain(rows: list[dict[str, Any]]) -> tuple[float | None, list[StrikeRow]]:
    """(spot, strikes ascending) from GET /v2/option/chain rows. Empty legs count as zero OI."""
    spot: float | None = None
    out: list[StrikeRow] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        strike = _num(raw.get("strike_price"))
        if strike is None or strike <= 0:
            continue
        s = _num(raw.get("underlying_spot_price"))
        if spot is None and s is not None and s > 0:
            spot = s
        out.append(StrikeRow(strike, _leg(raw.get("call_options")), _leg(raw.get("put_options"))))
    out.sort(key=lambda r: r.strike)
    return spot, out


def _gamma(leg: Leg, S: float, K: float, T: float) -> float:
    if leg.iv is not None:
        return bs_gamma(S, K, T, leg.iv)
    return leg.gamma_api or 0.0


def strike_gex(row: StrikeRow, S: float, T: float) -> tuple[float, float]:
    """(call GEX, put GEX) at spot `S`, in crore per 1% move. Put GEX is ≤ 0."""
    unit = S * S * 0.01 / CRORE
    call = _gamma(row.call, S, row.strike, T) * row.call.oi * unit
    put = -_gamma(row.put, S, row.strike, T) * row.put.oi * unit
    return call, put


def net_gex_at(rows: list[StrikeRow], S: float, T: float) -> float:
    total = 0.0
    for r in rows:
        c, p = strike_gex(r, S, T)
        total += c + p
    return total


def gamma_flip(rows: list[StrikeRow], spot: float, T: float) -> float | None:
    """Spot level where total net GEX changes sign (nearest to spot), or None if it never does."""
    lo, hi = spot * (1 - FLIP_RANGE), spot * (1 + FLIP_RANGE)
    xs = [lo + (hi - lo) * i / (FLIP_STEPS - 1) for i in range(FLIP_STEPS)]
    ys = [net_gex_at(rows, x, T) for x in xs]
    best: float | None = None
    for i in range(1, len(xs)):
        y0, y1 = ys[i - 1], ys[i]
        if y0 == 0.0 and y1 == 0.0:
            continue
        if (y0 <= 0 < y1) or (y0 >= 0 > y1) or (y0 < 0 <= y1) or (y0 > 0 >= y1):
            x = xs[i - 1] + (xs[i] - xs[i - 1]) * (0 - y0) / (y1 - y0) if y1 != y0 else xs[i]
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    return round(best, 2) if best is not None else None


def compute_gex(rows: list[dict[str, Any]], *, now: int, expiry: date) -> dict[str, Any]:
    """The chart payload: per-strike call/put/net GEX, totals, walls and the gamma flip."""
    spot, parsed = parse_chain(rows)
    base: dict[str, Any] = {"expiry": expiry.isoformat(), "as_of": int(now), "unit": UNIT}
    if spot is None or not parsed:
        return {**base, "available": False, "reason": "option chain is empty", "strikes": []}
    T = max(calendar_minutes_to_expiry(int(now), expiry) / CALENDAR_MIN_PER_YEAR, MIN_YEARS)
    strikes = []
    for r in parsed:
        c, p = strike_gex(r, spot, T)
        if c == 0.0 and p == 0.0:
            continue
        strikes.append({"strike": r.strike, "call": c, "put": p, "net": c + p})
    total_call = sum(s["call"] for s in strikes)
    total_put = sum(s["put"] for s in strikes)
    total_net = total_call + total_put
    calls = [s for s in strikes if s["call"] > 0]
    puts = [s for s in strikes if s["put"] < 0]
    return {
        **base,
        "available": bool(strikes),
        "reason": None if strikes else "no open interest with gamma",
        "spot": spot,
        "strikes": strikes,
        "total_call": total_call,
        "total_put": total_put,
        "total_net": total_net,
        "regime": "positive" if total_net >= 0 else "negative",
        "call_wall": max(calls, key=lambda s: s["call"])["strike"] if calls else None,
        "put_wall": min(puts, key=lambda s: s["put"])["strike"] if puts else None,
        "gamma_flip": gamma_flip(parsed, spot, T) if strikes else None,
    }
