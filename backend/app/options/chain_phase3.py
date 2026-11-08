"""Options tab Phase 3: IV panel, expected move, level probabilities, strike picker.

Pure functions — no sockets. Callers supply India VIX, lot_size, and snapshot history.

Upstox option_greeks units (locked by tests; matches GEX which does iv/100 for BS):
  - iv: percent points (14.5 = 14.5%), same scale as India VIX
  - theta: premium points (same units as LTP) per calendar day
  - delta / gamma / vega: standard (delta dimensionless, gamma per point, …)
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from app.options.bs import ncdf
from app.options.time import CALENDAR_MIN_PER_YEAR, IST, calendar_minutes_to_expiry

STRADDLE_FACTOR = 0.85
VIX_BAND = 2.0
SKEW_DELTA = 0.25
IV_HISTORY_DAYS = 20
IV_MIN_DTE_DAYS = 3  # skip nearest expiry on its last calendar days
T_FLOOR_MINUTES = 15
TOUCH_CAP = 0.95
STOP_BUFFER_PTS = 20.0
STOP_BUFFER_PCT = 0.001  # 0.1% of spot
MIN_STOP_PTS = 40.0
MIN_STOP_EM_FRAC = 0.25

# Locked assumptions about Upstox payloads (see test_upstox_greeks_units).
UPSTOX_IV_UNIT = "percent"  # divide by 100 before BS / √T math
UPSTOX_THETA_UNIT = "premium_per_day"  # θ · t_hold with t_hold in days

HoldMode = Literal["intraday", "1d", "2d"]
Direction = Literal["Bullish", "Bearish"]

DELTA_BANDS: dict[str, tuple[float, float]] = {
    "ITM": (0.60, 0.70),
    "ATM": (0.45, 0.55),
    "OTM": (0.25, 0.35),
}

HOLD_LABELS: dict[HoldMode, str] = {
    "intraday": "Intraday",
    "1d": "1 day",
    "2d": "2 days",
}


def years_to_expiry_calendar(now: int | float, expiry: str | date, *, floor_min: int = T_FLOOR_MINUTES) -> float:
    """Calendar years to 15:30 IST on expiry, floored at `floor_min` minutes."""
    exp = date.fromisoformat(expiry) if isinstance(expiry, str) else expiry
    minutes = max(int(floor_min), calendar_minutes_to_expiry(int(now), exp))
    return minutes / CALENDAR_MIN_PER_YEAR


def calendar_days_to_expiry(now: int | float, expiry: str | date, *, floor_min: int = T_FLOOR_MINUTES) -> float:
    """Calendar days left to 15:30 IST on expiry (same clock as T, including floor)."""
    return years_to_expiry_calendar(now, expiry, floor_min=floor_min) * (CALENDAR_MIN_PER_YEAR / (24 * 60))


def t_hold_days(now: int | float, expiry: str | date, mode: HoldMode = "1d") -> float:
    """Holding horizon in days for θ·t_hold, capped by time left to expiry.

    - intraday: remaining calendar time until 15:30 IST today (floor 15 min if still in session)
    - 1d / 2d: that many calendar days
    Always min(requested, days_to_expiry).
    """
    left = calendar_days_to_expiry(now, expiry)
    if mode == "1d":
        requested = 1.0
    elif mode == "2d":
        requested = 2.0
    else:
        local = datetime.fromtimestamp(int(now), IST)
        end = datetime(local.year, local.month, local.day, 15, 30, tzinfo=IST)
        if local >= end:
            requested = T_FLOOR_MINUTES / (24 * 60)
        else:
            mins = max(T_FLOOR_MINUTES, int((end - local).total_seconds() // 60))
            requested = mins / (24 * 60)
    return min(requested, left)


def stop_buffer(spot: float) -> float:
    return max(STOP_BUFFER_PTS, STOP_BUFFER_PCT * float(spot))


def min_stop_distance(spot: float, expected_move: float | None) -> float:
    em = float(expected_move) if expected_move is not None else 0.0
    return max(MIN_STOP_PTS, MIN_STOP_EM_FRAC * em)


def choose_stop(
    *,
    spot: float,
    direction: Direction,
    levels: list[dict[str, Any]],
    expected_move: float | None,
) -> dict[str, Any]:
    """Buffered stop with minimum distance; walk to the next level when too close.

    Bullish: levels on the downside (Pivot, S1, S2, …), stop = level − buffer.
    Bearish: levels on the upside (Pivot, R1, R2, …), stop = level + buffer.
    """
    buf = stop_buffer(spot)
    need = min_stop_distance(spot, expected_move)
    # Sort candidates: nearest to spot first on the protective side
    if direction == "Bullish":
        cands = [lv for lv in levels if float(lv["strike"]) <= spot]
        cands.sort(key=lambda lv: (spot - float(lv["strike"]), float(lv["strike"])))
    else:
        cands = [lv for lv in levels if float(lv["strike"]) >= spot]
        cands.sort(key=lambda lv: (float(lv["strike"]) - spot, float(lv["strike"])))

    rejected: list[dict[str, Any]] = []
    for lv in cands:
        level = float(lv["strike"])
        tag = str(lv.get("tag") or lv.get("key") or "")
        if direction == "Bullish":
            stop = level - buf
            dist = spot - stop
        else:
            stop = level + buf
            dist = stop - spot
        label = tag or f"{level:g}"
        if dist + 1e-9 < need:
            rejected.append(
                {
                    "level": level,
                    "tag": tag,
                    "raw_stop": stop,
                    "distance": dist,
                    "reason": f"{label} too close ({dist:.0f} < min {need:.0f})",
                }
            )
            continue
        why = (
            f"{label} {'−' if direction == 'Bullish' else '+'} "
            f"buffer {buf:.0f}; dist {dist:.0f} ≥ min {need:.0f}"
        )
        if rejected:
            why += f"; skipped {[r['tag'] or r['level'] for r in rejected]}"
        return {
            "stop": stop,
            "level": level,
            "tag": tag,
            "buffer": buf,
            "min_distance": need,
            "distance": dist,
            "why": why,
            "rejected": rejected,
        }

    # Fallback: furthest candidate with buffer, even if under min distance
    if cands:
        lv = cands[-1]
        level = float(lv["strike"])
        tag = str(lv.get("tag") or "")
        label = tag or f"{level:g}"
        stop = level - buf if direction == "Bullish" else level + buf
        dist = abs(spot - stop)
        return {
            "stop": stop,
            "level": level,
            "tag": tag,
            "buffer": buf,
            "min_distance": need,
            "distance": dist,
            "why": f"fallback {label} ± buffer (no level met min dist {need:.0f})",
            "rejected": rejected,
        }
    return {
        "stop": None,
        "level": None,
        "tag": None,
        "buffer": buf,
        "min_distance": need,
        "distance": None,
        "why": "no protective level",
        "rejected": rejected,
    }


def atm_iv(call_iv: float | None, put_iv: float | None) -> float | None:
    """Average of ATM call and put IV (percent) when both exist; otherwise the one that exists."""
    vals = [v for v in (call_iv, put_iv) if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def iv_to_sigma(iv_pct: float) -> float:
    """Upstox IV percent → decimal volatility for BS / expected-move math."""
    return float(iv_pct) / 100.0


def iv_vs_vix(atm: float | None, vix: float | None, *, band: float = VIX_BAND) -> dict[str, Any]:
    """Compare ATM IV to India VIX (±band). Both in percent points."""
    if atm is None or vix is None:
        return {
            "atm_iv": atm,
            "india_vix": vix,
            "diff": None,
            "verdict": None,
            "reason": "need ATM IV and India VIX",
        }
    diff = atm - vix
    if atm > vix + band:
        verdict = "Options expensive"
    elif atm < vix - band:
        verdict = "Options cheap"
    else:
        verdict = "Fair"
    return {
        "atm_iv": atm,
        "india_vix": vix,
        "diff": diff,
        "verdict": verdict,
        "reason": None,
    }


def _closest_iv_by_delta(rows: list[dict[str, Any]], side: Literal["call", "put"], target: float) -> float | None:
    best: tuple[float, float] | None = None
    for r in rows:
        leg = r.get(side) or {}
        d = leg.get("delta")
        iv = leg.get("iv")
        if d is None or iv is None:
            continue
        err = abs(float(d) - target)
        if best is None or err < best[0]:
            best = (err, float(iv))
    return None if best is None else best[1]


def iv_skew(rows: list[dict[str, Any]], *, target: float = SKEW_DELTA) -> float | None:
    """Put IV at Δ≈−target minus call IV at Δ≈+target. Positive = fear / downside demand."""
    put_iv = _closest_iv_by_delta(rows, "put", -target)
    call_iv = _closest_iv_by_delta(rows, "call", target)
    if put_iv is None or call_iv is None:
        return None
    return put_iv - call_iv


def _atm_iv_from_snapshot(snap: dict[str, Any]) -> float | None:
    atm = snap.get("atm")
    strikes = snap.get("strikes") or []
    if atm is None:
        return None
    for s in strikes:
        if float(s.get("strike") or 0) != float(atm):
            continue
        call = s.get("call") or {}
        put = s.get("put") or {}
        return atm_iv(
            float(call["iv"]) if call.get("iv") is not None else None,
            float(put["iv"]) if put.get("iv") is not None else None,
        )
    return None


def _day_from_path(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def pick_iv_expiry_for_day(day: date, expiries: list[str], *, min_dte: int = IV_MIN_DTE_DAYS) -> str | None:
    """Nearest expiry with ≥ min_dte calendar days left (skip the dying near contract)."""
    eligible: list[tuple[int, str]] = []
    for exp in expiries:
        try:
            ed = date.fromisoformat(exp)
        except ValueError:
            continue
        dte = (ed - day).days
        if dte >= min_dte:
            eligible.append((dte, exp))
    if not eligible:
        return None
    eligible.sort()
    return eligible[0][1]


def daily_atm_iv_series(options_dir: Path, *, min_dte: int = IV_MIN_DTE_DAYS) -> list[dict[str, Any]]:
    """One ATM-IV point per day: last snap of the nearest expiry with ≥ min_dte days left."""
    if not options_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(options_dir.glob("*.jsonl")):
        day = _day_from_path(path.stem)
        if day is None:
            continue
        by_expiry: dict[str, dict[str, Any]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            exp = row.get("expiry")
            if not isinstance(exp, str):
                continue
            prev = by_expiry.get(exp)
            if prev is None or int(row.get("t") or 0) >= int(prev.get("t") or 0):
                by_expiry[exp] = row
        chosen = pick_iv_expiry_for_day(day, list(by_expiry.keys()), min_dte=min_dte)
        if chosen is None:
            continue
        snap = by_expiry[chosen]
        iv = _atm_iv_from_snapshot(snap)
        if iv is None:
            continue
        out.append({"day": day.isoformat(), "atm_iv": iv, "expiry": chosen, "t": int(snap.get("t") or 0)})
    return out


def iv_percentile(current: float | None, history: list[dict[str, Any]], *, need: int = IV_HISTORY_DAYS) -> dict[str, Any]:
    """Percentile of current ATM IV vs daily history; placeholder until `need` days exist."""
    if current is None:
        return {
            "available": False,
            "label": "Building history",
            "percentile": None,
            "days": len(history),
            "need": need,
        }
    if len(history) < need:
        return {
            "available": False,
            "label": "Building history",
            "percentile": None,
            "days": len(history),
            "need": need,
        }
    vals = sorted(float(h["atm_iv"]) for h in history[-need:])
    below = sum(1 for v in vals if v <= current)
    pct = 100.0 * below / len(vals)
    return {
        "available": True,
        "label": f"{pct:.0f}th percentile",
        "percentile": pct,
        "days": len(vals),
        "need": need,
    }


def expected_move_straddle(call_ltp: float, put_ltp: float, *, factor: float = STRADDLE_FACTOR) -> float:
    return (float(call_ltp) + float(put_ltp)) * factor


def expected_move_iv(spot: float, atm_iv_pct: float, t_years: float) -> float:
    if spot <= 0 or atm_iv_pct <= 0 or t_years < 0:
        return 0.0
    return float(spot) * iv_to_sigma(atm_iv_pct) * math.sqrt(t_years)


def expected_move_bands(spot: float, move: float) -> dict[str, float]:
    return {"move": move, "upper": spot + move, "lower": spot - move}


def prob_close_beyond(spot: float, level: float, sigma: float, t_years: float) -> float | None:
    """P(S_T beyond level at expiry). Upside N(d2); downside N(−d2). r = q = 0."""
    if spot <= 0 or level <= 0 or sigma <= 0 or t_years <= 0:
        return None
    vs = sigma * math.sqrt(t_years)
    d2 = (math.log(spot / level) - 0.5 * sigma * sigma * t_years) / vs
    if level >= spot:
        return ncdf(d2)
    return ncdf(-d2)


def prob_touch(p_close: float | None, *, cap: float = TOUCH_CAP) -> float | None:
    if p_close is None:
        return None
    return min(cap, 2.0 * p_close)


def attach_level_probs(
    levels: list[dict[str, Any]],
    *,
    spot: float,
    atm_iv_pct: float | None,
    t_years: float,
) -> list[dict[str, Any]]:
    if atm_iv_pct is None or atm_iv_pct <= 0 or t_years <= 0:
        return [{**lvl, "p_close": None, "p_touch": None} for lvl in levels]
    sigma = iv_to_sigma(atm_iv_pct)
    out = []
    for lvl in levels:
        strike = float(lvl["strike"])
        pc = prob_close_beyond(spot, strike, sigma, t_years)
        out.append({**lvl, "p_close": pc, "p_touch": prob_touch(pc)})
    return out


def _level_prob(obj: dict[str, Any] | None, *, spot: float, sigma: float, t_years: float) -> dict[str, Any] | None:
    if obj is None:
        return None
    strike = float(obj["strike"])
    pc = 0.5 if abs(strike - spot) < 1e-9 else prob_close_beyond(spot, strike, sigma, t_years)
    return {**obj, "p_close": pc, "p_touch": prob_touch(pc)}


def taylor_price(
    ltp: float,
    delta: float,
    gamma: float,
    move: float,
    *,
    theta: float = 0.0,
    t_hold: float = 0.0,
) -> float:
    """ΔP ≈ δ·ΔS + ½γ·(ΔS)² + θ·t_hold; floored at 0. θ is premium/day."""
    est = float(ltp) + float(delta) * move + 0.5 * float(gamma) * move * move + float(theta) * float(t_hold)
    return max(0.0, est)


def _pick_by_delta(legs: list[dict[str, Any]], lo: float, hi: float) -> dict[str, Any] | None:
    mid = (lo + hi) / 2.0
    in_band: list[tuple[float, dict[str, Any]]] = []
    any_band: list[tuple[float, dict[str, Any]]] = []
    for item in legs:
        d = abs(float(item["delta"]))
        err = abs(d - mid)
        any_band.append((err, item))
        if lo <= d <= hi:
            in_band.append((err, item))
    pool = in_band or any_band
    if not pool:
        return None
    pool.sort(key=lambda x: (x[0], x[1]["strike"]))
    return pool[0][1]


def next_resistance(resistance: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_tag = {r.get("tag"): r for r in resistance}
    return by_tag.get("R1") or (resistance[0] if resistance else None)


def next_support(support: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_tag = {s.get("tag"): s for s in support}
    return by_tag.get("S1") or (support[0] if support else None)


def _protective_levels(
    *,
    direction: Direction,
    resistance: list[dict[str, Any]],
    support: list[dict[str, Any]],
    pivot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Ordered list used by choose_stop (Pivot + S1..S3 or R1..R3)."""
    out: list[dict[str, Any]] = []
    if pivot:
        out.append({"strike": float(pivot["strike"]), "tag": "PIVOT"})
    if direction == "Bullish":
        for tag in ("S1", "S2", "S3"):
            for s in support:
                if s.get("tag") == tag:
                    out.append({"strike": float(s["strike"]), "tag": tag})
    else:
        for tag in ("R1", "R2", "R3"):
            for r in resistance:
                if r.get("tag") == tag:
                    out.append({"strike": float(r["strike"]), "tag": tag})
    return out


