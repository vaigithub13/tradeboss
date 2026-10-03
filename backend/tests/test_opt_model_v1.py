"""Option model v1 (carry + DTE scales) and real-premium fills."""

from __future__ import annotations

import math
from datetime import date

from app.options.bs import bs_forward_price, bs_price, snap_premium
from app.options.contract import choose_contract
from app.options.history import ContractRef, OptionHistoryStore
from app.options.model import (
    HISTORY_START,
    MODEL_ONLY_WARNING,
    MODELLED_FILL_WARNING,
    load_option_model,
)
from app.options.time import years_to_expiry
from tests.opt_helpers import CAL, LOTS, STEPS, bar, estimate, ist, trade


class MapTape:
    def __init__(self, rows: dict[tuple, float]) -> None:
        self.rows = rows

    def premium_at(self, expiry: date, strike: float, kind: str, time: int) -> float | None:
        return self.rows.get((expiry, float(strike), kind, int(time)))


class EmptyTape:
    def premium_at(self, expiry: date, strike: float, kind: str, time: int) -> float | None:
        return None


def _oct5():
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]
    return fill, ex, index, vix


def test_shipped_config_is_option_model_v1() -> None:
    cfg = load_option_model()
    assert cfg.model_version == "option model v1"
    assert cfg.model_as_of == "2026-10-03"
    assert cfg.calibration_ref == "data/validation/option_model_carry_2026-10-03.json"
    assert cfg.r == 0.072 and cfg.q == 0.0
    assert cfg.carry_basis == "calendar" and cfg.time_basis == "trading"
    assert cfg.real_premiums is True
    assert cfg.scale_for(0) == 0.6489
    assert cfg.scale_for(1) == 0.8519
    assert cfg.scale_for(2) == 0.9014
    assert cfg.scale_for(3) == 0.9064 and cfg.scale_for(4) == 0.9064
    assert cfg.scale_for(5) == 0.9022 and cfg.scale_for(9) == 0.9022


def test_forward_price_matches_black_scholes_when_the_clocks_agree() -> None:
    args = ("CE", 25010.0, 25000.0, 705 / 93750, 0.14, 0.072, 0.0)
    assert bs_forward_price(*args[:4], args[3], *args[4:]) == bs_price(*args)
    # Hull call, one clock
    assert abs(bs_forward_price("CE", 42, 40, 0.5, 0.5, 0.20, 0.10, 0.0) - 4.7594) < 5e-4
    S, K, tv, tc, sig, r = 25000.0, 25050.0, 3 / 250, 5 / 365, 0.119266, 0.072
    c = bs_forward_price("CE", S, K, tv, tc, sig, r, 0.0)
    p = bs_forward_price("PE", S, K, tv, tc, sig, r, 0.0)
    assert abs((c - p) - (S - K * math.exp(-r * tc))) < 1e-6


