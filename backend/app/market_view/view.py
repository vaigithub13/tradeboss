"""The pre-market view: five checks, each scored -1 / 0 / +1, an overall bias, the expected range from
India VIX, a day type (trend vs range) and the levels to watch. Pure: no I/O, no clock.

The thresholds are first guesses written down before any data; every view is saved and scored after
the close (score.py), so they can be judged on results instead of being trusted.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

GLOBAL_PCT = 0.3        # mean move beyond ±0.3% counts
FII_CASH_CR = 1000.0    # ₹ crore net, either way
FII_LONG_HIGH = 55.0    # FII index-futures long % above this: supportive
FII_LONG_LOW = 30.0     # below this: heavy shorts
CRUDE_PCT = 1.5         # Brent move beyond ±1.5% overnight
VIX_CHANGE_PCT = 5.0    # VIX up / down more than 5% on the day
CPR_WIDE = 1.2          # CPR width vs its 20-day mean
CPR_NARROW = 0.8
TRADING_DAYS = 252


def _factor(key: str, label: str, score: int | None, value: str, reason: str) -> dict[str, Any]:
    return {"key": key, "label": label, "score": score, "available": score is not None, "value": value,
            "reason": reason}


def _sign(x: float, band: float) -> int:
    return 1 if x > band else -1 if x < -band else 0


# ------------------------------------------------------------------ 1. global
def score_global(quotes: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    us = [q["change_pct"] for k in ("sp500", "nasdaq") if (q := quotes.get(k))]
    fut = quotes.get("us_futures")
    asia = [q["change_pct"] for k in ("nikkei", "hang_seng") if (q := quotes.get(k))]
    parts = []
    if us:
        parts.append(sum(us) / len(us))
    if fut:
        parts.append(fut["change_pct"])
    if asia:
        parts.append(sum(asia) / len(asia))
    if not parts:
        return _factor("global", "Global", None, "–", "no global quotes")
    mean = sum(parts) / len(parts)
    bits = []
    if us:
        bits.append(f"US {sum(us) / len(us):+.2f}%")
    if fut:
        bits.append(f"US fut {fut['change_pct']:+.2f}%")
    if asia:
        bits.append(f"Asia {sum(asia) / len(asia):+.2f}%")
    return _factor("global", "Global", _sign(mean, GLOBAL_PCT), f"{mean:+.2f}%", ", ".join(bits))


# ------------------------------------------------------------------ 2. FII / DII
def score_fii(cash: dict[str, Any] | None, oi: dict[str, Any] | None) -> dict[str, Any]:
    votes: list[int] = []
    bits: list[str] = []
    value = "–"
    if cash:
        votes.append(_sign(cash["fii_net"], FII_CASH_CR))
        value = f"FII {cash['fii_net']:+,.0f} Cr"
        bits.append(f"FII cash {cash['fii_net']:+,.0f} Cr")
        if cash.get("dii_net") is not None:
            bits.append(f"DII {cash['dii_net']:+,.0f} Cr")
    if oi:
        pct = oi["fii_long_pct"]
        votes.append(1 if pct > FII_LONG_HIGH else -1 if pct < FII_LONG_LOW else 0)
        bits.append(f"FII index futures {pct:.0f}% long")
    if not votes:
        return _factor("fii", "FII / DII", None, "–", "NSE data unavailable")
    total = sum(votes)
    return _factor("fii", "FII / DII", 1 if total > 0 else -1 if total < 0 else 0, value, ", ".join(bits))


# ------------------------------------------------------------------ 3. crude
def score_crude(brent: dict[str, Any] | None) -> dict[str, Any]:
    if not brent:
        return _factor("crude", "Crude", None, "–", "no Brent quote")
    pct = brent["change_pct"]
    # rising crude is a cost for India: a sharp rise is negative, a sharp fall positive
    return _factor("crude", "Crude", -_sign(pct, CRUDE_PCT), f"{pct:+.2f}%", f"Brent {brent['last']:.2f}")


# ------------------------------------------------------------------ 4. VIX
def score_vix(last: float | None, prev: float | None) -> dict[str, Any]:
    if last is None:
        return _factor("vix", "India VIX", None, "–", "no VIX close")
    if not prev:
        return _factor("vix", "India VIX", 0, f"{last:.2f}", "no previous close")
    pct = (last / prev - 1.0) * 100.0
    # rising fear is negative for direction, falling fear positive
    return _factor("vix", "India VIX", -_sign(pct, VIX_CHANGE_PCT), f"{last:.2f} ({last - prev:+.2f})",
                   f"{pct:+.1f}% on the day")


# ------------------------------------------------------------------ 5. structure
def structure(daily: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """From completed daily candles (ascending, the last one is yesterday)."""
    if len(daily) < 2:
        return None
    y = daily[-1]
    h, l, c = float(y["high"]), float(y["low"]), float(y["close"])
    pivot = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * pivot - bc
    top, bottom = max(tc, bc), min(tc, bc)

    def width(d: dict[str, Any]) -> float:
        p = (float(d["high"]) + float(d["low"]) + float(d["close"])) / 3
        b = (float(d["high"]) + float(d["low"])) / 2
        return abs(2 * p - 2 * b) / p * 100

    w = width(y)
    prior = [width(d) for d in daily[-21:-1]]
    ratio = w / (sum(prior) / len(prior)) if prior and sum(prior) > 0 else None
    closes = [float(d["close"]) for d in daily[-20:]]
    sma20 = sum(closes) / len(closes)
    last5, prev5 = daily[-5:], daily[-10:-5]
    trend = "sideways"
    if len(prev5) == 5:
        hh = max(float(d["high"]) for d in last5) > max(float(d["high"]) for d in prev5)
        hl = min(float(d["low"]) for d in last5) > min(float(d["low"]) for d in prev5)
        lh = max(float(d["high"]) for d in last5) < max(float(d["high"]) for d in prev5)
        ll = min(float(d["low"]) for d in last5) < min(float(d["low"]) for d in prev5)
        if c > sma20 and hh and hl:
            trend = "up"
        elif c < sma20 and lh and ll:
            trend = "down"
    return {
        "prev_close": c, "pdh": h, "pdl": l, "pivot": pivot, "tc": top, "bc": bottom,
        "cpr_width_pct": w, "cpr_ratio": ratio, "sma20": sma20, "trend": trend,
        "close_vs_cpr": "above" if c > top else "below" if c < bottom else "inside",
    }


def score_structure(st: dict[str, Any] | None) -> dict[str, Any]:
    if not st:
        return _factor("structure", "Structure", None, "–", "not enough daily candles")
    score = 1 if st["trend"] == "up" else -1 if st["trend"] == "down" else 0
    if score == 0:
        score = 1 if st["prev_close"] > st["sma20"] and st["close_vs_cpr"] == "above" else \
            -1 if st["prev_close"] < st["sma20"] and st["close_vs_cpr"] == "below" else 0
    trend = {"up": "Uptrend", "down": "Downtrend", "sideways": "Sideways"}[st["trend"]]
    cpr = "narrow CPR" if (st["cpr_ratio"] or 1) < CPR_NARROW else "wide CPR" if (st["cpr_ratio"] or 1) > CPR_WIDE else "normal CPR"
    side = "above" if st["prev_close"] > st["sma20"] else "below"
    return _factor("structure", "Structure", score, f"{trend}, {st['close_vs_cpr']} CPR",
                   f"close {side} 20-day average, {cpr}")


# ------------------------------------------------------------------ verdict
BIAS_LABELS = {2: "Bullish", 1: "Mild bullish", 0: "Neutral", -1: "Mild bearish", -2: "Bearish"}


def bias_of(factors: Sequence[dict[str, Any]]) -> tuple[int, str]:
    total = sum(f["score"] for f in factors if f["score"] is not None)
    level = max(-2, min(2, total if abs(total) < 2 else (2 if total > 0 else -2)))
    return total, BIAS_LABELS[level]


def expected_range(ref: float, vix: float | None) -> tuple[float, float] | None:
    if vix is None or ref <= 0:
        return None
    move = ref * vix / 100.0 / math.sqrt(TRADING_DAYS)
    return ref - move, ref + move


def day_type(st: dict[str, Any] | None, gex_net: float | None) -> tuple[str, str]:
    ratio = st.get("cpr_ratio") if st else None
    if gex_net is not None and gex_net < 0:
        return "trend", "negative GEX: hedging can extend moves"
    if ratio is not None and ratio < CPR_NARROW:
        return "trend", "narrow CPR: breakouts more likely"
    if gex_net is not None and gex_net > 0 and ratio is not None and ratio > CPR_WIDE:
        return "range", "positive GEX and wide CPR: moves tend to pin"
    if gex_net is not None and gex_net > 0:
        return "range", "positive GEX: moves tend to pin"
    return "mixed", "no clear signal"


def levels_of(st: dict[str, Any] | None, rng: tuple[float, float] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if st:
        out += [
            {"key": "pdh", "label": "PDH", "price": round(st["pdh"], 2)},
            {"key": "pdl", "label": "PDL", "price": round(st["pdl"], 2)},
            {"key": "tc", "label": "CPR top", "price": round(st["tc"], 2)},
            {"key": "bc", "label": "CPR bottom", "price": round(st["bc"], 2)},
        ]
    if rng:
        out += [
            {"key": "range_hi", "label": "Range high", "price": round(rng[1], 2)},
            {"key": "range_lo", "label": "Range low", "price": round(rng[0], 2)},
        ]
    return out


def plan_of(total: int, st: dict[str, Any] | None, flip: float | None) -> str:
    if not st:
        return "No structure levels yet: wait for the first 15 minutes."
    up = max(st["pdh"], st["tc"]) if total > 0 else st["pdh"]
    down = min(st["pdl"], st["bc"]) if total < 0 else st["pdl"]
    f = f" (γ flip {flip:,.0f})" if flip is not None else ""
    if total > 0:
        return f"Calls on a hold above {up:,.0f}. Puts only below {down:,.0f}{f}."
    if total < 0:
        return f"Puts on a break below {down:,.0f}{f}. Calls only above {up:,.0f}."
    return f"No edge: wait for a break of {st['pdh']:,.0f} or {st['pdl']:,.0f}{f}."


def build_view(
    *,
    day: str,
    built_at: int,
    quotes: dict[str, dict[str, Any] | None],
    brent: dict[str, Any] | None,
    fii_cash: dict[str, Any] | None,
    fii_oi: dict[str, Any] | None,
    vix_last: float | None,
    vix_prev: float | None,
    daily: Sequence[dict[str, Any]],
    gex: dict[str, Any] | None,
) -> dict[str, Any]:
    st = structure(daily)
    factors = [
        score_global(quotes),
        score_fii(fii_cash, fii_oi),
        score_crude(brent),
        score_vix(vix_last, vix_prev),
        score_structure(st),
    ]
    total, bias = bias_of(factors)
    rng = expected_range(st["prev_close"], vix_last) if st else None
    gex_net = gex.get("total_net") if gex else None
    flip = gex.get("gamma_flip") if gex else None
    dtype, dreason = day_type(st, gex_net)
    return {
        "day": day,
        "built_at": built_at,
        "factors": factors,
        "score": total,
        "bias": bias,
        "expected_range": [round(rng[0], 2), round(rng[1], 2)] if rng else None,
        "day_type": dtype,
        "day_type_reason": dreason,
        "levels": levels_of(st, rng),
        "plan": plan_of(total, st, flip),
        "structure": st,
        "gex": {"total_net": gex_net, "gamma_flip": flip} if gex else None,
        "quotes": {k: v for k, v in quotes.items() if v},
        "brent": brent,
        "fii_cash": fii_cash,
        "fii_oi": fii_oi,
    }


# ------------------------------------------------------------------ after the close
def score_outcome(view: dict[str, Any], day_candles: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """How the view did, from the day's intraday candles (ascending)."""
    if not day_candles:
        return None
    o = float(day_candles[0]["open"])
    c = float(day_candles[-1]["close"])
    h = max(float(x["high"]) for x in day_candles)
    lo = min(float(x["low"]) for x in day_candles)
    move = c - o
    actual_type = "trend" if h > lo and abs(move) >= 0.5 * (h - lo) else "range"
    score = int(view.get("score") or 0)
    bias_hit = None if score == 0 else (score > 0) == (move > 0)
    rng = view.get("expected_range")
    return {
        "open": o, "close": c, "high": h, "low": lo, "move": move,
        "actual_type": actual_type,
        "bias_hit": bias_hit,
        "range_held": None if not rng else (h <= rng[1] and lo >= rng[0]),
        "day_type_hit": None if view.get("day_type") == "mixed" else view.get("day_type") == actual_type,
    }
