"""Option-premium exits (target / stop as a % of the entry fill) and the profit given back per trade.

The index trade is unchanged; the option leg can leave earlier, on the contract's real 1m bars.
Rules: the walk covers the minutes from the entry (the entry minute too when the entry filled at its open)
up to the minute before the index exit. A minute that opens beyond a level exits at that open; inside a minute
the level is the fill; a minute that touches both levels exits at the stop (as the engine does).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.options.contract import choose_contract
from app.options.model import OptionModelConfig, load_option_model
from tests.opt_helpers import CAL, LOTS, STEPS, bar, estimate, ist, trade

FILL, EXIT = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
INDEX = [bar(FILL - 60, 25010), bar(FILL, 25010), bar(EXIT, 25060)]
VIX = [bar(FILL, 14.0), bar(EXIT, 14.0)]
CONTRACT = choose_contract("LONG", 25010, date(2026, 10, 5), calendar=CAL, lots=LOTS, steps=STEPS)
UNITS = CONTRACT.lot_size


class Tape:
    """Minute bars of the chosen contract. Unlisted minutes are flat at `flat`."""

    def __init__(self, bars: dict[int, tuple[float, float, float, float]], flat: float = 100.0) -> None:
        self.bars, self.flat = bars, flat

    def bar_at(self, expiry: date, strike: float, kind: str, time: int):
        if (expiry, float(strike), kind) != (CONTRACT.expiry, CONTRACT.strike, CONTRACT.kind):
            return None
        if not FILL <= time <= EXIT:
            return None
        return self.bars.get(time, (self.flat,) * 4)

    def premium_at(self, expiry: date, strike: float, kind: str, time: int):
        b = self.bar_at(expiry, strike, kind, time)
        return None if b is None else b[0]


def cfg(target: float | None = None, stop: float | None = None, slip: float = 0.0) -> OptionModelConfig:
    shipped = load_option_model()
    return OptionModelConfig(**{**shipped.to_dict(), "vix_scales": shipped.vix_scales, "option_fill": "delta_adjusted",
                                "slippage_points": slip, "premium_target_pct": target, "premium_stop_pct": stop})


def row(tape: Tape, config: OptionModelConfig) -> dict:
    out = estimate([trade(entry=(FILL, 25010), exit=(EXIT, 25060))], INDEX, VIX, config=config, tape=tape)
    return out.option.trades[0]


def m(k: int) -> int:
    return FILL + 60 * k


def test_the_target_fills_at_its_level_in_the_first_minute_that_reaches_it() -> None:
    r = row(Tape({m(5): (110.0, 131.0, 109.0, 125.0), m(9): (140.0, 150.0, 139.0, 145.0)}), cfg(target=0.30, stop=0.20))
    assert r["entry_fill"] == 100.0
    assert (r["exit_time"], r["exit_fill"], r["exit_source"]) == (m(5), 130.0, "real")
    assert r["exit_reason"] == "premium_target" and r["gross_pnl"] == 30.0 * UNITS


def test_the_stop_fills_at_its_level_and_a_gap_through_it_fills_at_the_open() -> None:
    r = row(Tape({m(3): (95.0, 96.0, 79.0, 85.0)}), cfg(target=0.30, stop=0.20))
    assert (r["exit_time"], r["exit_fill"], r["exit_reason"]) == (m(3), 80.0, "premium_stop")
    gapped = row(Tape({m(3): (70.0, 72.0, 65.0, 71.0)}), cfg(target=0.30, stop=0.20))
    assert (gapped["exit_time"], gapped["exit_fill"]) == (m(3), 70.0)


def test_a_minute_that_touches_both_levels_exits_at_the_stop() -> None:
    r = row(Tape({m(4): (100.0, 135.0, 75.0, 100.0)}), cfg(target=0.30, stop=0.20))
    assert (r["exit_fill"], r["exit_reason"]) == (80.0, "premium_stop")


def test_slippage_applies_to_a_premium_exit() -> None:
    r = row(Tape({m(5): (110.0, 131.0, 109.0, 125.0)}), cfg(target=0.30, stop=0.20, slip=0.2))
    assert r["entry_fill"] == 100.2
    target = round(100.2 * 1.3, 2)
    assert r["exit_premium"] == pytest.approx(target) and r["exit_fill"] == pytest.approx(target - 0.2)


def test_without_a_level_reached_the_option_leaves_with_the_index_and_reports_what_it_gave_back() -> None:
    r = row(Tape({m(10): (100.0, 120.0, 99.0, 110.0)}), cfg(target=0.30, stop=0.20))
    assert r["exit_time"] == EXIT and r["exit_reason"] == "index"
    assert r["max_open_gross"] == 20.0 * UNITS
    assert r["given_back"] == pytest.approx(r["max_open_gross"] - r["gross_pnl"])


def test_no_premium_exit_configured_keeps_the_index_exit_and_still_reports_the_give_back() -> None:
    r = row(Tape({m(5): (110.0, 160.0, 50.0, 125.0)}), cfg())
    assert r["exit_time"] == EXIT and r["exit_reason"] == "index"
    assert r["max_open_gross"] == 60.0 * UNITS


def test_without_real_bars_the_premium_exit_is_not_checked_and_the_trade_says_so() -> None:
    class Empty:
        def bar_at(self, *a):
            return None

        def premium_at(self, *a):
            return None

    r = row(Empty(), cfg(target=0.30, stop=0.20))  # type: ignore[arg-type]
    assert r["exit_time"] == EXIT and "premium_exit_unchecked" in r["flags"] and r["max_open_gross"] is None


def test_levels_must_be_positive() -> None:
    with pytest.raises(ValueError):
        cfg(target=0.0)
    with pytest.raises(ValueError):
        cfg(stop=1.0)  # a 100% stop is a zero premium