def build_strike_picker(
    rows: list[dict[str, Any]],
    *,
    spot: float,
    direction: Direction,
    target: float | None,
    stop_info: dict[str, Any],
    lot_size: int | None,
    t_hold: float,
    hold_mode: HoldMode,
) -> dict[str, Any]:
    side: Literal["call", "put"] = "call" if direction == "Bullish" else "put"
    stop = stop_info.get("stop")
    pool: list[dict[str, Any]] = []
    for r in rows:
        leg = r.get(side) or {}
        d = leg.get("delta")
        if d is None:
            continue
        pool.append(
            {
                "strike": float(r["strike"]),
                "side": side,
                "ltp": float(leg.get("ltp") or 0.0),
                "delta": float(d),
                "gamma": float(leg.get("gamma") or 0.0),
                "theta": float(leg.get("theta") or 0.0),
                "iv": leg.get("iv"),
                "spread_pct": leg.get("spread_pct"),
            }
        )

    candidates = []
    for label, (lo, hi) in DELTA_BANDS.items():
        pick = _pick_by_delta(pool, lo, hi)
        if pick is None:
            continue
        ltp = pick["ltp"]
        theta = pick["theta"]
        theta_pct = (abs(theta) / ltp * 100.0) if ltp > 0 else None
        lot = int(lot_size) if lot_size else None
        cost = (ltp * lot) if lot else None
        at_target = (
            taylor_price(ltp, pick["delta"], pick["gamma"], float(target) - spot, theta=theta, t_hold=t_hold)
            if target is not None
            else None
        )
        at_stop = (
            taylor_price(ltp, pick["delta"], pick["gamma"], float(stop) - spot, theta=theta, t_hold=t_hold)
            if stop is not None
            else None
        )
        candidates.append(
            {
                "bucket": label,
                "strike": pick["strike"],
                "side": side,
                "ltp": ltp,
                "delta": pick["delta"],
                "gamma": pick["gamma"],
                "theta": theta,
                "theta_pct_premium": theta_pct,
                "iv": pick["iv"],
                "spread_pct": pick["spread_pct"],
                "lot_size": lot,
                "cost_per_lot": cost,
                "est_at_target": at_target,
                "est_at_stop": at_stop,
            }
        )

    return {
        "direction": direction,
        "side": side,
        "target": target,
        "stop": stop,
        "stop_level": stop_info.get("level"),
        "stop_tag": stop_info.get("tag"),
        "stop_why": stop_info.get("why"),
        "stop_buffer": stop_info.get("buffer"),
        "min_stop_distance": stop_info.get("min_distance"),
        "lot_size": lot_size,
        "t_hold": t_hold,
        "hold_mode": hold_mode,
        "hold_label": HOLD_LABELS[hold_mode],
        "candidates": candidates,
    }


