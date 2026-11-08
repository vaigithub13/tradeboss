"""Live Nifty option-chain view: ATM, OI change, S/R, PCR from the full Upstox chain.

Pure functions — no sockets. Totals / PCR use every strike; the table is ±window around ATM.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

from app.options.time import IST

OPEN = time(9, 15)
CLOSE = time(15, 30)
DEFAULT_WINDOW = 15
UNAVAILABLE = "option chain empty"


def format_ist(t: int) -> str:
    """Unix seconds → ISO-8601 with +05:30 (no microseconds)."""
    return datetime.fromtimestamp(int(t), IST).isoformat(timespec="seconds")


def market_status(now: int | float, *, is_session: Callable[[date], bool]) -> str:
    """LIVE only on a regular session day between 09:15 and 15:30 IST inclusive."""
    local = datetime.fromtimestamp(float(now), IST)
    if not is_session(local.date()):
        return "MARKET_CLOSED"
    clock = local.time()
    if OPEN <= clock <= CLOSE:
        return "LIVE"
    return "MARKET_CLOSED"


def atm_strike(spot: float, strikes: list[float]) -> float:
    """Nearest listed strike to spot; a tie takes the lower strike."""
    return min(strikes, key=lambda k: (abs(k - spot), k))


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _leg(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    md = raw.get("market_data") or {}
    greeks = raw.get("option_greeks") or {}
    oi = _f(md.get("oi"))
    prev_oi = _f(md.get("prev_oi"))
    ltp = _f(md.get("ltp"))
    close = _f(md.get("close_price"))
    bid = _f(md.get("bid_price"))
    ask = _f(md.get("ask_price"))
    oi_chg = oi - prev_oi
    oi_chg_pct = (oi_chg / prev_oi * 100.0) if prev_oi else None
    spread_pct = ((ask - bid) / ltp * 100.0) if ltp else None
    return {
        "oi": oi,
        "prev_oi": prev_oi,
        "oi_chg": oi_chg,
        "oi_chg_pct": oi_chg_pct,
        "volume": _f(md.get("volume")),
        "iv": _f(greeks.get("iv")) if greeks.get("iv") is not None else None,
        "ltp": ltp,
        "close": close,
        "price_chg": ltp - close,
        "bid": bid,
        "ask": ask,
        "spread_pct": spread_pct,
        "delta": _f(greeks.get("delta")) if greeks.get("delta") is not None else None,
        "gamma": _f(greeks.get("gamma")) if greeks.get("gamma") is not None else None,
        "theta": _f(greeks.get("theta")) if greeks.get("theta") is not None else None,
        "vega": _f(greeks.get("vega")) if greeks.get("vega") is not None else None,
        "pop": _f(greeks.get("pop")) if greeks.get("pop") is not None else None,
        "bid_qty": _f(md.get("bid_qty")),
        "ask_qty": _f(md.get("ask_qty")),
    }


def _levels(rows: list[dict[str, Any]], spot: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Top 3 call-OI at/above spot (R1..) and put-OI at/below spot (S1..) — from `rows` only.

    Callers must pass the ±window display slice so every R/S tag lands on a visible table row.
    """
    above = sorted(
        (r for r in rows if r["strike"] >= spot),
        key=lambda r: (-r["call"]["oi"], r["strike"]),
    )[:3]
    below = sorted(
        (r for r in rows if r["strike"] <= spot),
        key=lambda r: (-r["put"]["oi"], -r["strike"]),
    )[:3]
    resistance = [{"strike": r["strike"], "oi": r["call"]["oi"], "tag": f"R{i}"} for i, r in enumerate(above, 1)]
    support = [{"strike": r["strike"], "oi": r["put"]["oi"], "tag": f"S{i}"} for i, r in enumerate(below, 1)]
    return resistance, support


