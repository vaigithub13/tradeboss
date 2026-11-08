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

from collections.abc import Callable

from app.options.bs import ncdf
from app.options.time import IST, SESSION_MIN, trading_minutes_to_expiry

VIX_BAND = 2.0
SKEW_DELTA = 0.25
IV_HISTORY_DAYS = 20
IV_MIN_DTE_DAYS = 3  # skip nearest expiry on its last calendar days
T_FLOOR_MINUTES = 15
TRADING_DAYS_PER_YEAR = 252  # T = trading_days / 252 for EM + probs
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
TargetMode = Literal["Auto", "R1", "R2", "R3", "S1", "S2", "S3"]

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

# Strike-picker quality gates (✅ pass / ⚠️ warn / ❌ fail)
SPREAD_PASS_PCT = 2.0
SPREAD_WARN_PCT = 3.0
RR_PASS = 2.0
RR_WARN = 1.5
THETA_DRAG_PASS = 0.10
THETA_DRAG_WARN = 0.15
LOSS_PASS_PCT = 0.40  # |est_loss| / premium
LOSS_WARN_PCT = 0.70
NO_SETUP_MSG = "No good buy setup now"
NO_EDGE_MSG = "No edge without a directional signal — R:R alone isn't enough"
AUTO_RR = 2.0
DEFAULT_ROUND_TRIP_CHARGES = 60.0  # ₹ per lot round-trip (brokerage + taxes approx)


def years_to_expiry_trading(
    now: int | float,
    expiry: str | date,
    is_trading_day: Callable[[date], bool],
    *,
    floor_min: int = T_FLOOR_MINUTES,
) -> float:
    """Trading years to 15:30 IST: (session minutes left / 375) / 252.

    `is_trading_day` is the NSE holiday calendar (same source GEX/session code uses).
    Floored at `floor_min` session minutes so √T stays finite near expiry.
    """
    exp = date.fromisoformat(expiry) if isinstance(expiry, str) else expiry
    minutes = max(int(floor_min), trading_minutes_to_expiry(int(now), exp, is_trading_day))
    return (minutes / SESSION_MIN) / TRADING_DAYS_PER_YEAR


def trading_days_to_expiry(
    now: int | float,
    expiry: str | date,
    is_trading_day: Callable[[date], bool],
    *,
    floor_min: int = T_FLOOR_MINUTES,
) -> float:
    return years_to_expiry_trading(now, expiry, is_trading_day, floor_min=floor_min) * TRADING_DAYS_PER_YEAR


