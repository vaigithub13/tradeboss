"""The Quantman variant of the DD strategy: trailing premium stop, strike by premium, paper slot settings."""

from __future__ import annotations

from datetime import date

import pytest

from app.exits.rules import NAMED, ExitRule, parse_exit_rule, trailed_stop
from app.options.contract import OptionContract, strike_by_premium
from app.options.model import OptionModelConfig, _premium_walk
from app.paper.live import PaperError, slot_settings


def test_named_trailing_rule_round_trips() -> None:
    r = NAMED["premium_trail_10"]
    assert (r.stop, r.trail_every, r.trail_by) == (0.10, 0.15, 0.10)
    assert parse_exit_rule(r.to_dict()) == r
    assert "trail" in r.label and "no target" in r.label


def test_trailing_rule_needs_both_fields_and_premium_kind() -> None:
    with pytest.raises(ValueError):
        ExitRule("premium", 0.1, 1.0, trail_every=0.15)
    with pytest.raises(ValueError):
        ExitRule("atr", 1.0, 2.0, trail_every=0.15, trail_by=0.1)


def test_trailed_stop_steps() -> None:
    r = NAMED["premium_trail_10"]
    assert trailed_stop(200.0, r, None) == 180.0
    assert trailed_stop(200.0, r, 229.0) == 180.0  # +14.5%: not a full step yet
    assert trailed_stop(200.0, r, 230.0) == 200.0  # +15%: stop to cost
    assert trailed_stop(200.0, r, 262.0) == 220.0  # +31%: two steps
    assert trailed_stop(200.0, NAMED["premium_1to2"], 400.0) == 160.0  # no trailing on the plain rule


def contract(strike: float = 25000.0) -> OptionContract:
    return OptionContract(kind="CE", strike=strike, expiry=date(2026, 11, 17), cycle="weekly", lot_size=65,
                          symbol=f"NIFTY {strike:g} CE 17 NOV 26", step=50, step_verification="verified")


def test_strike_by_premium_picks_nearest() -> None:
    # premium falls by 30 per 50-point strike step from 200 at the ATM
    pick = strike_by_premium(contract(), 250.0, lambda k: 200.0 - (k - 25000.0) * 0.6)
    assert pick.strike in (24900.0, 24950.0) and pick.symbol.startswith(f"NIFTY {pick.strike:g} CE")
    assert pick.kind == "CE" and pick.expiry == date(2026, 11, 17)
    same = strike_by_premium(contract(), 250.0, lambda k: None)
    assert same == contract()


def test_walk_trails_the_stop_on_modelled_minutes() -> None:
    bars = {0: (100, 101, 99, 100), 60: (100, 116, 100, 115), 120: (115, 116, 110, 111), 180: (111, 112, 98, 99)}
    walk = _premium_walk(None, date(2026, 11, 17), 25000.0, "CE", start=0, end=240, entry_fill=100.0,
                         target_pct=50.0, stop_pct=0.10, trail=(0.15, 0.10), model_bar=bars.get)
    assert walk.exit is not None
    t, price, reason = walk.exit
    # the 116 high (+16%) raised the stop to 100; the 120 bar's low 110 holds; the 180 bar trades through 100
    assert t == 180 and price == 100.0 and reason == "premium_trail"


def test_walk_without_trail_and_without_tape_is_unchanged() -> None:
    walk = _premium_walk(None, date(2026, 11, 17), 25000.0, "CE", start=0, end=240, entry_fill=100.0,
                         target_pct=0.4, stop_pct=0.2)
    assert walk.exit is None and walk.checked is False


def test_model_config_trailing_validation() -> None:
    OptionModelConfig(premium_stop_pct=0.1, premium_trail_every=0.15, premium_trail_by=0.1, strike_premium=250.0)
    with pytest.raises(ValueError):
        OptionModelConfig(premium_trail_every=0.15, premium_trail_by=0.1)  # no stop
    with pytest.raises(ValueError):
        OptionModelConfig(strike_premium=0.0)
    assert "strike_premium" not in OptionModelConfig().to_dict()  # off: earlier run ids unchanged


def test_slot_settings() -> None:
    from app.paper.live import MODEL_SLIPPAGE_POINTS, SENSEX_SLIPPAGE_POINTS

    rest, minutes, prem, symbol, slip = slot_settings(
        {"timeframe": "3m", "strike_premium": 250, "mode": "long_only"}
    )
    assert rest == {"mode": "long_only"} and minutes == 3 and prem == 250.0
    assert symbol == "NIFTY50" and slip == MODEL_SLIPPAGE_POINTS
    assert slot_settings({}) == ({}, 5, None, "NIFTY50", MODEL_SLIPPAGE_POINTS)
    _, _, _, sx, sx_slip = slot_settings({"symbol": "SENSEX", "strike_premium": 800})
    assert sx == "SENSEX" and sx_slip == SENSEX_SLIPPAGE_POINTS
    with pytest.raises(PaperError):
        slot_settings({"timeframe": "2m"})
    with pytest.raises(PaperError):
        slot_settings({"strike_premium": -5})
    with pytest.raises(PaperError):
        slot_settings({"symbol": "BANKNIFTY"})
