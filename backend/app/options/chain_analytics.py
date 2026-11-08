"""Phase 2 Options analytics: build-up labels, PCR set, near-ATM zone, max pain.

Pure functions — no sockets.
"""

from __future__ import annotations

from typing import Any, Literal

NEAR_ATM = 5
NEUTRAL_OI_PCT = 2.0
PCR_CAP_HIGH = 5.0
PCR_CAP_LOW = 0.2
CONCENTRATION_SHARE = 0.40

BuildupLabel = Literal[
    "Long Build-up",
    "Short Build-up",
    "Short Covering",
    "Long Unwinding",
    "Neutral",
]
NiftyBias = Literal["Bullish", "Bearish"]
PcrRead = Literal["Bullish", "Neutral", "Bearish", "Strong Bullish", "Strong Bearish"]

SIDE = Literal["call", "put"]


def classify_buildup(
    price_chg: float,
    oi_chg: float,
    oi_chg_pct: float | None,
) -> dict[str, Any]:
    """Price/OI change → build-up label (Neutral when |oi_chg_pct| < 2%)."""
    if oi_chg_pct is None or abs(float(oi_chg_pct)) < NEUTRAL_OI_PCT:
        return {"label": "Neutral", "nifty": None}
    if price_chg > 0 and oi_chg > 0:
        label: BuildupLabel = "Long Build-up"
    elif price_chg < 0 and oi_chg > 0:
        label = "Short Build-up"
    elif price_chg > 0 and oi_chg < 0:
        label = "Short Covering"
    elif price_chg < 0 and oi_chg < 0:
        label = "Long Unwinding"
    else:
        return {"label": "Neutral", "nifty": None}
    return {"label": label, "nifty": None}  # nifty filled by annotate_leg


def nifty_bias(side: str, label: str) -> NiftyBias | None:
    """What this leg's build-up implies for Nifty direction."""
    if label == "Neutral":
        return None
    if side == "call":
        if label in ("Short Build-up", "Long Unwinding"):
            return "Bearish"
        if label in ("Long Build-up", "Short Covering"):
            return "Bullish"
    if side == "put":
        if label in ("Short Build-up", "Long Unwinding"):
            return "Bullish"
        if label in ("Long Build-up", "Short Covering"):
            return "Bearish"
    return None


def annotate_leg(leg: dict[str, Any], side: str) -> dict[str, Any]:
    """Attach build-up + nifty bias onto a shaped leg dict (mutates and returns)."""
    built = classify_buildup(
        float(leg.get("price_chg") or 0.0),
        float(leg.get("oi_chg") or 0.0),
        leg.get("oi_chg_pct") if leg.get("oi_chg_pct") is not None else None,
    )
    label = built["label"]
    leg["buildup"] = label
    leg["buildup_nifty"] = nifty_bias(side, label)
    return leg


def near_atm_slice(rows: list[dict[str, Any]], atm: float, band: int = NEAR_ATM) -> list[dict[str, Any]]:
    """ATM ±band strikes, clamped to the ends of the chain."""
    if not rows:
        return []
    strikes = [r["strike"] for r in rows]
    try:
        idx = strikes.index(atm)
    except ValueError:
        idx = min(range(len(strikes)), key=lambda i: (abs(strikes[i] - atm), strikes[i]))
    lo = max(0, idx - band)
    hi = min(len(rows), idx + band + 1)
    return rows[lo:hi]


def pcr_read(pcr: float | None) -> PcrRead | None:
    if pcr is None:
        return None
    if pcr > 1.2:
        return "Bullish"
    if pcr < 0.8:
        return "Bearish"
    return "Neutral"


def _normalize_pcr_direction(read: str | None) -> str | None:
    """Collapse Strong* into Bullish/Bearish for agreement checks."""
    if read is None:
        return None
    if read.startswith("Strong "):
        return read[len("Strong ") :]
    return read


def _pcr_payload(value: float | None, *, reason: str | None = None) -> dict[str, Any]:
    return {
        "value": value,
        "display": "n/a" if value is None else round(value, 4),
        "reason": reason,
        "read": pcr_read(value),
        "one_sided": False,
        "capped_label": None,
        "net_doi": None,
        "concentration": None,
    }


def oi_pcr(rows: list[dict[str, Any]], *, spot: float | None = None) -> dict[str, Any]:
    call = sum(float(r["call"]["oi"]) for r in rows)
    put = sum(float(r["put"]["oi"]) for r in rows)
    if call <= 0:
        out = _pcr_payload(None, reason="no call OI in zone")
    else:
        out = _pcr_payload(put / call)
    if spot is not None:
        out["concentration"] = oi_concentration_note(rows, spot)
    return out


