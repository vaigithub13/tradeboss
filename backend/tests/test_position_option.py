"""Estimated option premium for a long or short position drawing.

See PROJECT_PLAN.md, "Long position and short position". The function is not written yet.
A long uses the ATM call of the nearest weekly expiry, a short the ATM put. Premiums are
option model v1 at the entry, then moved by that delta. One lot is marked after the options
cost table. The label says the result is estimated. No network and no stored candles.
"""

from __future__ import annotations

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
VIX = 15.0


def _expected(direction: str, entry: float, target: float, stop: float, exit_index: float) -> dict:
    day = ist_date(ENTRY)
    cfg = load_option_model()
    contract = choose_contract(
        direction,
        entry,
        day,
        calendar=load_default_calendar(),
        lots=load_default_lot_table(),
        steps=load_default_step_table(),
    )
    prem, *_rest = _model_premium(contract.kind, entry, contract.strike, ENTRY, contract.expiry, VIX, cfg, load_default_calendar())
    delta = _model_delta(contract.kind, entry, contract.strike, ENTRY, contract.expiry, VIX, cfg, load_default_calendar())

    def adjusted(index: float) -> float:
        return snap_premium(prem + delta * (index - entry), cfg.tick, cfg.min_premium)

    entry_premium = adjusted(entry)
    exit_premium = adjusted(exit_index)
    costs = get_cost_model("options")
    buy = costs.leg_cost("BUY", entry_premium, contract.lot_size, day)
    sell = costs.leg_cost("SELL", exit_premium, contract.lot_size, day)
    gross = (exit_premium - entry_premium) * contract.lot_size
    charges = float(buy.total + sell.total)
    return {
        "kind": contract.kind,
        "strike": contract.strike,
        "expiry": contract.expiry,
        "lot_size": contract.lot_size,
        "delta": delta,
        "entry_premium": entry_premium,
        "target_premium": adjusted(target),
        "stop_premium": adjusted(stop),
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


def test_a_long_estimates_the_call_and_a_short_estimates_the_put() -> None:
    long = estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        status="target", exit_index=None, vix=VIX,
    )
    assert long is not None
    want = _expected("LONG", 24_000, 24_200, 23_900, 24_200)
    assert long.kind == "CE"
    assert (long.strike, long.expiry, long.lot_size) == (want["strike"], want["expiry"], want["lot_size"])
    assert long.delta == pytest.approx(want["delta"])
    assert long.delta > 0
    assert long.entry_premium == pytest.approx(want["entry_premium"])
    assert long.target_premium == pytest.approx(want["target_premium"])
    assert long.stop_premium == pytest.approx(want["stop_premium"])
    assert long.target_premium > long.entry_premium
    assert long.exit_premium == pytest.approx(want["exit_premium"])
    assert long.gross_per_lot == pytest.approx(want["gross"])
    assert long.charges_per_lot == pytest.approx(want["charges"])
    assert long.net_per_lot == pytest.approx(want["net"])
    assert long.net_per_lot < long.gross_per_lot
    assert "estimated" in long.label

    short = estimate_position_option(
        side="short", entry=24_000, target=23_800, stop=24_100, entry_time=ENTRY,
        status="target", exit_index=None, vix=VIX,
    )
    assert short is not None and short.kind == "PE" and short.delta < 0
    assert short.target_premium > short.entry_premium
    put = _expected("SHORT", 24_000, 23_800, 24_100, 23_800)
    assert short.entry_premium == pytest.approx(put["entry_premium"])
    assert short.net_per_lot == pytest.approx(put["net"])


def test_an_ambiguous_stop_and_a_cursor_before_entry_stay_honest() -> None:
    ambiguous = estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        status="ambiguous", exit_index=None, vix=VIX,
    )
    assert ambiguous is not None
    want = _expected("LONG", 24_000, 24_200, 23_900, 23_900)
    assert ambiguous.exit_premium == pytest.approx(want["stop_premium"])
    assert ambiguous.net_per_lot == pytest.approx(want["net"])
    assert "estimated" in ambiguous.label and "stop assumed" in ambiguous.label

    assert estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        status="target", exit_index=None, vix=VIX, as_of=ENTRY - 60,
    ) is None
    assert estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        status="pending", exit_index=None, vix=VIX,
    ) is None
    assert estimate_position_option(
        side="long", entry=24_000, target=24_200, stop=23_900, entry_time=ENTRY,
        status="open", exit_index=24_080, vix=0,
    ) is None
