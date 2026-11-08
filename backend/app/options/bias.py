"""Options Phase 4: directional bias score (−10..+10) from chain + pre-market factors.

Pure functions — no sockets. Callers supply market-view JSON and prior EOD snapshot.
"""

from __future__ import annotations

from typing import Any

GIFT_NIFTY_STATUS = "not connected"

# Base weights (sum = 1.0). On expiry day max_pain rises to 15%; others × 0.85/0.95.
WEIGHTS_NORMAL: dict[str, float] = {
    "buildup": 0.25,
    "pivot": 0.20,
    "pcr_doi": 0.15,
    "pcr_near": 0.10,
    "iv_trend": 0.10,
    "premarket": 0.15,
    "max_pain": 0.05,
}
EXPIRY_MAX_PAIN = 0.15
EXPIRY_OTHER_SCALE = 0.85 / 0.95  # remaining mass after lifting max_pain

COMPONENT_LABELS: dict[str, str] = {
    "buildup": "Near-ATM build-up",
    "pivot": "Spot vs Pivot",
    "pcr_doi": "PCR ΔOI",
    "pcr_near": "Near-ATM PCR",
    "iv_trend": "IV trend",
    "premarket": "Pre-market checks",
    "max_pain": "Max Pain pull",
}

BIAS_WIN_BUMP_AT_10 = 0.12  # +12 percentage points at |score|=10


def weights_for(*, expiry_day: bool) -> dict[str, float]:
    if not expiry_day:
        return dict(WEIGHTS_NORMAL)
    out = {k: (EXPIRY_MAX_PAIN if k == "max_pain" else v * EXPIRY_OTHER_SCALE) for k, v in WEIGHTS_NORMAL.items()}
    return out


def bias_label(score: int) -> str:
    if score <= -6:
        return "Strong Bearish"
    if score <= -2:
        return "Mildly Bearish"
    if score <= 1:
        return "Neutral"
    if score <= 5:
        return "Mildly Bullish"
    return "Strong Bullish"


def bias_win_bump(score: int | float) -> float:
    """Fractional win-chance bump over driftless: 0 at 0 → +0.12 at |10|."""
    return BIAS_WIN_BUMP_AT_10 * min(10.0, abs(float(score))) / 10.0


def bias_direction(score: int) -> str | None:
    """Bullish / Bearish when |score| ≥ 2; else None (no trade bias)."""
    if score >= 2:
        return "Bullish"
    if score <= -2:
        return "Bearish"
    return None


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_buildup(analysis: dict[str, Any] | None) -> float:
    if not analysis:
        return 0.0
    call_d = float(analysis.get("call_doi") or 0.0)
    put_d = float(analysis.get("put_doi") or 0.0)
    total = abs(call_d) + abs(put_d)
    if total <= 0:
        return 0.0
    # Put writing dominant → bullish; call writing → bearish
    raw = (put_d - call_d) / total
    return _clip(raw)


def score_pivot(spot: float, pivot: dict[str, Any] | None, *, one_sigma: float | None) -> float:
    if not pivot or pivot.get("strike") is None:
        return 0.0
    p = float(pivot["strike"])
    dist = float(spot) - p
    if abs(dist) < 1e-9:
        return 0.0
    scale = max(50.0, 0.5 * float(one_sigma)) if one_sigma and one_sigma > 0 else 50.0
    return _clip(dist / scale)


def _pcr_read_score(read: str | None) -> float:
    if read is None:
        return 0.0
    table = {
        "Strong Bullish": 1.0,
        "Bullish": 0.5,
        "Neutral": 0.0,
        "Bearish": -0.5,
        "Strong Bearish": -1.0,
    }
    return table.get(read, 0.0)


def score_pcr(pcr_payload: dict[str, Any] | None) -> float:
    if not pcr_payload:
        return 0.0
    return _pcr_read_score(pcr_payload.get("read"))


def score_iv_trend(
    *,
    atm_iv: float | None,
    prev_atm_iv: float | None,
    skew: float | None,
) -> float:
    """ATM IV down → +; up → −. Elevated put skew → − tilt. Average of available legs."""
    legs: list[float] = []
    if atm_iv is not None and prev_atm_iv is not None and prev_atm_iv > 0:
        # −5% IV change ≈ full +1; +5% ≈ −1
        pct = (atm_iv - prev_atm_iv) / prev_atm_iv
        legs.append(_clip(-pct / 0.05))
    if skew is not None:
        # Skew in IV points; ±3 pts ≈ full signal (put-rich = fear = bearish)
        legs.append(_clip(-float(skew) / 3.0))
    if not legs:
        return 0.0
    return _clip(sum(legs) / len(legs))