def t_hold_days(
    now: int | float,
    expiry: str | date,
    mode: HoldMode = "1d",
    *,
    is_trading_day: Callable[[date], bool] | None = None,
) -> float:
    """Holding horizon in days for θ·t_hold, capped by time left to expiry."""
    if is_trading_day is not None:
        left = trading_days_to_expiry(now, expiry, is_trading_day)
    else:
        # tests without a calendar: treat remaining clock as calendar days
        from app.options.time import calendar_minutes_to_expiry

        exp = date.fromisoformat(expiry) if isinstance(expiry, str) else expiry
        left = max(T_FLOOR_MINUTES, calendar_minutes_to_expiry(int(now), exp)) / (24 * 60)
    if mode == "1d":
        requested = 1.0
    elif mode == "2d":
        requested = 2.0
    else:
        local = datetime.fromtimestamp(int(now), IST)
        end = datetime(local.year, local.month, local.day, 15, 30, tzinfo=IST)
        if local >= end:
            requested = T_FLOOR_MINUTES / SESSION_MIN
        else:
            mins = max(T_FLOOR_MINUTES, int((end - local).total_seconds() // 60))
            requested = mins / SESSION_MIN
    return min(requested, left)


def stop_buffer(spot: float) -> float:
    return max(STOP_BUFFER_PTS, STOP_BUFFER_PCT * float(spot))


def min_stop_distance(spot: float, expected_move: float | None) -> float:
    em = float(expected_move) if expected_move is not None else 0.0
    return max(MIN_STOP_PTS, MIN_STOP_EM_FRAC * em)


def _volatility_stop(spot: float, direction: Direction, need: float) -> float:
    return float(spot) - need if direction == "Bullish" else float(spot) + need


def choose_stop(
    *,
    spot: float,
    direction: Direction,
    levels: list[dict[str, Any]],
    expected_move: float | None,
) -> dict[str, Any]:
    """Bullish: stop = min(level − buffer, spot − min_distance); bearish mirrored.

    Walk to the next level only when it is within 1× expected move of spot; otherwise
    use the volatility stop. Never a 500-pt structural stop for a ~170-pt target.
    """
    buf = stop_buffer(spot)
    need = min_stop_distance(spot, expected_move)
    em = float(expected_move) if expected_move is not None else 0.0
    vol = _volatility_stop(spot, direction, need)

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
        label = tag or f"{level:g}"
        dist_level = abs(spot - level)
        if em > 0 and dist_level > em + 1e-9:
            rejected.append(
                {"level": level, "tag": tag, "reason": f"{label} beyond 1×EM ({dist_level:.0f} > {em:.0f})"}
            )
            break

        stop = min(level - buf, vol) if direction == "Bullish" else max(level + buf, vol)
        dist = abs(spot - stop)
        used_vol = abs(stop - vol) < 1e-6
        if used_vol:
            why = f"volatility stop (spot {'−' if direction == 'Bullish' else '+'} min {need:.0f})"
            if tag:
                why += f"; from {label}"
        else:
            why = (
                f"{label} {'−' if direction == 'Bullish' else '+'} buffer {buf:.0f}; "
                f"dist {dist:.0f} ≥ min {need:.0f}"
            )
        if rejected:
            why += f"; skipped {[r['tag'] or r['level'] for r in rejected]}"
        return {
            "stop": stop,
            "level": None if used_vol else level,
            "tag": "VOL" if used_vol else tag,
            "buffer": buf,
            "min_distance": need,
            "distance": dist,
            "why": why,
            "volatility_stop": used_vol,
            "rejected": rejected,
        }

    dist = abs(spot - vol)
    why = f"volatility stop (spot {'−' if direction == 'Bullish' else '+'} min {need:.0f})"
    if rejected:
        why += f"; skipped {[r['tag'] or r['level'] for r in rejected]}"
    return {
        "stop": vol,
        "level": None,
        "tag": "VOL",
        "buffer": buf,
        "min_distance": need,
        "distance": dist,
        "why": why,
        "volatility_stop": True,
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


def expected_move_straddle(call_ltp: float, put_ltp: float) -> float:
    """Market-priced move = ATM call LTP + ATM put LTP (no discount factor)."""
    return float(call_ltp) + float(put_ltp)


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


def _check_status(value: float | None, *, pass_at: float, warn_at: float, higher_is_better: bool) -> str:
    if value is None or math.isnan(value):
        return "fail"
    if higher_is_better:
        if value >= pass_at:
            return "pass"
        if value >= warn_at:
            return "warn"
        return "fail"
    if value <= pass_at:
        return "pass"
    if value <= warn_at:
        return "warn"
    return "fail"


def score_candidate(c: dict[str, Any], *, t_hold: float) -> dict[str, Any]:
    """Attach est_gain / est_loss / R:R / checks; returns a new dict."""
    ltp = float(c.get("ltp") or 0.0)
    at_target = c.get("est_at_target")
    at_stop = c.get("est_at_stop")
    est_gain = (float(at_target) - ltp) if at_target is not None else None
    est_loss = (ltp - float(at_stop)) if at_stop is not None else None
    rr = (est_gain / est_loss) if est_gain is not None and est_loss is not None and est_loss > 0 else None
    spread = c.get("spread_pct")
    spread_v = float(spread) if spread is not None else None
    drag = (abs(float(c.get("theta") or 0.0)) * float(t_hold) / ltp) if ltp > 0 else None

    loss_pct = (est_loss / ltp) if est_loss is not None and ltp > 0 else None
    checks = [
        {
            "id": "spread",
            "label": "Spread",
            "status": _check_status(spread_v, pass_at=SPREAD_PASS_PCT, warn_at=SPREAD_WARN_PCT, higher_is_better=False)
            if spread_v is not None
            else "fail",
            "detail": None if spread_v is None else f"{spread_v:.2f}%",
        },
        {
            "id": "rr",
            "label": "R:R",
            "status": _check_status(rr, pass_at=RR_PASS, warn_at=RR_WARN, higher_is_better=True),
            "detail": None if rr is None else f"{rr:.2f}",
        },
        {
            "id": "loss",
            "label": "Est. loss",
            "status": _check_status(loss_pct, pass_at=LOSS_PASS_PCT, warn_at=LOSS_WARN_PCT, higher_is_better=False)
            if loss_pct is not None and est_loss is not None and est_loss > 0
            else "fail",
            "detail": None if est_loss is None else f"-{est_loss:.2f}",
        },
        {
            "id": "theta_drag",
            "label": "θ drag",
            "status": _check_status(drag, pass_at=THETA_DRAG_PASS, warn_at=THETA_DRAG_WARN, higher_is_better=False)
            if drag is not None
            else "fail",
            "detail": None if drag is None else f"{drag * 100:.1f}%",
        },
    ]
    failed = [ch["label"] for ch in checks if ch["status"] == "fail"]
    out = {
        **c,
        "est_gain": est_gain,
        "est_loss": est_loss,
        "reward_risk": rr,
        "breakeven_win": breakeven_win_pct(rr),
        "checks": checks,
        "passed": len(failed) == 0,
        "failed_checks": failed,
    }
    return out


def driftless_win_chance(stop_dist: float | None, target_dist: float | None) -> float | None:
    """P(hit target before stop) ≈ stop_dist / (stop_dist + target_dist), driftless."""
    if stop_dist is None or target_dist is None:
        return None
    sd, td = float(stop_dist), float(target_dist)
    if sd <= 0 or td <= 0:
        return None
    return sd / (sd + td)


def breakeven_win_pct(rr: float | None) -> float | None:
    """Win rate needed for zero EV before charges: 1 / (1 + R:R)."""
    if rr is None or math.isnan(rr) or rr < 0:
        return None
    return 1.0 / (1.0 + float(rr))


def ev_per_lot(
    *,
    win: float,
    gain: float,
    loss: float,
    lot_size: int,
    charges: float = DEFAULT_ROUND_TRIP_CHARGES,
) -> float:
    """(win×gain − (1−win)×loss) × lot_size − charges, in ₹."""
    return (float(win) * float(gain) - (1.0 - float(win)) * float(loss)) * int(lot_size) - float(charges)


def attach_ev_metrics(
    candidates: list[dict[str, Any]],
    *,
    stop_dist: float | None,
    target_dist: float | None,
    lot_size: int | None,
    charges: float = DEFAULT_ROUND_TRIP_CHARGES,
    win: float | None = None,
) -> tuple[list[dict[str, Any]], float | None]:
    """Attach win_chance / breakeven / EV; `win` overrides driftless when set."""
    driftless = driftless_win_chance(stop_dist, target_dist)
    use_win = float(win) if win is not None else driftless
    out: list[dict[str, Any]] = []
    for c in candidates:
        gain = c.get("est_gain")
        loss = c.get("est_loss")
        rr = c.get("reward_risk")
        be = c.get("breakeven_win")
        if be is None:
            be = breakeven_win_pct(float(rr) if rr is not None else None)
        ev = None
        if (
            use_win is not None
            and gain is not None
            and loss is not None
            and float(loss) > 0
            and lot_size
        ):
            ev = ev_per_lot(
                win=use_win,
                gain=float(gain),
                loss=float(loss),
                lot_size=int(lot_size),
                charges=charges,
            )
        out.append(
            {
                **c,
                "win_chance": use_win,
                "win_chance_driftless": driftless,
                "breakeven_win": be,
                "ev_per_lot": ev,
                "round_trip_charges": float(charges),
            }
        )
    return out, driftless


def apply_picker_highlight(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """★ best EV > 0 with no ❌ checks; else no-edge / no-setup message."""
    scored = sorted(
        list(candidates),
        key=lambda c: (
            float(c["ev_per_lot"]) if c.get("ev_per_lot") is not None else float("-inf"),
            float(c["reward_risk"]) if c.get("reward_risk") is not None else float("-inf"),
        ),
        reverse=True,
    )
    highlight: str | None = None
    closest: str | None = None
    closest_fails: list[str] = []
    message: str | None = None

    def ev_key(c: dict[str, Any]) -> float:
        ev = c.get("ev_per_lot")
        return float(ev) if ev is not None else float("-inf")

    eligible = [
        c
        for c in scored
        if c.get("passed") and c.get("ev_per_lot") is not None and float(c["ev_per_lot"]) > 0
    ]
    if eligible:
        best = max(eligible, key=ev_key)
        highlight = str(best["bucket"])
        for c in scored:
            c["highlighted"] = c["bucket"] == highlight
            c["closest_failed"] = False
    else:
        evs = [c.get("ev_per_lot") for c in scored if c.get("ev_per_lot") is not None]
        if evs and all(float(ev) <= 0 for ev in evs):
            message = NO_EDGE_MSG
        elif any(c.get("passed") for c in scored):
            message = NO_EDGE_MSG
        else:
            message = NO_SETUP_MSG
        if scored:
            closest_c = max(scored, key=ev_key)
            closest = str(closest_c["bucket"])
            closest_fails = list(closest_c.get("failed_checks") or [])
            for c in scored:
                c["highlighted"] = False
                c["closest_failed"] = c["bucket"] == closest
        else:
            for c in scored:
                c["highlighted"] = False
                c["closest_failed"] = False

    return {
        "candidates": scored,
        "highlight": highlight,
        "closest": closest,
        "closest_fails": closest_fails,
        "setup_message": message,
        "has_setup": highlight is not None,
    }


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
    round_trip_charges: float = DEFAULT_ROUND_TRIP_CHARGES,
    signal_win: float | None = None,
) -> dict[str, Any]:
    side: Literal["call", "put"] = "call" if direction == "Bullish" else "put"
    stop = stop_info.get("stop")
    stop_distance = stop_info.get("distance")
    if stop_distance is None and stop is not None:
        stop_distance = abs(float(spot) - float(stop))
    target_distance = abs(float(target) - float(spot)) if target is not None else None
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

    raw_candidates = []
    for label, (lo, hi_band) in DELTA_BANDS.items():
        pick = _pick_by_delta(pool, lo, hi_band)
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
        scored = score_candidate(
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
            },
            t_hold=t_hold,
        )
        raw_candidates.append(scored)

    with_ev, driftless = attach_ev_metrics(
        raw_candidates,
        stop_dist=float(stop_distance) if stop_distance is not None else None,
        target_dist=target_distance,
        lot_size=int(lot_size) if lot_size else None,
        charges=round_trip_charges,
        win=signal_win,
    )
    hi = apply_picker_highlight(with_ev)
    return {
        "direction": direction,
        "side": side,
        "target": target,
        "target_distance": target_distance,
        "stop": stop,
        "stop_distance": stop_distance,
        "stop_level": stop_info.get("level"),
        "stop_tag": stop_info.get("tag"),
        "stop_why": stop_info.get("why"),
        "stop_buffer": stop_info.get("buffer"),
        "min_stop_distance": stop_info.get("min_distance"),
        "volatility_stop": bool(stop_info.get("volatility_stop")),
        "win_chance": signal_win if signal_win is not None else driftless,
        "win_chance_driftless": driftless,
        "round_trip_charges": float(round_trip_charges),
        "lot_size": lot_size,
        "t_hold": t_hold,
        "hold_mode": hold_mode,
        "hold_label": HOLD_LABELS[hold_mode],
        "candidates": hi["candidates"],
        "highlight": hi["highlight"],
        "closest": hi["closest"],
        "closest_fails": hi["closest_fails"],
        "setup_message": hi["setup_message"],
        "has_setup": hi["has_setup"],
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
    is_trading_day: Callable[[date], bool],
) -> dict[str, Any]:
    """Two labelled moves: market-priced ATM straddle, and 1σ IV range (≈68%)."""
    t_years = years_to_expiry_trading(now, expiry, is_trading_day)
    trading_days = t_years * TRADING_DAYS_PER_YEAR
    straddle_move = expected_move_straddle(call_ltp, put_ltp)
    straddle = expected_move_bands(spot, straddle_move)
    iv_move = expected_move_iv(spot, atm_iv_pct, t_years) if atm_iv_pct is not None else None
    iv_band = expected_move_bands(spot, iv_move) if iv_move is not None else None
    return {
        "t_years": t_years,
        "trading_days": trading_days,
        "market_priced": {
            **straddle,
            "label": "Market-priced move (ATM straddle)",
        },
        "one_sigma": {
            **(iv_band or {"move": 0.0, "upper": spot, "lower": spot}),
            "label": "1σ range (IV), ≈68% probability",
            "available": iv_band is not None,
        },
        # backwards-compatible aliases (market-priced is primary for stops / walk distance)
        "straddle": straddle,
        "iv": iv_band,
        "primary": "market_priced",
        "move": straddle["move"],
        "upper": straddle["upper"],
        "lower": straddle["lower"],
    }


def level_by_tag(levels: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    for lv in levels:
        if lv.get("tag") == tag:
            return lv
    return None


def target_ladder(direction: Direction, resistance: list[dict[str, Any]], support: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Ordered target choices nearest-first: R1→R3 (bull) or S1→S3 (bear)."""
    tags = ("R1", "R2", "R3") if direction == "Bullish" else ("S1", "S2", "S3")
    pool = resistance if direction == "Bullish" else support
    out: list[tuple[str, float]] = []
    for tag in tags:
        lv = level_by_tag(pool, tag)
        if lv is not None:
            out.append((tag, float(lv["strike"])))
    return out


def best_rr(candidates: list[dict[str, Any]]) -> float | None:
    rrs = [c.get("reward_risk") for c in candidates if c.get("reward_risk") is not None]
    return max(rrs) if rrs else None


def needed_move_for_rr2(
    *,
    spot: float,
    direction: Direction,
    candidate: dict[str, Any],
    stop: float | None,
    t_hold: float,
) -> float | None:
    """Index level where this candidate would reach R:R = 2 (linear δ approx + γ)."""
    if stop is None or candidate.get("ltp") is None:
        return None
    ltp = float(candidate["ltp"])
    delta = float(candidate["delta"])
    gamma = float(candidate.get("gamma") or 0.0)
    theta = float(candidate.get("theta") or 0.0)
    at_stop = taylor_price(ltp, delta, gamma, float(stop) - spot, theta=theta, t_hold=t_hold)
    est_loss = ltp - at_stop
    if est_loss <= 0:
        return None
    need_gain = AUTO_RR * est_loss
    # Solve  δ m + ½ γ m² + θ t ≈ need_gain  for move m (bullish call m>0)
    # 0.5 γ m² + δ m + (θ t − need_gain) = 0
    a = 0.5 * gamma
    b = delta
    c = theta * t_hold - need_gain
    if abs(a) < 1e-12:
        if abs(b) < 1e-12:
            return None
        m = -c / b
    else:
        disc = b * b - 4 * a * c
        if disc < 0:
            return None
        r1 = (-b + math.sqrt(disc)) / (2 * a)
        r2 = (-b - math.sqrt(disc)) / (2 * a)
        # prefer the root in the trade direction
        roots = [r for r in (r1, r2) if (r >= 0 if direction == "Bullish" else r <= 0)]
        if not roots:
            roots = [r1, r2]
        m = min(roots, key=lambda x: abs(x)) if direction == "Bullish" else max(roots, key=lambda x: abs(x))
    return spot + m


def select_auto_target(
    rows: list[dict[str, Any]],
    *,
    spot: float,
    direction: Direction,
    resistance: list[dict[str, Any]],
    support: list[dict[str, Any]],
    stop_info: dict[str, Any],
    lot_size: int | None,
    t_hold: float,
    hold_mode: HoldMode,
) -> dict[str, Any]:
    """Nearest R/S where the best candidate hits R:R ≥ 2; else a needs-move message."""
    ladder = target_ladder(direction, resistance, support)
    chosen: dict[str, Any] | None = None
    for tag, strike in ladder:
        built = build_strike_picker(
            rows,
            spot=spot,
            direction=direction,
            target=strike,
            stop_info=stop_info,
            lot_size=lot_size,
            t_hold=t_hold,
            hold_mode=hold_mode,
        )
        built["target_mode"] = tag
        built["target_tag"] = tag
        rr = best_rr(built["candidates"])
        if rr is not None and rr >= AUTO_RR:
            chosen = built
            break
    if chosen is not None:
        chosen["target_mode"] = "Auto"
        chosen["auto_resolved_tag"] = chosen.get("target_tag")
        chosen["auto_message"] = None
        return chosen

    # No level works — build against nearest target for display + needs-move hint
    fallback_tag, fallback_strike = ladder[0] if ladder else ("R1", spot)
    built = build_strike_picker(
        rows,
        spot=spot,
        direction=direction,
        target=fallback_strike if ladder else None,
        stop_info=stop_info,
        lot_size=lot_size,
        t_hold=t_hold,
        hold_mode=hold_mode,
    )
    # pick candidate with best R:R for the solve
    cands = [c for c in built["candidates"] if c.get("reward_risk") is not None]
    cands.sort(key=lambda c: float(c["reward_risk"]), reverse=True)
    need_lvl = None
    if cands:
        need_lvl = needed_move_for_rr2(
            spot=spot,
            direction=direction,
            candidate=cands[0],
            stop=stop_info.get("stop"),
            t_hold=t_hold,
        )
    if need_lvl is not None:
        msg = f"1:2 not possible with current levels — needs move to {need_lvl:.0f}"
    else:
        msg = "1:2 not possible with current levels"
    built["target_mode"] = "Auto"
    built["target_tag"] = None
    built["auto_resolved_tag"] = None
    built["auto_message"] = msg
    built["needs_move_to"] = need_lvl
    # Auto with no R:R≥2 is not a setup
    built["has_setup"] = False
    built["highlight"] = None
    built["setup_message"] = built.get("setup_message") or NO_SETUP_MSG
    for c in built["candidates"]:
        c["highlighted"] = False
    return built


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
    is_trading_day: Callable[[date], bool] | None = None,
    target_mode: TargetMode = "Auto",
) -> dict[str, Any]:
    levels = _protective_levels(direction=direction, resistance=resistance, support=support, pivot=pivot)
    stop_info = choose_stop(spot=spot, direction=direction, levels=levels, expected_move=expected_move)
    th = t_hold_days(now, expiry, hold_mode, is_trading_day=is_trading_day)

    if target_mode == "Auto":
        return select_auto_target(
            rows,
            spot=spot,
            direction=direction,
            resistance=resistance,
            support=support,
            stop_info=stop_info,
            lot_size=lot_size,
            t_hold=th,
            hold_mode=hold_mode,
        )

    pool = resistance if direction == "Bullish" else support
    lv = level_by_tag(pool, target_mode)
    target = float(lv["strike"]) if lv else None
    built = build_strike_picker(
        rows,
        spot=spot,
        direction=direction,
        target=target,
        stop_info=stop_info,
        lot_size=lot_size,
        t_hold=th,
        hold_mode=hold_mode,
    )
    built["target_mode"] = target_mode
    built["target_tag"] = target_mode if target is not None else None
    built["auto_message"] = None
    return built


def enrich_phase3(
    view: dict[str, Any],
    *,
    vix: float | None = None,
    options_dir: Path | None = None,
    lot_size: int | None = None,
    hold_mode: HoldMode = "1d",
    is_trading_day: Callable[[date], bool] | None = None,
) -> dict[str, Any]:
    """Attach Phase 3 blocks onto a chain view (mutates and returns `view`)."""
    if not view.get("available"):
        view["iv_panel"] = None
        view["expected_move"] = None
        view["strike_picker"] = None
        return view

    if is_trading_day is None:
        from app.backtest.expiry import load_default_calendar

        is_trading_day = load_default_calendar().is_trading_day

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
    t_years = years_to_expiry_trading(now, expiry, is_trading_day)

    view["iv_panel"] = iv_panel
    em = build_expected_move(
        spot=spot,
        call_ltp=call_ltp,
        put_ltp=put_ltp,
        atm_iv_pct=atm_iv_pct,
        now=now,
        expiry=expiry,
        is_trading_day=is_trading_day,
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
        is_trading_day=is_trading_day,
    )
    bull_auto = _picker_for(direction="Bullish", target_mode="Auto", **common)  # type: ignore[arg-type]
    bear_auto = _picker_for(direction="Bearish", target_mode="Auto", **common)  # type: ignore[arg-type]
    bull_targets = {
        "Auto": bull_auto,
        **{
            tag: _picker_for(direction="Bullish", target_mode=tag, **common)  # type: ignore[arg-type]
            for tag in ("R1", "R2", "R3")
            if level_by_tag(resistance, tag)
        },
    }
    bear_targets = {
        "Auto": bear_auto,
        **{
            tag: _picker_for(direction="Bearish", target_mode=tag, **common)  # type: ignore[arg-type]
            for tag in ("S1", "S2", "S3")
            if level_by_tag(support, tag)
        },
    }
    view["strike_picker"] = {
        "lot_size": lot_size,
        "hold_mode": hold_mode,
        "hold_modes": list(HOLD_LABELS.keys()),
        "target_mode": "Auto",
        "round_trip_charges": DEFAULT_ROUND_TRIP_CHARGES,
        "Bullish": bull_auto,
        "Bearish": bear_auto,
        "Bullish_targets": bull_targets,
        "Bearish_targets": bear_targets,
        "active": bull_auto,
    }
    return view


def rebuild_strike_picker(
    view: dict[str, Any],
    *,
    direction: Direction,
    hold_mode: HoldMode = "1d",
    target_mode: TargetMode = "Auto",
    is_trading_day: Callable[[date], bool] | None = None,
) -> dict[str, Any] | None:
    """Rebuild one side of the picker (used when the UI changes hold / direction without refetch)."""
    if not view.get("available"):
        return None
    if is_trading_day is None:
        from app.backtest.expiry import load_default_calendar

        is_trading_day = load_default_calendar().is_trading_day
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
        is_trading_day=is_trading_day,
        target_mode=target_mode,
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