def test_default_overlay_records_v1_and_prices_with_the_dte_scale() -> None:
    fill, ex, index, vix = _oct5()
    out = estimate(
        [trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix,
        config=load_option_model(), tape=EmptyTape(),
    )
    row = out.option.trades[0]
    assert out.option.settings["model_version"] == "option model v1"
    assert out.option.to_dict()["model_version"] == "option model v1"
    assert out.option.carry["model_version"] == "option model v1"
    assert out.option.carry["calibration_ref"].endswith("option_model_carry_2026-10-03.json")
    assert row["model_version"] == "option model v1"
    exp = date(2026, 10, 6)
    t_vol = years_to_expiry(fill, exp, CAL.is_trading_day, "trading")
    t_carry = years_to_expiry(fill, exp, CAL.is_trading_day, "calendar")
    raw = bs_forward_price("CE", 25010, 25000, t_vol, t_carry, 0.8519 * 0.14, 0.072, 0.0)
    assert row["iv"] == 0.8519 * 0.14
    assert row["entry_premium"] == snap_premium(raw)
    assert row["entry_source"] == "modelled" and "modelled" in row["flags"]
    assert any(MODELLED_FILL_WARNING in w for w in out.option.warnings)
    assert out.option.counters["modelled_fills"] == 2 and out.option.counters["real_fills"] == 0
    assert out.option.premium_source["modelled"]["trades"] == 1
    assert out.option.premium_source["real"]["trades"] == 0


def test_real_bar_is_the_fill_and_a_missing_bar_is_flagged_modelled() -> None:
    fill, ex, index, vix = _oct5()
    contract = choose_contract(
        "LONG", 25010, date(2026, 10, 5), calendar=CAL, lots=LOTS, steps=STEPS,
    )
    both = MapTape({
        (contract.expiry, contract.strike, contract.kind, fill): 80.0,
        (contract.expiry, contract.strike, contract.kind, ex): 90.0,
    })
    real = estimate(
        [trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix,
        config=load_option_model(), tape=both,
    ).option
    row = real.trades[0]
    assert "modelled" not in row["flags"]
    assert (row["entry_premium"], row["exit_premium"]) == (80.0, 90.0)
    assert (row["entry_fill"], row["exit_fill"]) == (80.5, 89.5)
    assert (row["entry_source"], row["exit_source"]) == ("real", "real")
    assert real.counters["real_fills"] == 2 and real.counters["modelled_fills"] == 0
    assert real.premium_source["real"]["trades"] == 1
    assert real.premium_source["real"]["net_pnl"] == row["net_pnl"]
    assert not any(MODELLED_FILL_WARNING in w for w in real.warnings)

    entry_only = MapTape({(contract.expiry, contract.strike, contract.kind, fill): 80.0})
    mixed = estimate(
        [trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix,
        config=load_option_model(), tape=entry_only,
    ).option
    m = mixed.trades[0]
    assert m["entry_source"] == "real" and m["exit_source"] == "modelled"
    assert "modelled" in m["flags"]
    assert mixed.counters == {"priced": 1, "unpriced": 0, "real_fills": 1, "modelled_fills": 1}
    assert mixed.premium_source["modelled"]["trades"] == 1
    assert mixed.premium_source["modelled"]["net_pnl"] == m["net_pnl"]
    assert mixed.premium_source["real"]["trades"] == 0
    assert any(MODELLED_FILL_WARNING in w for w in mixed.warnings)


def test_twenty_percent_modelled_does_not_warn_and_more_does() -> None:
    fill, ex, index, vix = _oct5()
    contract = choose_contract(
        "LONG", 25010, date(2026, 10, 5), calendar=CAL, lots=LOTS, steps=STEPS,
    )
    # 4 real fills + 1 modelled fill = 20% exactly
    tape = MapTape({
        (contract.expiry, contract.strike, contract.kind, fill): 80.0,
        (contract.expiry, contract.strike, contract.kind, ex): 90.0,
    })
    # second trade: entry real (same minute, same contract), exit missing — share the tape,
    # so both trades see the exit bar. Use two different exits by giving only one exit time
    # and a second trade that exits a minute the tape does not have.
    ex2 = ist(2026, 10, 5, 11, 0)
    index2 = index + [bar(ex2, 25080)]
    vix2 = vix + [bar(ex2, 14.0)]
    out = estimate(
        [
            trade(tid=1, entry=(fill, 25010), exit=(ex, 25060)),
            trade(tid=2, entry=(fill, 25010), exit=(ex2, 25080)),
        ],
        index2, vix2, config=load_option_model(), tape=tape,
    )
    # trade 1: 2 real. trade 2: entry real, exit modelled. 3 real + 1 modelled = 25%. That warns.
    # Build exactly 20% with 4 real trades and one mixed: 8 real + 1 real + 1 modelled = 10 fills, 10%.
    trades = [trade(tid=i, entry=(fill, 25010), exit=(ex, 25060)) for i in range(1, 5)]
    trades.append(trade(tid=5, entry=(fill, 25010), exit=(ex2, 25080)))
    exact = estimate(trades, index2, vix2, config=load_option_model(), tape=tape).option
    assert exact.counters["real_fills"] == 9 and exact.counters["modelled_fills"] == 1
    assert not any(MODELLED_FILL_WARNING in w for w in exact.warnings)
    assert out.option.counters["modelled_fills"] / (
        out.option.counters["real_fills"] + out.option.counters["modelled_fills"]
    ) > 0.20
    assert any(MODELLED_FILL_WARNING in w for w in out.option.warnings)


def test_a_period_before_the_history_is_marked_model_only() -> None:
    assert HISTORY_START == date(2024, 10, 3)
    fill, ex = ist(2024, 9, 2, 10, 0), ist(2024, 9, 2, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]
    out = estimate([trade(entry=(fill, 25010), exit=(ex, 25060), lot_size=25)], index, vix)
    assert MODEL_ONLY_WARNING in out.option.warnings
    fill2, ex2, index2, vix2 = _oct5()
    later = estimate([trade(entry=(fill2, 25010), exit=(ex2, 25060))], index2, vix2)
    assert MODEL_ONLY_WARNING not in later.option.warnings


def test_store_premium_at_is_the_open_of_that_minute(tmp_path) -> None:
    store = OptionHistoryStore(tmp_path)
    exp = date(2026, 10, 6)
    t = ist(2026, 10, 5, 10, 0)
    ref = ContractRef(25000.0, "CE", "k", "NIFTY", 65)
    store.write(exp, ref, [{
        "t": t * 1000, "open": 80.0, "high": 81.0, "low": 79.0, "close": 80.5, "volume": 10, "oi": None,
    }], "test")
    assert store.premium_at(exp, 25000, "CE", t) == 80.0
    assert store.premium_at(exp, 25000, "CE", t + 60) is None
    assert store.premium_at(exp, 25050, "CE", t) is None
    assert store.premium_at(exp, 25000, "PE", t) is None