def change_oi_pcr(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Σ Put ΔOI / Σ Call ΔOI. Zero or negative call sum → n/a (not a usable ratio).

    Extreme ratios (>5 or <0.2) stay exact in ``value`` but display as One-sided with a
    capped label; ``net_doi`` is Put ΔOI − Call ΔOI for the raw flow readout.
    """
    call_d = sum(float(r["call"].get("oi_chg") or 0.0) for r in rows)
    put_d = sum(float(r["put"].get("oi_chg") or 0.0) for r in rows)
    if call_d == 0:
        return _pcr_payload(None, reason="no call OI change in zone")
    if call_d < 0:
        return _pcr_payload(None, reason="call OI fell in zone (negative denominator)")
    ratio = put_d / call_d
    out = _pcr_payload(ratio)
    out["net_doi"] = put_d - call_d
    if ratio > PCR_CAP_HIGH:
        out["display"] = "One-sided"
        out["capped_label"] = f"> {PCR_CAP_HIGH:g} · Strong Bullish"
        out["read"] = "Strong Bullish"
        out["one_sided"] = True
    elif ratio < PCR_CAP_LOW:
        out["display"] = "One-sided"
        out["capped_label"] = f"< {PCR_CAP_LOW:g} · Strong Bearish"
        out["read"] = "Strong Bearish"
        out["one_sided"] = True
    return out


def _leg_moneyness(side: str, strike: float, spot: float) -> str:
    if side == "call":
        return "ITM" if strike < spot else "OTM"
    return "ITM" if strike > spot else "OTM"


def oi_concentration_note(rows: list[dict[str, Any]], spot: float) -> str | None:
    """If one strike holds >40% of Call or Put OI in the zone, return a short warning."""
    call_total = sum(float(r["call"]["oi"]) for r in rows)
    put_total = sum(float(r["put"]["oi"]) for r in rows)
    best: tuple[float, str] | None = None  # (share, note)
    for r in rows:
        strike = float(r["strike"])
        for side, total in (("call", call_total), ("put", put_total)):
            if total <= 0:
                continue
            oi = float(r[side]["oi"])
            share = oi / total
            if share <= CONCENTRATION_SHARE:
                continue
            money = _leg_moneyness(side, strike, spot)
            gloss = "ITM — trapped writers" if money == "ITM" else "OTM"
            side_label = "Call" if side == "call" else "Put"
            note = f"Dominated by {strike:,.0f} {side_label} OI ({gloss})"
            if best is None or share > best[0]:
                best = (share, note)
    return best[1] if best else None


def pcr_mixed_signals(pcrs: dict[str, Any]) -> bool:
    """True when full / near-ATM / ΔOI reads are all present and not unanimous."""
    directions: list[str] = []
    for key in ("full", "near_atm", "change_oi"):
        payload = pcrs.get(key) or {}
        direction = _normalize_pcr_direction(payload.get("read"))
        if direction is None:
            return False
        directions.append(direction)
    return len(set(directions)) > 1


def max_pain(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Strike K minimising writer pain = Σ call_oi×max(0,K−S) + Σ put_oi×max(0,S−K)."""
    if not rows:
        return None
    strikes = [float(r["strike"]) for r in rows]
    best_k = strikes[0]
    best_pain = float("inf")
    for k in strikes:
        pain = 0.0
        for r in rows:
            s = float(r["strike"])
            pain += float(r["call"]["oi"]) * max(0.0, k - s)
            pain += float(r["put"]["oi"]) * max(0.0, s - k)
        if pain < best_pain or (pain == best_pain and k < best_k):
            best_pain = pain
            best_k = k
    return {
        "strike": best_k,
        "pain": best_pain,
        "tag": "MAX PAIN",
    }


def buildup_analysis(zone: list[dict[str, Any]]) -> dict[str, Any]:
    """Near-ATM ΔOI totals, top additions, one-line bias summary."""
    call_d = sum(float(r["call"].get("oi_chg") or 0.0) for r in zone)
    put_d = sum(float(r["put"].get("oi_chg") or 0.0) for r in zone)
    top_call = sorted(zone, key=lambda r: -float(r["call"].get("oi_chg") or 0.0))[:3]
    top_put = sorted(zone, key=lambda r: -float(r["put"].get("oi_chg") or 0.0))[:3]

    # Writing = Short Build-up on that side (OI up, price down). Use net ΔOI sign as proxy for panel.
    if put_d > call_d and put_d > 0:
        summary = "Put writing dominant near ATM → Bullish bias"
        bias: NiftyBias | Literal["Mixed"] = "Bullish"
    elif call_d > put_d and call_d > 0:
        summary = "Call writing dominant → Bearish bias"
        bias = "Bearish"
    else:
        summary = "Mixed"
        bias = "Mixed"

    return {
        "zone_strikes": [r["strike"] for r in zone],
        "call_doi": call_d,
        "put_doi": put_d,
        "top_call_additions": [
            {"strike": r["strike"], "oi_chg": float(r["call"].get("oi_chg") or 0.0)} for r in top_call
        ],
        "top_put_additions": [
            {"strike": r["strike"], "oi_chg": float(r["put"].get("oi_chg") or 0.0)} for r in top_put
        ],
        "summary": summary,
        "bias": bias,
    }


def enrich_chain(
    *,
    all_strikes: list[dict[str, Any]],
    window_rows: list[dict[str, Any]],
    atm: float,
    spot: float,
    full_pcr: float | None,
) -> dict[str, Any]:
    """Annotate legs + compute Phase 2 blocks from full chain / window."""
    for r in all_strikes:
        annotate_leg(r["call"], "call")
        annotate_leg(r["put"], "put")

    zone = near_atm_slice(all_strikes, atm, NEAR_ATM)
    if full_pcr is not None:
        full = _pcr_payload(full_pcr)
        full["concentration"] = oi_concentration_note(all_strikes, spot)
    else:
        full = oi_pcr(all_strikes, spot=spot)
    pcrs = {
        "full": full,
        "near_atm": oi_pcr(zone, spot=spot),
        "change_oi": change_oi_pcr(zone),
    }

    mp = max_pain(all_strikes)
    if mp is not None:
        mp["distance"] = mp["strike"] - spot

    analysis = buildup_analysis(zone)

    # window display legs already share dict refs with all_strikes when sliced from same objects;
    # if not, re-annotate window
    for r in window_rows:
        if "buildup" not in r["call"]:
            annotate_leg(r["call"], "call")
            annotate_leg(r["put"], "put")

    return {
        "pcrs": pcrs,
        "pcr_mixed_signals": pcr_mixed_signals(pcrs),
        "max_pain": mp,
        "buildup_analysis": analysis,
        "near_atm_strikes": [r["strike"] for r in zone],
    }
