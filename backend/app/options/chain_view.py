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
#: Nifty strike step is 50 → ±2 strikes = ±100 pts around spot
PIVOT_BAND_PTS = 100.0
PIVOT_STRONG = 0.80
UNAVAILABLE = "option chain empty"

PIVOT_READ_ABOVE_CALL = "Above = bullish, short covering risk for call writers"
PIVOT_READ_BELOW_PUT = "Below = bearish, put writers under pressure"
PIVOT_READ_BATTLE = "Battle zone"


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


def _strength(oi: float, peak: float) -> str:
    """Strong ≥ 80% of peak, Medium 50–80%, Weak < 50%."""
    if peak <= 0:
        return "Weak"
    pct = oi / peak * 100.0
    if pct >= 80.0:
        return "Strong"
    if pct >= 50.0:
        return "Medium"
    return "Weak"


def _levels(rows: list[dict[str, Any]], spot: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pick top-3 OI on each side of spot, then number R1/S1 by nearest distance.

    Callers must pass the ±window display slice so every R/S tag lands on a visible table row.
    Strength is vs the largest Call/Put OI on that side within the window. `edge` marks a
    level that sits on the first/last strike of the window (true peak may be outside).
    """
    if not rows:
        return [], []
    edge_strikes = {rows[0]["strike"], rows[-1]["strike"]}
    above_pool = [r for r in rows if r["strike"] >= spot]
    below_pool = [r for r in rows if r["strike"] <= spot]
    call_peak = max((r["call"]["oi"] for r in above_pool), default=0.0)
    put_peak = max((r["put"]["oi"] for r in below_pool), default=0.0)

    # select by OI…
    above_picked = sorted(above_pool, key=lambda r: (-r["call"]["oi"], r["strike"]))[:3]
    below_picked = sorted(below_pool, key=lambda r: (-r["put"]["oi"], -r["strike"]))[:3]
    # …number by distance from spot (nearest = R1 / S1)
    above_picked.sort(key=lambda r: (r["strike"] - spot, r["strike"]))
    below_picked.sort(key=lambda r: (spot - r["strike"], -r["strike"]))

    resistance = []
    for i, r in enumerate(above_picked, 1):
        oi = r["call"]["oi"]
        resistance.append(
            {
                "strike": r["strike"],
                "oi": oi,
                "tag": f"R{i}",
                "strength": _strength(oi, call_peak),
                "oi_pct": (oi / call_peak * 100.0) if call_peak else 0.0,
                "edge": r["strike"] in edge_strikes,
            }
        )
    support = []
    for i, r in enumerate(below_picked, 1):
        oi = r["put"]["oi"]
        support.append(
            {
                "strike": r["strike"],
                "oi": oi,
                "tag": f"S{i}",
                "strength": _strength(oi, put_peak),
                "oi_pct": (oi / put_peak * 100.0) if put_peak else 0.0,
                "edge": r["strike"] in edge_strikes,
            }
        )
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


def _pivot_read(spot: float, strike: float, *, heavy_call: bool, heavy_put: bool) -> str:
    if spot > strike and heavy_call:
        return PIVOT_READ_ABOVE_CALL
    if spot < strike and heavy_put:
        return PIVOT_READ_BELOW_PUT
    return PIVOT_READ_BATTLE


def _pivot(rows: list[dict[str, Any]], spot: float) -> dict[str, Any] | None:
    """Nearest heavy-OI strike within ±2 strikes (±100 pts) of spot, or None.

    Qualifies when Call OI ≥ 80% of the window's peak Call OI, or Put OI ≥ 80% of peak Put OI.
    If several qualify, pick the nearest to spot (then highest max(call, put) OI).
    """
    if not rows:
        return None
    call_peak = max((r["call"]["oi"] for r in rows), default=0.0)
    put_peak = max((r["put"]["oi"] for r in rows), default=0.0)
    candidates: list[dict[str, Any]] = []
    for r in rows:
        if abs(r["strike"] - spot) > PIVOT_BAND_PTS:
            continue
        call_oi = r["call"]["oi"]
        put_oi = r["put"]["oi"]
        heavy_call = call_peak > 0 and call_oi >= PIVOT_STRONG * call_peak
        heavy_put = put_peak > 0 and put_oi >= PIVOT_STRONG * put_peak
        if not (heavy_call or heavy_put):
            continue
        candidates.append(
            {
                "strike": r["strike"],
                "call_oi": call_oi,
                "put_oi": put_oi,
                "heavy_call": heavy_call,
                "heavy_put": heavy_put,
                "tag": "PIVOT",
                "read": _pivot_read(spot, r["strike"], heavy_call=heavy_call, heavy_put=heavy_put),
            }
        )
    if not candidates:
        return None
    candidates.sort(key=lambda c: (abs(c["strike"] - spot), -max(c["call_oi"], c["put_oi"]), c["strike"]))
    return candidates[0]


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
            "pivot": None,
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
            "pivot": None,
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
    pivot = _pivot(window_rows, spot)
    from app.options.chain_analytics import enrich_chain

    phase2 = enrich_chain(
        all_strikes=parsed,
        window_rows=window_rows,
        atm=atm,
        spot=spot,
        full_pcr=pcr,
    )
    level_by_strike = {r["strike"]: r for r in resistance}
    level_by_strike.update({s["strike"]: s for s in support})
    pivot_strike = pivot["strike"] if pivot else None
    max_pain_strike = (phase2.get("max_pain") or {}).get("strike")

    display = []
    for r in window_rows:
        lvl = level_by_strike.get(r["strike"])
        is_pivot = pivot_strike is not None and r["strike"] == pivot_strike
        # Prefer PIVOT on the table when both apply; R/S remain in Key Levels lists
        if is_pivot:
            tag = "PIVOT"
            strength = None
            edge = False
        elif lvl:
            tag = lvl["tag"]
            strength = lvl.get("strength")
            edge = bool(lvl.get("edge"))
        else:
            tag = None
            strength = None
            edge = False
        display.append(
            {
                "strike": r["strike"],
                "call": r["call"],
                "put": r["put"],
                "atm": r["strike"] == atm,
                "tag": tag,
                "strength": strength,
                "edge": edge,
                "max_pain": max_pain_strike is not None and r["strike"] == max_pain_strike,
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
        "pivot": pivot,
        "major_walls": major_walls,
        "pcrs": phase2["pcrs"],
        "max_pain": phase2["max_pain"],
        "buildup_analysis": phase2["buildup_analysis"],
        "near_atm_strikes": phase2["near_atm_strikes"],
        "rows": display,
        # full chain kept for snapshots / later phases (not shown in the ±window table)
        "all_strikes": parsed,
        "max_oi": max((max(r["call"]["oi"], r["put"]["oi"]) for r in window_rows), default=0.0),
        "max_oi_chg": max(
            (max(abs(r["call"].get("oi_chg") or 0.0), abs(r["put"].get("oi_chg") or 0.0)) for r in window_rows),
            default=0.0,
        ),
    }
