"""Turn a setup hit into an entry, stop, target, and share quantity."""

from __future__ import annotations

import math

from app.scanner.bars import BAR_S, Bar, minute_of
from app.scanner.config import ScannerSettings
from app.scanner.models import Hit, Plan


def _target_r(settings: ScannerSettings) -> float:
    if settings.target_mode == "3r":
        return 3.0
    return 2.0


def orb_stop(hit: Hit, settings: ScannerSettings, *, side: str) -> float | None:
    if hit.range_high is None or hit.range_low is None:
        return hit.stop
    if settings.orb_stop == "opposite":
        return hit.range_low if side == "long" else hit.range_high
    return (hit.range_high + hit.range_low) / 2.0


def build_plan(
    hit: Hit,
    settings: ScannerSettings,
    *,
    symbol: str,
    next_bar: Bar | None = None,
) -> Plan:
    """`next_bar` is the bar after the trigger. Required when entry_mode is next_open and the bar has opened."""
    target_r = _target_r(settings)
    if hit.forming or hit.side == "flat":
        status = "expired" if minute_of(hit.time) >= 14 * 60 + 30 else "waiting"
        return Plan(
            instrument="equity_intraday",
            symbol=symbol,
            setup=hit.setup,
            side="flat",
            status=status,
            entry=None,
            stop=hit.stop,
            target=None,
            qty=0,
            risk_per_share=None,
            rupee_risk=None,
            rupee_reward=None,
            capital=None,
            rr=None,
            resistance=hit.resistance,
            support=hit.support,
            entry_time=hit.time,
            target_r=target_r,
            note="opening range not broken",
        )

    side = hit.side
    note = ""
    if settings.entry_mode == "next_open":
        if next_bar is None:
            return Plan(
                instrument="equity_intraday",
                symbol=symbol,
                setup=hit.setup,
                side=side,
                status="triggered",
                entry=None,
                stop=None,
                target=None,
                qty=0,
                risk_per_share=None,
                rupee_risk=None,
                rupee_reward=None,
                capital=None,
                rr=None,
                resistance=hit.resistance,
                support=hit.support,
                entry_time=hit.time + BAR_S,
                target_r=target_r,
                note="waiting for the next bar open",
            )
        entry = float(next_bar["open"])
        entry_time = int(next_bar["time"])
    else:
        entry = float(hit.close)
        entry_time = int(hit.time)

    if hit.setup == "orb15":
        stop = orb_stop(hit, settings, side=side)
    else:
        stop = hit.stop

    empty = Plan(
        instrument="equity_intraday",
        symbol=symbol,
        setup=hit.setup,
        side=side,
        status="triggered",
        entry=entry,
        stop=stop,
        target=None,
        qty=0,
        risk_per_share=None,
        rupee_risk=None,
        rupee_reward=None,
        capital=None,
        rr=None,
        resistance=hit.resistance,
        support=hit.support,
        entry_time=entry_time,
        target_r=target_r,
        note="stop is not on the right side of entry",
    )
    if stop is None:
        empty.note = "no stop"
        return empty
    risk = abs(entry - stop)
    if risk <= 0 or (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
        return empty

    level = hit.resistance if side == "long" else hit.support
    if settings.target_mode == "next_level" and level is not None:
        beyond = (side == "long" and level > entry) or (side == "short" and level < entry)
        if beyond:
            target = float(level)
            note = "target is the nearest swing"
        else:
            target = entry + target_r * risk if side == "long" else entry - target_r * risk
            note = "no swing beyond entry; target fell back to 2R/3R"
    else:
        if settings.target_mode == "next_level":
            note = "no swing beyond entry; target fell back to 2R/3R"
        target = entry + target_r * risk if side == "long" else entry - target_r * risk

    reward = abs(target - entry)
    rr = reward / risk
    qty = int(math.floor(settings.max_loss_per_trade / risk))
    if entry > 0 and qty * entry > settings.max_capital_per_trade:
        qty = int(math.floor(settings.max_capital_per_trade / entry))
    if qty < 0:
        qty = 0
    rupee_risk = qty * risk
    rupee_reward = qty * reward
    capital = qty * entry
    return Plan(
        instrument="equity_intraday",
        symbol=symbol,
        setup=hit.setup,
        side=side,
        status="triggered",
        entry=entry,
        stop=stop,
        target=target,
        qty=qty,
        risk_per_share=risk,
        rupee_risk=rupee_risk,
        rupee_reward=rupee_reward,
        capital=capital,
        rr=rr,
        resistance=hit.resistance,
        support=hit.support,
        entry_time=entry_time,
        target_r=target_r,
        note=note,
    )