def build_iv_panel(
    *,
    call_iv: float | None,
    put_iv: float | None,
    vix: float | None,
    rows: list[dict[str, Any]],
    options_dir: Path | None,
) -> dict[str, Any]:
    atm = atm_iv(call_iv, put_iv)
    cmp = iv_vs_vix(atm, vix)
    history = daily_atm_iv_series(options_dir) if options_dir is not None else []
    return {
        "atm_iv": atm,
        "call_iv": call_iv,
        "put_iv": put_iv,
        "india_vix": cmp["india_vix"],
        "diff": cmp["diff"],
        "verdict": cmp["verdict"],
        "skew": iv_skew(rows),
        "percentile": iv_percentile(atm, history),
        "iv_unit": UPSTOX_IV_UNIT,
        "theta_unit": UPSTOX_THETA_UNIT,
    }


def build_expected_move(
    *,
    spot: float,
    call_ltp: float,
    put_ltp: float,
    atm_iv_pct: float | None,
    now: int | float,
    expiry: str,
) -> dict[str, Any]:
    t_years = years_to_expiry_calendar(now, expiry)
    straddle_move = expected_move_straddle(call_ltp, put_ltp)
    straddle = expected_move_bands(spot, straddle_move)
    iv_move = expected_move_iv(spot, atm_iv_pct, t_years) if atm_iv_pct is not None else None
    iv_band = expected_move_bands(spot, iv_move) if iv_move is not None else None
    return {
        "t_years": t_years,
        "straddle": straddle,
        "iv": iv_band,
        "primary": "straddle",
        "move": straddle["move"],
        "upper": straddle["upper"],
        "lower": straddle["lower"],
    }


