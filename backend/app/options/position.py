"""Estimated option premium for a long or short position drawing.

A long is the ATM call of the nearest weekly expiry, a short the ATM put. Each premium is
option model v1 repriced at that index level and at the time that level is reached, so the
move includes gamma and time decay. One lot is marked after the options cost table.
No network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from app.backtest.costs import get_cost_model
from app.backtest.expiry import load_default_calendar
from app.backtest.lots import LotSizeTable, load_default_lot_table
from app.backtest.sources import ist_date
from app.options.contract import choose_contract
from app.options.model import OptionModelConfig, _model_premium, load_option_model
from app.options.strikes import load_default_step_table

_UNDERLYING = {"NIFTY50": "NIFTY", "NIFTY": "NIFTY"}


def position_option_applies(symbol: str) -> bool:
    return symbol in _UNDERLYING


def lot_for_position(symbol: str, on: date, table: LotSizeTable | None = None) -> int:
    """Dated lot for the symbol. A transition raises LotSizeAmbiguous instead of guessing."""
    underlying = _UNDERLYING.get(symbol, symbol)
    return (table or load_default_lot_table()).lot_size(underlying, on)


@dataclass(frozen=True)
class PositionOptionEstimate:
    kind: str
    strike: float
    expiry: date
    lot_size: int
    entry_premium: float
    target_premium: float
    stop_premium: float
    exit_premium: float
    gross_per_lot: float
    charges_per_lot: float
    net_per_lot: float
    option_lots: int
    option_risk_per_lot: float
    risk_budget: float
    label: str


def _priced(kind: str, spot: float, strike: float, when: int, expiry: date, vix: float, cfg: OptionModelConfig) -> float:
    premium, *_rest = _model_premium(kind, spot, strike, when, expiry, vix, cfg, load_default_calendar())
    return float(premium)


def estimate_position_option(
    *,
    side: str,
    entry: float,
    target: float,
    stop: float,
    entry_time: int,
    target_time: int,
    stop_time: int,
    status: str,
    exit_index: float | None = None,
    exit_time: int | None = None,
    vix: float,
    as_of: int | None = None,
    account_size: float = 1_000_000,
    risk_mode: str = "percent",
    risk_percent: float = 1,
    risk_rupees: float = 10_000,
) -> PositionOptionEstimate | None:
    """Reprice the contract at entry, target, and stop. None when the position has not started."""
    if side not in ("long", "short") or status in ("pending", "not_entered") or vix <= 0:
        return None
    if as_of is not None and (entry_time > as_of or (status == "target" and target_time > as_of) or (status in ("stop", "ambiguous") and stop_time > as_of)):
        return None
    direction = "LONG" if side == "long" else "SHORT"
    day = ist_date(entry_time)
    cfg = load_option_model()
    contract = choose_contract(
        direction,
        entry,
        day,
        calendar=load_default_calendar(),
        lots=load_default_lot_table(),
        steps=load_default_step_table(),
    )
    entry_premium = _priced(contract.kind, entry, contract.strike, entry_time, contract.expiry, vix, cfg)
    target_premium = _priced(contract.kind, target, contract.strike, target_time, contract.expiry, vix, cfg)
    stop_premium = _priced(contract.kind, stop, contract.strike, stop_time, contract.expiry, vix, cfg)
    if status == "target":
        exit_premium, exit_when = target_premium, target_time
    elif status in ("stop", "ambiguous"):
        exit_premium, exit_when = stop_premium, stop_time
    else:
        spot = entry if exit_index is None else exit_index
        exit_when = entry_time if exit_time is None else exit_time
        if as_of is not None and exit_when > as_of:
            return None
        exit_premium = _priced(contract.kind, spot, contract.strike, exit_when, contract.expiry, vix, cfg)
    costs = get_cost_model("options")
    buy = costs.leg_cost("BUY", entry_premium, contract.lot_size, day)
    sell = costs.leg_cost("SELL", exit_premium, contract.lot_size, ist_date(exit_when))
    gross = (exit_premium - entry_premium) * contract.lot_size
    charges = float(buy.total + sell.total)
    net = gross - charges
    stop_sell = costs.leg_cost("SELL", stop_premium, contract.lot_size, ist_date(stop_time))
    premium_lost = max(0.0, entry_premium - stop_premium) * contract.lot_size
    option_risk = premium_lost + float(buy.total + stop_sell.total)
    budget = risk_rupees if risk_mode == "rupees" else account_size * risk_percent / 100
    option_lots = math.floor(budget / option_risk) if option_risk > 0 and budget > 0 else 0
    bits = [
        f"{contract.symbol} estimated",
        f"entry {entry_premium:.2f}",
        f"target {target_premium:.2f}",
        f"stop {stop_premium:.2f}",
        f"P&L/lot {net:.2f}",
        "option-based quantity",
        f"{option_lots} lots",
    ]
    if status == "ambiguous":
        bits.append("stop assumed")
    if option_lots == 0 and option_risk > budget:
        bits.append(f"0 lots: risk per lot Rs {option_risk:.2f} exceeds budget Rs {budget:.2f}")
    return PositionOptionEstimate(
        kind=contract.kind,
        strike=contract.strike,
        expiry=contract.expiry,
        lot_size=contract.lot_size,
        entry_premium=entry_premium,
        target_premium=target_premium,
        stop_premium=stop_premium,
        exit_premium=exit_premium,
        gross_per_lot=gross,
        charges_per_lot=charges,
        net_per_lot=net,
        option_lots=option_lots,
        option_risk_per_lot=option_risk,
        risk_budget=budget,
        label=" ".join(bits),
    )