def score_premarket(factors: list[dict[str, Any]] | None) -> float | None:
    """Mean of available −1/0/+1 factor scores; None if no factors available."""
    if not factors:
        return None
    vals = [float(f["score"]) for f in factors if f.get("score") is not None]
    if not vals:
        return None
    return _clip(sum(vals) / len(vals))


def score_max_pain(spot: float, max_pain: dict[str, Any] | None) -> float:
    if not max_pain or max_pain.get("strike") is None:
        return 0.0
    mp = float(max_pain["strike"])
    dist = mp - float(spot)  # spot below MP → positive (upward pull)
    if abs(dist) < 1e-9:
        return 0.0
    scale = 100.0  # ~100 pts → full magnitude
    return _clip(dist / scale)


def aggregate_score(components: list[dict[str, Any]]) -> int:
    raw = sum(float(c["weight"]) * float(c["score"]) for c in components)
    return int(max(-10, min(10, round(10.0 * raw))))


def level_strike(levels: list[dict[str, Any]] | None, tag: str) -> float | None:
    for lv in levels or []:
        if lv.get("tag") == tag:
            return float(lv["strike"])
    return None


def changed_since_yesterday(
    *,
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Diff R1/S1/Pivot/MaxPain, PCR, ATM IV vs prior EOD snapshot fields."""
    if previous is None:
        return None

    def _lvl(view: dict[str, Any], tag: str) -> float | None:
        if tag == "PIVOT":
            p = view.get("pivot")
            return float(p["strike"]) if p and p.get("strike") is not None else None
        if tag == "MAX PAIN":
            mp = view.get("max_pain")
            return float(mp["strike"]) if mp and mp.get("strike") is not None else None
        pool = (view.get("resistance") or []) + (view.get("support") or [])
        return level_strike(pool, tag)

    def _delta(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return a - b

    cur_pcr = None
    prev_pcr = None
    if isinstance(current.get("pcrs"), dict) and isinstance(current["pcrs"].get("full"), dict):
        cur_pcr = current["pcrs"]["full"].get("value")
    elif current.get("pcr") is not None:
        cur_pcr = current.get("pcr")
    if isinstance(previous.get("pcrs"), dict) and isinstance(previous["pcrs"].get("full"), dict):
        prev_pcr = previous["pcrs"]["full"].get("value")
    elif previous.get("pcr") is not None:
        prev_pcr = previous.get("pcr")

    cur_iv = (current.get("iv_panel") or {}).get("atm_iv")
    prev_iv = previous.get("atm_iv")
    if prev_iv is None and isinstance(previous.get("iv_panel"), dict):
        prev_iv = previous["iv_panel"].get("atm_iv")
    # Snapshot lines may store ATM IV on ATM strike legs
    if prev_iv is None and previous.get("strikes") and previous.get("atm") is not None:
        atm = float(previous["atm"])
        for s in previous["strikes"]:
            if float(s.get("strike") or 0) == atm:
                civ = (s.get("call") or {}).get("iv")
                piv = (s.get("put") or {}).get("iv")
                vals = [float(v) for v in (civ, piv) if v is not None]
                if vals:
                    prev_iv = sum(vals) / len(vals)
                break

    return {
        "r1": _delta(_lvl(current, "R1"), _lvl(previous, "R1")),
        "s1": _delta(_lvl(current, "S1"), _lvl(previous, "S1")),
        "pivot": _delta(_lvl(current, "PIVOT"), _lvl(previous, "PIVOT")),
        "max_pain": _delta(_lvl(current, "MAX PAIN"), _lvl(previous, "MAX PAIN")),
        "pcr_full": _delta(
            float(cur_pcr) if cur_pcr is not None else None,
            float(prev_pcr) if prev_pcr is not None else None,
        ),
        "atm_iv": _delta(
            float(cur_iv) if cur_iv is not None else None,
            float(prev_iv) if prev_iv is not None else None,
        ),
        "prev_as_of_ist": previous.get("as_of_ist"),
        "prev_spot": previous.get("spot"),
    }


def build_bias(
    view: dict[str, Any],
    *,
    market_view: dict[str, Any] | None = None,
    previous_eod: dict[str, Any] | None = None,
    expiry_day: bool = False,
) -> dict[str, Any]:
    """Attachable bias block for the Options chain response."""
    spot = float(view.get("spot") or 0.0)
    em = view.get("expected_move") or {}
    one_sigma = None
    osig = em.get("one_sigma") or {}
    if osig.get("move") is not None and osig.get("available") is not False:
        one_sigma = float(osig["move"])
    elif em.get("iv") and em["iv"].get("move") is not None:
        one_sigma = float(em["iv"]["move"])

    iv = view.get("iv_panel") or {}
    prev_iv = None
    if previous_eod is not None:
        ch = changed_since_yesterday(current=view, previous=previous_eod) or {}
        # recover absolute prev IV from delta if needed
        if iv.get("atm_iv") is not None and ch.get("atm_iv") is not None:
            prev_iv = float(iv["atm_iv"]) - float(ch["atm_iv"])
        elif previous_eod.get("atm_iv") is not None:
            prev_iv = float(previous_eod["atm_iv"])

    factors = (market_view or {}).get("factors") if market_view else None
    pm = score_premarket(factors if isinstance(factors, list) else None)

    w = weights_for(expiry_day=expiry_day)
    raw_scores: dict[str, float] = {
        "buildup": score_buildup(view.get("buildup_analysis")),
        "pivot": score_pivot(spot, view.get("pivot"), one_sigma=one_sigma),
        "pcr_doi": score_pcr((view.get("pcrs") or {}).get("change_oi")),
        "pcr_near": score_pcr((view.get("pcrs") or {}).get("near_atm")),
        "iv_trend": score_iv_trend(atm_iv=iv.get("atm_iv"), prev_atm_iv=prev_iv, skew=iv.get("skew")),
        "premarket": 0.0 if pm is None else pm,
        "max_pain": score_max_pain(spot, view.get("max_pain")),
    }

    components = []
    for key, weight in w.items():
        s = raw_scores[key]
        components.append(
            {
                "key": key,
                "label": COMPONENT_LABELS[key],
                "weight": weight,
                "score": s,
                "contribution": weight * s,
                "available": False if key == "premarket" and pm is None else True,
            }
        )

    # If premarket unavailable, redistribute its weight across available components
    if pm is None:
        avail = [c for c in components if c["key"] != "premarket"]
        mass = sum(c["weight"] for c in avail)
        if mass > 0:
            for c in avail:
                c["weight"] = c["weight"] / mass
                c["contribution"] = c["weight"] * c["score"]
            for c in components:
                if c["key"] == "premarket":
                    c["weight"] = 0.0
                    c["contribution"] = 0.0

    score = aggregate_score([c for c in components if c["weight"] > 0])
    label = bias_label(score)
    return {
        "score": score,
        "label": label,
        "direction": bias_direction(score),
        "components": components,
        "premarket_available": pm is not None,
        "gift_nifty": GIFT_NIFTY_STATUS,
        "changed_since_yesterday": changed_since_yesterday(current=view, previous=previous_eod),
        "expiry_day": expiry_day,
        "win_bump": bias_win_bump(score),
        "as_of": view.get("as_of"),
        "as_of_ist": view.get("as_of_ist"),
        "spot": spot,
    }


def overview_summary(view: dict[str, Any], bias: dict[str, Any]) -> dict[str, Any]:
    """Compact 3-line Overview payload."""
    spot = float(view.get("spot") or 0.0)

    def _dist(strike: float | None) -> float | None:
        return None if strike is None else strike - spot

    r1 = level_strike(view.get("resistance"), "R1")
    s1 = level_strike(view.get("support"), "S1")
    pivot = view.get("pivot")
    pivot_k = float(pivot["strike"]) if pivot and pivot.get("strike") is not None else None
    mp = view.get("max_pain")
    mp_k = float(mp["strike"]) if mp and mp.get("strike") is not None else None
    em = view.get("expected_move") or {}
    picker = (view.get("strike_picker") or {}).get("active") or (view.get("strike_picker") or {}).get("Bullish")
    pick_line = "No edge"
    if picker:
        if picker.get("has_setup") and picker.get("highlight"):
            hi = next((c for c in picker.get("candidates") or [] if c.get("highlighted")), None)
            if hi:
                pick_line = f"★ {hi.get('bucket')} {hi.get('strike'):.0f} {str(hi.get('side','')).upper()}"
            else:
                pick_line = f"★ {picker.get('highlight')}"
        elif picker.get("setup_message"):
            pick_line = str(picker["setup_message"])
        elif picker.get("auto_message"):
            pick_line = str(picker["auto_message"])
    return {
        "headline": f"Bias: {bias.get('label')} ({bias.get('score'):+d})",
        "levels": {
            "r1": r1,
            "r1_dist": _dist(r1),
            "pivot": pivot_k,
            "pivot_dist": _dist(pivot_k),
            "s1": s1,
            "s1_dist": _dist(s1),
            "max_pain": mp_k,
            "max_pain_dist": _dist(mp_k),
        },
        "expected_range": {
            "one_sigma": em.get("one_sigma"),
            "market_priced": em.get("market_priced") or {
                "move": em.get("move"),
                "lower": em.get("lower"),
                "upper": em.get("upper"),
            },
        },
        "picker": pick_line,
    }