def _picker_for(
    rows: list[dict[str, Any]],
    *,
    spot: float,
    direction: Direction,
    resistance: list[dict[str, Any]],
    support: list[dict[str, Any]],
    pivot: dict[str, Any] | None,
    expected_move: float | None,
    lot_size: int | None,
    now: int | float,
    expiry: str,
    hold_mode: HoldMode,
) -> dict[str, Any]:
    if direction == "Bullish":
        tgt = next_resistance(resistance)
        target = float(tgt["strike"]) if tgt else None
    else:
        tgt = next_support(support)
        target = float(tgt["strike"]) if tgt else None
    levels = _protective_levels(direction=direction, resistance=resistance, support=support, pivot=pivot)
    stop_info = choose_stop(spot=spot, direction=direction, levels=levels, expected_move=expected_move)
    th = t_hold_days(now, expiry, hold_mode)
    return build_strike_picker(
        rows,
        spot=spot,
        direction=direction,
        target=target,
        stop_info=stop_info,
        lot_size=lot_size,
        t_hold=th,
        hold_mode=hold_mode,
    )


def enrich_phase3(
    view: dict[str, Any],
    *,
    vix: float | None = None,
    options_dir: Path | None = None,
    lot_size: int | None = None,
    hold_mode: HoldMode = "1d",
) -> dict[str, Any]:
    """Attach Phase 3 blocks onto a chain view (mutates and returns `view`)."""
    if not view.get("available"):
        view["iv_panel"] = None
        view["expected_move"] = None
        view["strike_picker"] = None
        return view

    spot = float(view["spot"])
    atm = float(view["atm"])
    now = int(view["as_of"])
    expiry = str(view["expiry"])
    all_rows = view.get("all_strikes") or [
        {"strike": r["strike"], "call": r["call"], "put": r["put"]} for r in view.get("rows", [])
    ]

    atm_row = next((r for r in all_rows if float(r["strike"]) == atm), None)
    call_iv = atm_row["call"].get("iv") if atm_row else None
    put_iv = atm_row["put"].get("iv") if atm_row else None
    call_ltp = float((atm_row["call"].get("ltp") if atm_row else 0) or 0)
    put_ltp = float((atm_row["put"].get("ltp") if atm_row else 0) or 0)

    iv_panel = build_iv_panel(
        call_iv=float(call_iv) if call_iv is not None else None,
        put_iv=float(put_iv) if put_iv is not None else None,
        vix=vix,
        rows=all_rows,
        options_dir=options_dir,
    )
    atm_iv_pct = iv_panel["atm_iv"]
    t_years = years_to_expiry_calendar(now, expiry)

    view["iv_panel"] = iv_panel
    em = build_expected_move(
        spot=spot,
        call_ltp=call_ltp,
        put_ltp=put_ltp,
        atm_iv_pct=atm_iv_pct,
        now=now,
        expiry=expiry,
    )
    view["expected_move"] = em

    if atm_iv_pct is not None and atm_iv_pct > 0 and t_years > 0:
        sigma = iv_to_sigma(atm_iv_pct)
        view["resistance"] = attach_level_probs(
            view.get("resistance") or [], spot=spot, atm_iv_pct=atm_iv_pct, t_years=t_years
        )
        view["support"] = attach_level_probs(
            view.get("support") or [], spot=spot, atm_iv_pct=atm_iv_pct, t_years=t_years
        )
        view["pivot"] = _level_prob(view.get("pivot"), spot=spot, sigma=sigma, t_years=t_years)
        view["max_pain"] = _level_prob(view.get("max_pain"), spot=spot, sigma=sigma, t_years=t_years)
    else:
        view["resistance"] = attach_level_probs(
            view.get("resistance") or [], spot=spot, atm_iv_pct=None, t_years=t_years
        )
        view["support"] = attach_level_probs(
            view.get("support") or [], spot=spot, atm_iv_pct=None, t_years=t_years
        )

    resistance = view.get("resistance") or []
    support = view.get("support") or []
    pivot = view.get("pivot")
    common = dict(
        rows=all_rows,
        spot=spot,
        resistance=resistance,
        support=support,
        pivot=pivot,
        expected_move=em.get("move"),
        lot_size=lot_size,
        now=now,
        expiry=expiry,
        hold_mode=hold_mode,
    )
    bull = _picker_for(direction="Bullish", **common)  # type: ignore[arg-type]
    bear = _picker_for(direction="Bearish", **common)  # type: ignore[arg-type]
    view["strike_picker"] = {
        "lot_size": lot_size,
        "hold_mode": hold_mode,
        "hold_modes": list(HOLD_LABELS.keys()),
        "Bullish": bull,
        "Bearish": bear,
        "active": bull,
    }
    return view