def _major_walls(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Single highest Call OI and Put OI strikes across the full chain (may be far from ATM)."""
    if not rows:
        return {"call": None, "put": None}
    call_row = max(rows, key=lambda r: (r["call"]["oi"], -r["strike"]))
    put_row = max(rows, key=lambda r: (r["put"]["oi"], -r["strike"]))
    return {
        "call": {"strike": call_row["strike"], "oi": call_row["call"]["oi"]},
        "put": {"strike": put_row["strike"], "oi": put_row["put"]["oi"]},
    }


def build_chain_view(
    raw_rows: list[dict[str, Any]],
    *,
    now: int | float,
    expiry: str,
    window: int = DEFAULT_WINDOW,
    status: str | None = None,
    is_session: Callable[[date], bool] | None = None,
) -> dict[str, Any]:
    """Shape Upstox chain rows into the Options-tab payload."""
    as_of = int(now)
    as_of_ist = format_ist(as_of)
    if not raw_rows:
        return {
            "available": False,
            "reason": UNAVAILABLE,
            "expiry": expiry,
            "as_of": as_of,
            "as_of_ist": as_of_ist,
            "status": status or "MARKET_CLOSED",
            "rows": [],
            "resistance": [],
            "support": [],
            "major_walls": {"call": None, "put": None},
        }

    parsed: list[dict[str, Any]] = []
    spot = _f(raw_rows[0].get("underlying_spot_price"))
    for raw in raw_rows:
        strike = _f(raw.get("strike_price"))
        if not strike:
            continue
        spot = _f(raw.get("underlying_spot_price"), spot)
        parsed.append(
            {
                "strike": strike,
                "call": _leg(raw.get("call_options") if isinstance(raw.get("call_options"), dict) else None),
                "put": _leg(raw.get("put_options") if isinstance(raw.get("put_options"), dict) else None),
            }
        )
    if not parsed:
        return {
            "available": False,
            "reason": UNAVAILABLE,
            "expiry": expiry,
            "as_of": as_of,
            "as_of_ist": as_of_ist,
            "status": status or "MARKET_CLOSED",
            "rows": [],
            "resistance": [],
            "support": [],
            "major_walls": {"call": None, "put": None},
        }

    parsed.sort(key=lambda r: r["strike"])
    strikes = [r["strike"] for r in parsed]
    atm = atm_strike(spot, strikes)
    total_call = sum(r["call"]["oi"] for r in parsed)
    total_put = sum(r["put"]["oi"] for r in parsed)
    pcr = (total_put / total_call) if total_call else None
    major_walls = _major_walls(parsed)

    atm_idx = strikes.index(atm)
    lo = max(0, atm_idx - window)
    hi = min(len(parsed), atm_idx + window + 1)
    window_rows = parsed[lo:hi]
    # R/S only from the visible ±window so every tag matches a table row
    resistance, support = _levels(window_rows, spot)
    tags = {r["strike"]: r["tag"] for r in resistance}
    for s in support:
        tags[s["strike"]] = s["tag"]

    display = []
    for r in window_rows:
        display.append(
            {
                "strike": r["strike"],
                "call": r["call"],
                "put": r["put"],
                "atm": r["strike"] == atm,
                "tag": tags.get(r["strike"]),
                "call_itm": r["strike"] < spot,
                "put_itm": r["strike"] > spot,
            }
        )

    if status is None:
        if is_session is None:
            status = "MARKET_CLOSED"
        else:
            status = market_status(as_of, is_session=is_session)

    return {
        "available": True,
        "reason": None,
        "expiry": expiry,
        "as_of": as_of,
        "as_of_ist": as_of_ist,
        "status": status,
        "spot": spot,
        "atm": atm,
        "pcr": pcr,
        "total_call_oi": total_call,
        "total_put_oi": total_put,
        "resistance": resistance,
        "support": support,
        "major_walls": major_walls,
        "rows": display,
        # full chain kept for snapshots / later phases (not shown in the ±window table)
        "all_strikes": parsed,
        "max_oi": max((max(r["call"]["oi"], r["put"]["oi"]) for r in window_rows), default=0.0),
    }
