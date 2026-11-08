"""Phase 2 Options: build-up, PCR set, max pain, near-ATM edge cases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.options import chain_analytics as ca
from app.options import chain_view

IST = timezone(timedelta(hours=5, minutes=30))
EXPIRY = "2026-11-10"


def ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp())


def leg(*, oi=0.0, prev_oi=0.0, ltp=0.0, close=0.0):
    oi_chg = oi - prev_oi
    oi_chg_pct = (oi_chg / prev_oi * 100.0) if prev_oi else None
    return {
        "oi": oi,
        "prev_oi": prev_oi,
        "oi_chg": oi_chg,
        "oi_chg_pct": oi_chg_pct,
        "volume": 0.0,
        "iv": None,
        "ltp": ltp,
        "close": close,
        "price_chg": ltp - close,
        "bid": 0.0,
        "ask": 0.0,
        "spread_pct": None,
    }


def row(strike: float, call: dict, put: dict, spot: float = 25000.0) -> dict:
    return {"strike": strike, "call": call, "put": put, "underlying_spot_price": spot}


# ------------------------------------------------------------------ build-up
@pytest.mark.parametrize(
    "price_chg,oi_chg,oi_chg_pct,label",
    [
        (5.0, 1000.0, 10.0, "Long Build-up"),
        (-5.0, 1000.0, 10.0, "Short Build-up"),
        (5.0, -1000.0, -10.0, "Short Covering"),
        (-5.0, -1000.0, -10.0, "Long Unwinding"),
        (5.0, 100.0, 1.5, "Neutral"),
        (-5.0, -100.0, -1.0, "Neutral"),
    ],
)
def test_buildup_matrix(price_chg, oi_chg, oi_chg_pct, label) -> None:
    assert ca.classify_buildup(price_chg, oi_chg, oi_chg_pct)["label"] == label


@pytest.mark.parametrize(
    "side,label,bias",
    [
        ("call", "Short Build-up", "Bearish"),
        ("call", "Long Unwinding", "Bearish"),
        ("call", "Long Build-up", "Bullish"),
        ("call", "Short Covering", "Bullish"),
        ("put", "Short Build-up", "Bullish"),
        ("put", "Long Unwinding", "Bullish"),
        ("put", "Long Build-up", "Bearish"),
        ("put", "Short Covering", "Bearish"),
        ("call", "Neutral", None),
    ],
)
def test_nifty_bias_both_legs(side, label, bias) -> None:
    assert ca.nifty_bias(side, label) == bias


def test_annotate_leg_attaches_fields() -> None:
    lg = leg(oi=1100, prev_oi=1000, ltp=105, close=100)  # +price +OI → Long Build-up
    out = ca.annotate_leg(lg, "call")
    assert out["buildup"] == "Long Build-up"
    assert out["buildup_nifty"] == "Bullish"


# ------------------------------------------------------------------ max pain
def test_max_pain_known_fixture() -> None:
    # Puts pile below, calls pile above → writers suffer least at the centre (100).
    rows = [
        {"strike": 90.0, "call": {"oi": 10.0, "oi_chg": 0}, "put": {"oi": 100.0, "oi_chg": 0}},
        {"strike": 100.0, "call": {"oi": 50.0, "oi_chg": 0}, "put": {"oi": 50.0, "oi_chg": 0}},
        {"strike": 110.0, "call": {"oi": 100.0, "oi_chg": 0}, "put": {"oi": 10.0, "oi_chg": 0}},
    ]

    def pain(k: float) -> float:
        return sum(
            r["call"]["oi"] * max(0.0, k - r["strike"]) + r["put"]["oi"] * max(0.0, r["strike"] - k)
            for r in rows
        )

    mp = ca.max_pain(rows)
    assert mp is not None
    assert mp["strike"] == 100.0
    assert pain(100.0) < pain(90.0) and pain(100.0) < pain(110.0)


# ------------------------------------------------------------------ PCR
def test_change_oi_pcr_zero_and_negative_denominator() -> None:
    zone_zero = [
        {"strike": 25000, "call": {"oi": 1, "oi_chg": 0}, "put": {"oi": 1, "oi_chg": 500}},
    ]
    z = ca.change_oi_pcr(zone_zero)
    assert z["value"] is None and z["display"] == "n/a"
    assert "no call OI change" in (z["reason"] or "")

    zone_neg = [
        {"strike": 25000, "call": {"oi": 1, "oi_chg": -100}, "put": {"oi": 1, "oi_chg": 50}},
    ]
    n = ca.change_oi_pcr(zone_neg)
    assert n["value"] is None and "negative" in (n["reason"] or "").lower()


def test_change_oi_pcr_happy_path() -> None:
    zone = [
        {"strike": 25000, "call": {"oi": 1, "oi_chg": 100}, "put": {"oi": 1, "oi_chg": 200}},
        {"strike": 25050, "call": {"oi": 1, "oi_chg": 100}, "put": {"oi": 1, "oi_chg": 100}},
    ]
    out = ca.change_oi_pcr(zone)
    assert out["value"] == pytest.approx(300 / 200)
    assert out["read"] == "Bullish"  # 1.5 > 1.2


def test_pcr_read_bands() -> None:
    assert ca.pcr_read(1.3) == "Bullish"
    assert ca.pcr_read(1.0) == "Neutral"
    assert ca.pcr_read(0.7) == "Bearish"
    assert ca.pcr_read(None) is None


# ------------------------------------------------------------------ near-ATM edge
def test_near_atm_clamped_at_chain_edge() -> None:
    rows = [{"strike": float(s), "call": {"oi": 1, "oi_chg": 0}, "put": {"oi": 1, "oi_chg": 0}} for s in range(25000, 25100, 50)]
    # ATM = first strike → zone starts at index 0, not negative
    zone = ca.near_atm_slice(rows, atm=25000.0, band=5)
    assert zone[0]["strike"] == 25000.0
    assert len(zone) == len(rows)  # short chain, whole thing


def test_enrich_attaches_phase2_to_view() -> None:
    raw = []
    for s in range(24800, 25300, 50):
        raw.append(
            {
                "strike_price": float(s),
                "underlying_spot_price": 25030.0,
                "call_options": {
                    "market_data": {
                        "oi": 1_000_000 if s != 25000 else 10_000_000,
                        "prev_oi": 900_000,
                        "ltp": 100,
                        "close_price": 90,
                        "volume": 1,
                        "bid_price": 99,
                        "ask_price": 101,
                    },
                    "option_greeks": {},
                },
                "put_options": {
                    "market_data": {
                        "oi": 800_000,
                        "prev_oi": 700_000,
                        "ltp": 80,
                        "close_price": 90,
                        "volume": 1,
                        "bid_price": 79,
                        "ask_price": 81,
                    },
                    "option_greeks": {},
                },
            }
        )
    out = chain_view.build_chain_view(raw, now=ts(2026, 11, 7, 16, 0), expiry=EXPIRY)
    assert out["pcrs"]["full"]["value"] is not None
    assert out["pcrs"]["near_atm"]["value"] is not None
    assert "change_oi" in out["pcrs"]
    assert out["max_pain"] is not None and "strike" in out["max_pain"]
    assert out["buildup_analysis"]["summary"]
    assert out["rows"][0]["call"]["buildup"] in {
        "Long Build-up",
        "Short Build-up",
        "Short Covering",
        "Long Unwinding",
        "Neutral",
    }
