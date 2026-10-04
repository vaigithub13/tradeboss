"""Estimated option premium for a long or short position drawing.

See PROJECT_PLAN.md, "Long position and short position".
A long uses the ATM call of the nearest weekly expiry, a short the ATM put. Premiums are
option model v1 repriced at each index level and time. One lot is marked after the options
cost table. The label says the result is estimated. No network and no stored candles.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.backtest.costs import get_cost_model
from app.backtest.expiry import load_default_calendar
from app.backtest.lots import LotSizeAmbiguous, load_default_lot_table
from app.backtest.sources import ist_date
from app.options.bs import snap_premium
from app.options.contract import choose_contract
from app.options.model import _model_delta, _model_premium, load_option_model
from app.options.position import estimate_position_option, lot_for_position, position_option_applies
from app.options.strikes import load_default_step_table

ENTRY = 1_790_826_300  # 1 Oct 2026 09:15 IST
LATER = ENTRY + 6 * 3600
VIX = 15.0


def _contract(direction: str, entry: float):
    return choose_contract(
        direction,
        entry,
        ist_date(ENTRY),
        calendar=load_default_calendar(),
        lots=load_default_lot_table(),
        steps=load_default_step_table(),
    )


def _priced(kind: str, spot: float, strike: float, when: int, expiry: date) -> float:
    cfg = load_option_model()
    premium, *_rest = _model_premium(kind, spot, strike, when, expiry, VIX, cfg, load_default_calendar())
    return float(premium)


def _expected(direction: str, entry: float, target: float, stop: float, exit_index: float, exit_time: int, target_time: int, stop_time: int) -> dict:
    contract = _contract(direction, entry)
    entry_premium = _priced(contract.kind, entry, contract.strike, ENTRY, contract.expiry)
    exit_premium = _priced(contract.kind, exit_index, contract.strike, exit_time, contract.expiry)
    costs = get_cost_model("options")
    buy = costs.leg_cost("BUY", entry_premium, contract.lot_size, ist_date(ENTRY))
    sell = costs.leg_cost("SELL", exit_premium, contract.lot_size, ist_date(exit_time))
    gross = (exit_premium - entry_premium) * contract.lot_size
    charges = float(buy.total + sell.total)
    return {
        "kind": contract.kind,
        "strike": contract.strike,
        "expiry": contract.expiry,
        "lot_size": contract.lot_size,
        "entry_premium": entry_premium,
        "target_premium": _priced(contract.kind, target, contract.strike, target_time, contract.expiry),
        "stop_premium": _priced(contract.kind, stop, contract.strike, stop_time, contract.expiry),
        "exit_premium": exit_premium,
        "gross": gross,
        "charges": charges,
        "net": gross - charges,
    }


def test_the_lot_comes_from_the_table_and_a_transition_is_not_guessed() -> None:
    assert lot_for_position("NIFTY50", date(2026, 10, 1)) == 65
    with pytest.raises(LotSizeAmbiguous):
        lot_for_position("NIFTY50", date(2024, 12, 26))
    assert position_option_applies("NIFTY50") is True
    assert position_option_applies("NIFTY") is True
    assert position_option_applies("RELIANCE") is False


def test_a_one_percent_move_is_repriced_instead_of_shifted_by_delta() -> None:
    entry, target = 24_000.0, 24_240.0
    contract = _contract("LONG", entry)
    cfg = load_option_model()
    entry_premium = _priced("CE", entry, contract.strike, ENTRY, contract.expiry)
    delta = _model_delta("CE", entry, contract.strike, ENTRY, contract.expiry, VIX, cfg, load_default_calendar())
    linear = snap_premium(entry_premium + delta * (target - entry), cfg.tick, cfg.min_premium)
    est = estimate_position_option(
        side="long", entry=entry, target=target, stop=23_760, entry_time=ENTRY,
        target_time=LATER, stop_time=LATER, status="target", vix=VIX,
    )
    assert est is not None
    assert est.target_premium == pytest.approx(_priced("CE", target, contract.strike, LATER, contract.expiry))
    assert abs(est.target_premium - linear) > 1
    assert "estimated" in est.label and "option-based quantity" in est.label
    assert est.option_lots == math.floor(est.risk_budget / est.option_risk_per_lot)


def test_a_long_estimates_the_call_and_a_short_estimates_the_put() -> None:
    long = estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        target_time=LATER, stop_time=ENTRY, status="target", vix=VIX,
    )
    assert long is not None
    want = _expected("LONG", 24_000, 24_200, 23_900, 24_200, LATER, LATER, ENTRY)
    assert long.kind == "CE"
    assert (long.strike, long.expiry, long.lot_size) == (want["strike"], want["expiry"], want["lot_size"])
    assert long.entry_premium == pytest.approx(want["entry_premium"])
    assert long.target_premium == pytest.approx(want["target_premium"])
    assert long.stop_premium == pytest.approx(want["stop_premium"])
    assert long.target_premium > long.entry_premium
    assert long.exit_premium == pytest.approx(want["exit_premium"])
    assert long.gross_per_lot == pytest.approx(want["gross"])
    assert long.charges_per_lot == pytest.approx(want["charges"])
    assert long.net_per_lot == pytest.approx(want["net"])
    assert long.net_per_lot < long.gross_per_lot
    assert "estimated" in long.label and "option-based quantity" in long.label

    short = estimate_position_option(
        side="short", entry=24_000, target=23_800, stop=24_100, entry_time=ENTRY,
        target_time=LATER, stop_time=ENTRY, status="target", vix=VIX,
    )
    assert short is not None and short.kind == "PE"
    assert short.target_premium > short.entry_premium
    put = _expected("SHORT", 24_000, 23_800, 24_100, 23_800, LATER, LATER, ENTRY)
    assert short.entry_premium == pytest.approx(put["entry_premium"])
    assert short.net_per_lot == pytest.approx(put["net"])


def test_option_quantity_says_when_one_lot_costs_more_than_the_budget() -> None:
    est = estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        target_time=ENTRY, stop_time=ENTRY, status="target", vix=VIX,
        risk_mode="rupees", risk_rupees=1,
    )
    assert est is not None and est.option_lots == 0
    assert f"0 lots: risk per lot Rs {est.option_risk_per_lot:.2f} exceeds budget Rs {est.risk_budget:.2f}" in est.label
    assert "option-based quantity" in est.label


def test_an_ambiguous_stop_and_a_cursor_before_entry_stay_honest() -> None:
    ambiguous = estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        target_time=ENTRY, stop_time=LATER, status="ambiguous", vix=VIX,
    )
    assert ambiguous is not None
    want = _expected("LONG", 24_000, 24_200, 23_900, 23_900, LATER, ENTRY, LATER)
    assert ambiguous.exit_premium == pytest.approx(want["stop_premium"])
    assert ambiguous.net_per_lot == pytest.approx(want["net"])
    assert "estimated" in ambiguous.label and "stop assumed" in ambiguous.label

    assert estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        target_time=ENTRY, stop_time=ENTRY, status="target", vix=VIX, as_of=ENTRY - 60,
    ) is None
    assert estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        target_time=ENTRY, stop_time=ENTRY, status="pending", vix=VIX,
    ) is None
    assert estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        target_time=ENTRY, stop_time=ENTRY, status="not_entered", vix=VIX,
    ) is None
    assert estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        target_time=ENTRY, stop_time=ENTRY, status="open", exit_index=24_080, exit_time=ENTRY, vix=0,
    ) is None
