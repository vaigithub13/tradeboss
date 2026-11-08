"""Phase 4 Options: bias score, weights, spot guard, Auto 1σ cap, win bump."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.options import bias as bz
from app.options import chain_phase3 as p3
from app.options.chain_view import assert_spot_consistent, chain_spot_from_rows

IST = timezone(timedelta(hours=5, minutes=30))


def test_spot_consistency_rejects_pinned_older_spot() -> None:
    rows = [
        {"underlying_spot_price": 25100.0, "strike_price": 25100},
        {"underlying_spot_price": 25100.0, "strike_price": 25150},
        {"underlying_spot_price": 25100.5, "strike_price": 25050},
    ]
    assert chain_spot_from_rows(rows) == pytest.approx(25100.0)
    # Pinned Friday spot must not stick when chain says otherwise
    assert assert_spot_consistent(rows, 25030.35) == pytest.approx(25100.0)
    assert assert_spot_consistent(rows, 25100.0) == pytest.approx(25100.0)


def test_weights_sum_and_expiry_reweight() -> None:
    w = bz.weights_for(expiry_day=False)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["max_pain"] == pytest.approx(0.05)
    we = bz.weights_for(expiry_day=True)
    assert sum(we.values()) == pytest.approx(1.0)
    assert we["max_pain"] == pytest.approx(0.15)
    assert we["buildup"] == pytest.approx(0.25 * 0.85 / 0.95)


def test_bias_labels() -> None:
    assert bz.bias_label(-7) == "Strong Bearish"
    assert bz.bias_label(-3) == "Mildly Bearish"
    assert bz.bias_label(0) == "Neutral"
    assert bz.bias_label(4) == "Mildly Bullish"
    assert bz.bias_label(8) == "Strong Bullish"


def test_bias_win_bump() -> None:
    assert bz.bias_win_bump(0) == 0.0
    assert bz.bias_win_bump(10) == pytest.approx(0.12)
    assert bz.bias_win_bump(-10) == pytest.approx(0.12)
    assert bz.bias_win_bump(5) == pytest.approx(0.06)


def test_component_scoring_edges() -> None:
    assert bz.score_buildup({"call_doi": 0, "put_doi": 100}) == pytest.approx(1.0)
    assert bz.score_buildup({"call_doi": 100, "put_doi": 0}) == pytest.approx(-1.0)
    assert bz.score_buildup({"call_doi": 50, "put_doi": 50}) == pytest.approx(0.0)

    from app.options.chain_view import PIVOT_READ_ABOVE_CALL, PIVOT_READ_BELOW_CALL, PIVOT_READ_BATTLE

    assert bz.score_pivot(
        25100, {"strike": 25000, "read": PIVOT_READ_ABOVE_CALL, "strength": "Strong"}, one_sigma=200
    ) == pytest.approx(1.0)
    assert bz.score_pivot(
        24900, {"strike": 25000, "read": PIVOT_READ_BELOW_CALL, "strength": "Strong"}, one_sigma=200
    ) == pytest.approx(-1.0)
    assert bz.score_pivot(
        25100,
        {"strike": 25000, "read": PIVOT_READ_ABOVE_CALL, "strength": "Medium", "strength_scale": 0.6},
        one_sigma=200,
    ) == pytest.approx(0.6)
    assert bz.score_pivot(
        25100, {"strike": 25000, "read": PIVOT_READ_BATTLE, "strength": "Strong"}, one_sigma=200
    ) == pytest.approx(0.0)

    assert bz.score_pcr({"read": "Strong Bullish"}) == 1.0
    assert bz.score_pcr({"read": "Bearish"}) == -0.5

    iv = bz.score_iv_trend(atm_iv=14.0, prev_atm_iv=15.0, skew=0.0)
    assert iv > 0  # IV down → bullish
    assert bz.score_iv_trend(atm_iv=15.0, prev_atm_iv=15.0, skew=3.0) < 0

    assert bz.score_max_pain(24900, {"strike": 25000}) > 0  # upward pull
    assert bz.score_max_pain(25100, {"strike": 25000}) < 0

    assert bz.score_premarket([{"score": 1}, {"score": -1}, {"score": 1}]) == pytest.approx(1 / 3)
    assert bz.score_premarket([]) is None


def test_build_bias_aggregate() -> None:
    view = {
        "spot": 25000.0,
        "as_of": 1,
        "as_of_ist": "x",
        "buildup_analysis": {"call_doi": 0, "put_doi": 100, "bias": "Bullish"},
        "pivot": {"strike": 24950.0},
        "pcrs": {
            "change_oi": {"read": "Bullish"},
            "near_atm": {"read": "Bullish"},
            "full": {"value": 1.2},
        },
        "iv_panel": {"atm_iv": 14.0, "skew": -1.0},
        "max_pain": {"strike": 25100.0},
        "expected_move": {"range_move": 250.0, "move": 200.0},
        "resistance": [{"tag": "R1", "strike": 25200.0}],
        "support": [{"tag": "S1", "strike": 24800.0}],
    }
    mv = {
        "factors": [
            {"key": "global", "score": 1},
            {"key": "fii", "score": 1},
            {"key": "crude", "score": 0},
            {"key": "vix", "score": 1},
            {"key": "structure", "score": 1},
        ]
    }
    out = bz.build_bias(view, market_view=mv, previous_eod=None, expiry_day=False)
    assert -10 <= out["score"] <= 10
    assert out["label"] in (
        "Strong Bearish",
        "Mildly Bearish",
        "Neutral",
        "Mildly Bullish",
        "Strong Bullish",
    )
    assert out["gift_nifty"] == "not connected"
    assert len(out["components"]) == 7
    assert sum(c["weight"] for c in out["components"]) == pytest.approx(1.0)


def test_auto_target_capped_to_one_sigma() -> None:
    """Far R3 beyond 1σ must not be chosen; message when nothing in range works."""
    from tests.test_options_phase3 import rows_around_atm

    rows = rows_around_atm(25000.0)
    resistance = [
        {"strike": 25050.0, "tag": "R1"},
        {"strike": 25100.0, "tag": "R2"},
        {"strike": 25500.0, "tag": "R3"},  # 500 pts > 1σ=150
    ]
    support = [{"strike": 24900.0, "tag": "S1"}]
    stop_info = {
        "stop": 24950.0,
        "distance": 50.0,
        "level": None,
        "tag": "VOL",
        "buffer": 25.0,
        "min_distance": 50.0,
        "why": "vol",
        "volatility_stop": True,
    }
    # With tiny 1σ, only R1 (50 pts) is in range — may or may not hit R:R≥2
    out = p3.select_auto_target(
        rows,
        spot=25000.0,
        direction="Bullish",
        resistance=resistance,
        support=support,
        stop_info=stop_info,
        lot_size=65,
        t_hold=1.0,
        hold_mode="1d",
        one_sigma_move=80.0,  # only R1 at 50 pts in range
    )
    assert out["target_mode"] == "Auto"
    if out.get("auto_resolved_tag"):
        assert out["auto_resolved_tag"] == "R1"
        assert abs(out["target"] - 25050.0) < 1
    else:
        assert out["auto_message"] == p3.AUTO_NO_RANGE_MSG
        assert out["has_setup"] is False

    # Cap that excludes every level → always the range message
    out2 = p3.select_auto_target(
        rows,
        spot=25000.0,
        direction="Bullish",
        resistance=resistance,
        support=support,
        stop_info=stop_info,
        lot_size=65,
        t_hold=1.0,
        hold_mode="1d",
        one_sigma_move=20.0,  # nothing within 20 pts
    )
    assert out2["auto_message"] == p3.AUTO_NO_RANGE_MSG
    assert out2["has_setup"] is False


def test_bias_history_slot_windows() -> None:
    from app.options.bias_history import slot_for

    morning = datetime(2026, 11, 10, 9, 12, tzinfo=IST)
    eod = datetime(2026, 11, 10, 15, 28, tzinfo=IST)
    noon = datetime(2026, 11, 10, 12, 0, tzinfo=IST)
    assert slot_for(morning) == "morning"
    assert slot_for(eod) == "eod"
    assert slot_for(noon) is None