def rebuild_strike_picker(
    view: dict[str, Any],
    *,
    direction: Direction,
    hold_mode: HoldMode = "1d",
) -> dict[str, Any] | None:
    """Rebuild one side of the picker (used when the UI changes hold / direction without refetch)."""
    if not view.get("available"):
        return None
    all_rows = view.get("all_strikes") or [
        {"strike": r["strike"], "call": r["call"], "put": r["put"]} for r in view.get("rows", [])
    ]
    em = (view.get("expected_move") or {}).get("move")
    lot = (view.get("strike_picker") or {}).get("lot_size")
    return _picker_for(
        all_rows,
        spot=float(view["spot"]),
        direction=direction,
        resistance=view.get("resistance") or [],
        support=view.get("support") or [],
        pivot=view.get("pivot"),
        expected_move=em,
        lot_size=lot,
        now=int(view["as_of"]),
        expiry=str(view["expiry"]),
        hold_mode=hold_mode,
    )


def india_vix_at(when: int, *, candles_dir: Path) -> float | None:
    """Latest India VIX open at or before `when` from the candle store."""
    from app.data.store import CandleStore, SymbolNotFound
    from app.options.vix import vix_asof

    try:
        rows, _ = CandleStore(candles_dir).load("NSE_INDEX_India_VIX", to_time=when)
    except (SymbolNotFound, OSError, ValueError):
        return None
    quote = vix_asof([{"time": row["time"], "open": row["open"]} for row in rows], when)
    return None if quote is None else quote.value
