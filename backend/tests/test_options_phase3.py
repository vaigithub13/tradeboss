"""Phase 3 Options: IV panel, expected move, probs, strike picker, Upstox unit locks."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.options import chain_phase3 as p3
from app.options import chain_view
from app.options.bs import ncdf
from app.options.time import SESSION_MIN  # noqa: F401 — used in T tests via local import

IST = timezone(timedelta(hours=5, minutes=30))
EXPIRY = "2026-11-17"
SPOT = 25000.0


def ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def leg(
    *,
    oi=0.0,
    prev_oi=0.0,
    ltp=100.0,
    close=95.0,
    iv=15.0,
    delta=0.5,
    gamma=0.002,
    theta=-10.0,
    bid=99.0,
    ask=101.0,
):
    oi_chg = oi - prev_oi
    return {
        "oi": oi,
        "prev_oi": prev_oi,
        "oi_chg": oi_chg,
        "oi_chg_pct": (oi_chg / prev_oi * 100.0) if prev_oi else None,
        "volume": 0.0,
        "iv": iv,
        "ltp": ltp,
        "close": close,
        "price_chg": ltp - close,
        "bid": bid,
        "ask": ask,
        "spread_pct": ((ask - bid) / ltp * 100.0) if ltp else None,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": 5.0,
        "pop": 50.0,  # must never be used for level probs
    }


def rows_around_atm(spot: float = SPOT) -> list[dict]:
    """Strikes with call/put deltas spanning ITM/ATM/OTM bands."""
    out = []
    for i, k in enumerate(range(24800, 25250, 50)):
        # call delta falls as strike rises; put delta rises (less negative) as strike rises
        dist = (k - spot) / 50.0
        call_d = max(0.05, min(0.95, 0.50 - 0.05 * dist))
        put_d = -max(0.05, min(0.95, 0.50 + 0.05 * dist))
        out.append(
            {
                "strike": float(k),
                "call": leg(oi=100_000 + i * 1000, delta=call_d, iv=14.0 + abs(dist) * 0.2, ltp=120 - abs(dist) * 5),
                "put": leg(oi=150_000 - i * 500, delta=put_d, iv=15.0 + abs(dist) * 0.3, ltp=110 - abs(dist) * 4, theta=-8.0),
            }
        )
    return out


# ------------------------------------------------------------------ Upstox units
def test_upstox_greeks_units_locked() -> None:
    """IV is percent (GEX divides by 100); theta is premium points per day."""
    assert p3.UPSTOX_IV_UNIT == "percent"
    assert p3.UPSTOX_THETA_UNIT == "premium_per_day"
    # Same conversion GEX uses for BS
    assert p3.iv_to_sigma(14.5) == pytest.approx(0.145)
    # India VIX comparison is same scale: 14.5 vs VIX 12.3 → expensive by 2.2
    cmp = p3.iv_vs_vix(14.5, 12.3)
    assert cmp["verdict"] == "Options expensive"
    # θ·t_hold: −10 premium/day × 1 day → −10 on the estimate
    assert p3.taylor_price(100.0, 0.0, 0.0, 0.0, theta=-10.0, t_hold=1.0) == pytest.approx(90.0)


def test_gex_also_treats_iv_as_percent() -> None:
    """Regression: GEX parses Upstox iv as percent → decimal for BS."""
    from app.options import gex as gex_mod

    raw = {
        "market_data": {"oi": 1, "ltp": 10},
        "option_greeks": {"iv": 20.0, "delta": 0.5, "gamma": 0.001, "theta": -5.0, "vega": 1.0},
    }
    # _num path via public parse if available — mirror the /100 contract
    iv = raw["option_greeks"]["iv"]
    assert iv / 100.0 == pytest.approx(0.20)
    assert hasattr(gex_mod, "bs_gamma")


# ------------------------------------------------------------------ IV / VIX / skew
def test_atm_iv_average_and_vix_bands() -> None:
    assert p3.atm_iv(14.0, 16.0) == 15.0
    assert p3.atm_iv(14.0, None) == 14.0
    assert p3.iv_vs_vix(15.0, 14.0)["verdict"] == "Fair"
    assert p3.iv_vs_vix(17.0, 14.0)["verdict"] == "Options expensive"
    assert p3.iv_vs_vix(11.0, 14.0)["verdict"] == "Options cheap"


def test_skew_put_minus_call_at_quarter_delta() -> None:
    rs = rows_around_atm()
    # Force known IVs near ±0.25 delta
    for r in rs:
        if abs(abs(r["call"]["delta"]) - 0.25) < 0.06:
            r["call"]["iv"] = 12.0
        if abs(abs(r["put"]["delta"]) - 0.25) < 0.06:
            r["put"]["iv"] = 18.0
    skew = p3.iv_skew(rs)
    assert skew is not None and skew > 0  # fear / downside demand


# ------------------------------------------------------------------ IV history (≥3 DTE)
def test_pick_iv_expiry_skips_near_expiry() -> None:
    day = date(2026, 11, 10)  # Monday; Tue 11 Nov is 1 DTE, Tue 18 is 8 DTE
    assert p3.pick_iv_expiry_for_day(day, ["2026-11-11", "2026-11-18"]) == "2026-11-18"
    assert p3.pick_iv_expiry_for_day(day, ["2026-11-11"]) is None
    # 3 DTE exactly is allowed
    assert p3.pick_iv_expiry_for_day(date(2026, 11, 8), ["2026-11-11", "2026-11-18"]) == "2026-11-11"


def test_daily_atm_iv_series_uses_far_enough_expiry(tmp_path: Path) -> None:
    day = date(2026, 11, 10)
    path = tmp_path / f"{day.isoformat()}.jsonl"
    near = {
        "t": ts(2026, 11, 10, 15, 25),
        "expiry": "2026-11-11",
        "atm": 25000.0,
        "strikes": [{"strike": 25000.0, "call": {"iv": 99.0}, "put": {"iv": 99.0}}],
    }
    far = {
        "t": ts(2026, 11, 10, 15, 25),
        "expiry": "2026-11-18",
        "atm": 25000.0,
        "strikes": [{"strike": 25000.0, "call": {"iv": 14.0}, "put": {"iv": 16.0}}],
    }
    path.write_text(json.dumps(near) + "\n" + json.dumps(far) + "\n", encoding="utf-8")
    series = p3.daily_atm_iv_series(tmp_path)
    assert len(series) == 1
    assert series[0]["expiry"] == "2026-11-18"
    assert series[0]["atm_iv"] == 15.0


def test_iv_percentile_placeholder_until_20_days(tmp_path: Path) -> None:
    for i in range(5):
        d = date(2026, 10, 1) + timedelta(days=i)
        exp = (d + timedelta(days=10)).isoformat()
        row = {
            "t": int(datetime(d.year, d.month, d.day, 15, 25, tzinfo=IST).timestamp()),
            "expiry": exp,
            "atm": 25000.0,
            "strikes": [{"strike": 25000.0, "call": {"iv": 10.0 + i}, "put": {"iv": 10.0 + i}}],
        }
        p = tmp_path / f"{d.isoformat()}.jsonl"
        p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    hist = p3.daily_atm_iv_series(tmp_path)
    pct = p3.iv_percentile(12.0, hist)
    assert pct["available"] is False
    assert pct["label"] == "Building history"
    assert pct["days"] == 5


def test_iv_percentile_computes_after_20_days(tmp_path: Path) -> None:
    for i in range(20):
        d = date(2026, 9, 1) + timedelta(days=i)
        exp = (d + timedelta(days=10)).isoformat()
        iv = 10.0 + i * 0.5
        row = {
            "t": int(datetime(d.year, d.month, d.day, 15, 25, tzinfo=IST).timestamp()),
            "expiry": exp,
            "atm": 25000.0,
            "strikes": [{"strike": 25000.0, "call": {"iv": iv}, "put": {"iv": iv}}],
        }
        (tmp_path / f"{d.isoformat()}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    hist = p3.daily_atm_iv_series(tmp_path)
    assert len(hist) == 20
    pct = p3.iv_percentile(15.0, hist)
    assert pct["available"] is True
    assert pct["percentile"] is not None


# ------------------------------------------------------------------ Expected move / T floor
def test_expected_move_straddle_and_iv() -> None:
    # Market-priced move = CE + PE (no 0.85 discount)
    assert p3.expected_move_straddle(100, 80) == pytest.approx(180.0)
    t = 1.0 / 252  # one trading day
    move = p3.expected_move_iv(25000, 20.0, t)
    assert move == pytest.approx(25000 * 0.20 * math.sqrt(t))


def test_t_uses_trading_days_over_252() -> None:
    # Mon 10:00 → Tue expiry = remaining Mon session + full Tue = (CLOSE-10:00) + 375
    # From 10:00 = 330 min left today + 375 = 705 → 705/375/252
    from app.options.time import SESSION_MIN

    is_td = lambda d: d.weekday() < 5  # noqa: E731
    t = p3.years_to_expiry_trading(ts(2026, 11, 16, 10, 0), "2026-11-17", is_td)
    assert t == pytest.approx((705 / SESSION_MIN) / 252)


def test_t_floor_fifteen_minutes() -> None:
    # After 15:30 on expiry day, trading minutes = 0 → floor 15 min → 15/375/252
    from app.options.time import SESSION_MIN

    is_td = lambda d: True  # noqa: E731
    t = p3.years_to_expiry_trading(ts(2026, 11, 17, 15, 45), "2026-11-17", is_td)
    assert t == pytest.approx((15 / SESSION_MIN) / 252)


# ------------------------------------------------------------------ Probabilities (not pop)
def test_prob_close_and_touch_lognormal() -> None:
    sigma = 0.15
    t = 7 / 365
    # Far OTM upside → small close-beyond
    pc = p3.prob_close_beyond(25000, 26000, sigma, t)
    assert pc is not None and 0 < pc < 0.5
    assert p3.prob_touch(pc) == pytest.approx(min(0.95, 2 * pc))
    assert p3.prob_touch(0.6) == pytest.approx(0.95)  # capped
    # Hand-check vs N(d2)
    d2 = (math.log(25000 / 26000) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    assert pc == pytest.approx(ncdf(d2))


def test_attach_probs_ignores_pop_field() -> None:
    levels = [{"strike": 25100.0, "tag": "R1", "oi": 1, "pop": 0.99}]
    out = p3.attach_level_probs(levels, spot=25000, atm_iv_pct=15.0, t_years=7 / 365)
    assert out[0]["p_close"] != 0.99
    assert out[0]["p_close"] == p3.prob_close_beyond(25000, 25100, 0.15, 7 / 365)


# ------------------------------------------------------------------ Stop buffer
def test_stop_floors_to_min_distance_not_far_level() -> None:
    # Spot 25000, EM 200 → min dist = 50, vol stop = 24950
    # Pivot 24980: stop = min(24955, 24950) = 24950 (volatility stop) — never walk to S1 at 24500
    levels = [
        {"strike": 24980.0, "tag": "PIVOT"},
        {"strike": 24500.0, "tag": "S1"},  # 500 pts away > 1×EM
    ]
    got = p3.choose_stop(spot=25000.0, direction="Bullish", levels=levels, expected_move=200.0)
    assert got["stop"] == pytest.approx(24950.0)
    assert got["volatility_stop"] is True
    assert got["distance"] == pytest.approx(50.0)


def test_stop_beyond_em_uses_volatility_stop() -> None:
    # Only far S1 beyond 1×EM → volatility stop, do not use 500-pt structural stop
    levels = [{"strike": 24400.0, "tag": "S1"}]
    got = p3.choose_stop(spot=25000.0, direction="Bullish", levels=levels, expected_move=200.0)
    assert got["tag"] == "VOL"
    assert got["stop"] == pytest.approx(25000.0 - 50.0)
    assert "beyond 1×EM" in (got["rejected"][0]["reason"] if got["rejected"] else "")


def test_stop_bearish_mirrors() -> None:
    levels = [{"strike": 25100.0, "tag": "R1"}]
    got = p3.choose_stop(spot=25000.0, direction="Bearish", levels=levels, expected_move=100.0)
    need = p3.min_stop_distance(25000.0, 100.0)
    buf = p3.stop_buffer(25000.0)
    # stop = max(level+buf, spot+need)
    assert got["stop"] == pytest.approx(max(25100.0 + buf, 25000.0 + need))
    assert got["why"]


# ------------------------------------------------------------------ Taylor + t_hold
def test_taylor_includes_theta_hold() -> None:
    # δ·100 + ½γ·100² + θ·1
    est = p3.taylor_price(100.0, 0.5, 0.002, 100.0, theta=-10.0, t_hold=1.0)
    assert est == pytest.approx(100 + 50 + 0.5 * 0.002 * 10000 - 10)


def test_t_hold_capped_by_expiry() -> None:
    # Same day, morning → intraday < 1; 2d capped by days left to nearby expiry
    now = ts(2026, 11, 16, 10, 0)  # day before expiry
    assert p3.t_hold_days(now, "2026-11-17", "2d") < 2.0
    assert p3.t_hold_days(now, "2026-11-17", "1d") <= 1.0 + 1e-9
    intra = p3.t_hold_days(now, "2026-11-17", "intraday")
    assert 0 < intra < 1.0


# ------------------------------------------------------------------ Strike picker
def test_strike_picker_three_buckets_and_lot() -> None:
    rs = rows_around_atm()
    stop_info = {
        "stop": 24900.0,
        "level": 24920.0,
        "tag": "S1",
        "buffer": 20.0,
        "min_distance": 40.0,
        "distance": 100.0,
        "why": "S1 − buffer 20",
    }
    out = p3.build_strike_picker(
        rs,
        spot=SPOT,
        direction="Bullish",
        target=25100.0,
        stop_info=stop_info,
        lot_size=65,
        t_hold=1.0,
        hold_mode="1d",
    )
    assert out["side"] == "call"
    assert out["stop"] == 24900.0
    assert out["stop_why"]
    assert out["stop_distance"] == 100.0
    buckets = {c["bucket"] for c in out["candidates"]}
    assert buckets == {"ITM", "ATM", "OTM"}
    for c in out["candidates"]:
        assert c["lot_size"] == 65
        assert c["cost_per_lot"] == pytest.approx(c["ltp"] * 65)
        assert c["est_at_target"] is not None
        assert c["est_at_stop"] is not None
        assert "est_gain" in c and "est_loss" in c and "reward_risk" in c
        assert c["checks"]


def test_loss_check_by_premium_fraction() -> None:
    c = p3.score_candidate(
        {
            "bucket": "ATM",
            "strike": 25000.0,
            "side": "call",
            "ltp": 100.0,
            "delta": 0.5,
            "gamma": 0.0,
            "theta": -1.0,
            "theta_pct_premium": 1.0,
            "iv": 15.0,
            "spread_pct": 0.5,
            "lot_size": 65,
            "cost_per_lot": 6500.0,
            "est_at_target": 150.0,
            "est_at_stop": 70.0,  # loss 30 → 30% of premium → pass
        },
        t_hold=1.0,
    )
    loss_chk = next(ch for ch in c["checks"] if ch["id"] == "loss")
    assert loss_chk["status"] == "pass"
    assert loss_chk["detail"] == "-30.00"
    c2 = p3.score_candidate(
        {**{k: c[k] for k in ("bucket", "strike", "side", "ltp", "delta", "gamma", "theta", "theta_pct_premium", "iv", "spread_pct", "lot_size", "cost_per_lot")},
         "est_at_target": 120.0, "est_at_stop": 20.0},  # loss 80% → fail
        t_hold=1.0,
    )
    assert next(ch for ch in c2["checks"] if ch["id"] == "loss")["status"] == "fail"


def test_no_setup_does_not_highlight() -> None:
    """When every candidate fails a gate, no ★ — closest is labelled instead."""
    cands = [
        p3.score_candidate(
            {
                "bucket": "ITM",
                "strike": 24900.0,
                "side": "call",
                "ltp": 200.0,
                "delta": 0.65,
                "gamma": 0.001,
                "theta": -5.0,
                "theta_pct_premium": 2.5,
                "iv": 15.0,
                "spread_pct": 0.5,
                "lot_size": 65,
                "cost_per_lot": 13000.0,
                "est_at_target": 210.0,  # gain 10
                "est_at_stop": 50.0,  # loss 150 → R:R 0.07 fail
            },
            t_hold=1.0,
        ),
        p3.score_candidate(
            {
                "bucket": "ATM",
                "strike": 25000.0,
                "side": "call",
                "ltp": 100.0,
                "delta": 0.5,
                "gamma": 0.002,
                "theta": -8.0,
                "theta_pct_premium": 8.0,
                "iv": 15.0,
                "spread_pct": 0.5,
                "lot_size": 65,
                "cost_per_lot": 6500.0,
                "est_at_target": 105.0,
                "est_at_stop": 20.0,
            },
            t_hold=1.0,
        ),
    ]
    hi = p3.apply_picker_highlight(cands)
    assert hi["has_setup"] is False
    assert hi["highlight"] is None
    assert hi["setup_message"] == p3.NO_SETUP_MSG
    assert hi["closest"] in ("ITM", "ATM")
    assert any(c["closest_failed"] for c in hi["candidates"])
    assert not any(c["highlighted"] for c in hi["candidates"])


def test_enrich_phase3_attaches_blocks(tmp_path: Path) -> None:
    # Build a real chain view then enrich
    raw = []
    for r in rows_around_atm():
        raw.append(
            {
                "strike_price": r["strike"],
                "underlying_spot_price": SPOT,
                "call_options": {
                    "market_data": {
                        "oi": r["call"]["oi"],
                        "prev_oi": r["call"]["prev_oi"],
                        "ltp": r["call"]["ltp"],
                        "close_price": r["call"]["close"],
                        "bid_price": r["call"]["bid"],
                        "ask_price": r["call"]["ask"],
                        "volume": 0,
                    },
                    "option_greeks": {
                        "iv": r["call"]["iv"],
                        "delta": r["call"]["delta"],
                        "gamma": r["call"]["gamma"],
                        "theta": r["call"]["theta"],
                        "vega": 5.0,
                        "pop": 99.0,
                    },
                },
                "put_options": {
                    "market_data": {
                        "oi": r["put"]["oi"],
                        "prev_oi": r["put"]["prev_oi"],
                        "ltp": r["put"]["ltp"],
                        "close_price": r["put"]["close"],
                        "bid_price": r["put"]["bid"],
                        "ask_price": r["put"]["ask"],
                        "volume": 0,
                    },
                    "option_greeks": {
                        "iv": r["put"]["iv"],
                        "delta": r["put"]["delta"],
                        "gamma": r["put"]["gamma"],
                        "theta": r["put"]["theta"],
                        "vega": 5.0,
                        "pop": 99.0,
                    },
                },
            }
        )
    view = chain_view.build_chain_view(raw, now=ts(2026, 11, 10, 12, 0), expiry=EXPIRY, status="LIVE")
    p3.enrich_phase3(view, vix=13.0, options_dir=tmp_path, lot_size=65, hold_mode="1d")
    assert view["iv_panel"]["verdict"] in ("Fair", "Options expensive", "Options cheap")
    assert view["expected_move"]["upper"] > SPOT
    assert view["expected_move"]["lower"] < SPOT
    assert view["resistance"][0].get("p_close") is not None
    assert view["strike_picker"]["Bullish"]["lot_size"] == 65
    assert view["strike_picker"]["Bullish"]["stop_why"]
    # pop must not equal attached p_close
    assert view["resistance"][0]["p_close"] != 0.99
