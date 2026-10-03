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
    OptionModelConfig,
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


class BarTape:
    """One option minute: open 80, high 100, low 70, close 90."""

    def __init__(self, expiry: date, strike: float, kind: str, times: list[int]) -> None:
        self.expiry, self.strike, self.kind, self.times = expiry, float(strike), kind, set(times)

    def premium_at(self, expiry: date, strike: float, kind: str, time: int) -> float | None:
        bar = self.bar_at(expiry, strike, kind, time)
        return None if bar is None else bar[0]

    def bar_at(self, expiry: date, strike: float, kind: str, time: int):
        if (expiry, float(strike), kind, int(time)) in {(self.expiry, self.strike, self.kind, t) for t in self.times}:
            return (80.0, 100.0, 70.0, 90.0)
        return None


def test_a_stop_inside_the_minute_is_not_filled_at_the_option_open() -> None:
    """optimistic buys the pre-breakout open. adverse and worst pay through the minute.
    A market fill at the open stays at 80 in every mode."""
    fill, ex, index, vix = _oct5()
    contract = choose_contract(
        "LONG", 25010, date(2026, 10, 5), calendar=CAL, lots=LOTS, steps=STEPS,
    )
    tape = BarTape(contract.expiry, contract.strike, contract.kind, [fill, ex])
    inside = trade(entry=(fill, 25010), exit=(ex, 25060), entry_at_open=False, exit_at_open=False)
    at_open = trade(entry=(fill, 25010), exit=(ex, 25060))
    shipped = load_option_model()
    for mode, entry, exit_ in (
        ("optimistic", 80.0, 80.0),
        ("adverse", 90.0, 80.0),
        ("worst", 100.0, 70.0),
    ):
        cfg = OptionModelConfig(**{**shipped.to_dict(), "vix_scales": shipped.vix_scales, "option_fill": mode})
        row = estimate([inside], index, vix, config=cfg, tape=tape).option.trades[0]
        assert (row["entry_premium"], row["exit_premium"]) == (entry, exit_), mode
        opened = estimate([at_open], index, vix, config=cfg, tape=tape).option.trades[0]
        assert (opened["entry_premium"], opened["exit_premium"]) == (80.0, 80.0), mode


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
    assert store.bar_at(exp, 25000, "CE", t) == (80.0, 81.0, 79.0, 80.5)
    assert store.premium_at(exp, 25000, "CE", t + 60) is None
    assert store.premium_at(exp, 25050, "CE", t) is None
    assert store.premium_at(exp, 25000, "PE", t) is None


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _hand_delta(kind: str, S: float, K: float, t_vol: float, t_carry: float, sigma: float, r: float, q: float) -> float:
    """Black-Scholes delta written out here, so the example does not call the implementation."""
    vs = sigma * math.sqrt(t_vol)
    d1 = (math.log(S / K) + (r - q) * t_carry + 0.5 * sigma * sigma * t_vol) / vs
    signed = _ncdf(d1) if kind == "CE" else _ncdf(d1) - 1.0
    return math.exp(-q * t_carry) * signed


def test_delta_adjusted_premium_is_the_hand_example_and_clamps_to_the_minute() -> None:
    from app.options.model import delta_adjusted_premium

    # 80 + 0.40 * (25025 - 25000) = 90, inside 70..100
    assert delta_adjusted_premium(80.0, 0.40, 25025.0, 25000.0, 70.0, 100.0) == 90.0
    # A put delta is negative: the same rise cuts the premium. 80 + (-0.40) * 25 = 70
    assert delta_adjusted_premium(80.0, -0.40, 25025.0, 25000.0, 60.0, 100.0) == 70.0
    # 80 + 0.40 * 100 = 120, above the minute high
    assert delta_adjusted_premium(80.0, 0.40, 25100.0, 25000.0, 70.0, 100.0) == 100.0
    # 80 + 0.40 * (-40) = 64, below the minute low
    assert delta_adjusted_premium(80.0, 0.40, 24960.0, 25000.0, 70.0, 100.0) == 70.0
    # 80 + (-0.40) * 100 = 40, a put on a rally, clamped up to the low
    assert delta_adjusted_premium(80.0, -0.40, 25100.0, 25000.0, 70.0, 100.0) == 70.0


def test_a_stop_fill_uses_model_v1_delta_and_a_market_fill_stays_at_the_open() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [
        bar(fill - 60, 25000),
        {"time": fill, "open": 25000.0, "high": 25040.0, "low": 24980.0, "close": 25020.0, "volume": 1, "oi": None},
        bar(ex, 25020),
    ]
    vix = [bar(fill, 20.0), bar(ex, 20.0)]
    cfg = OptionModelConfig(
        r=0.0, q=0.0, vix_scale=1.0, real_premiums=True, slippage_points=0.0, snap_tick=False,
        option_fill="delta_adjusted", time_basis="trading", carry_basis="trading",
    )
    long = choose_contract("LONG", 25000, date(2026, 10, 5), calendar=CAL, lots=LOTS, steps=STEPS)
    short = choose_contract("SHORT", 25000, date(2026, 10, 5), calendar=CAL, lots=LOTS, steps=STEPS)
    wide = (80.0, 200.0, 1.0, 90.0)

    def tape_for(contract):
        class _Tape:
            def premium_at(self, expiry, strike, kind, time):
                bar_ = self.bar_at(expiry, strike, kind, time)
                return None if bar_ is None else bar_[0]

            def bar_at(self, expiry, strike, kind, time):
                if (expiry, float(strike), kind) == (contract.expiry, contract.strike, contract.kind) and int(time) in (fill, ex):
                    return wide
                return None
        return _Tape()

    def expect(contract, trigger: float) -> float:
        iv = 20.0 / 100.0
        t_vol = years_to_expiry(fill, contract.expiry, CAL.is_trading_day, "trading")
        t_carry = years_to_expiry(fill, contract.expiry, CAL.is_trading_day, "trading")
        delta = _hand_delta(contract.kind, 25000.0, contract.strike, t_vol, t_carry, iv, 0.0, 0.0)
        return 80.0 + delta * (trigger - 25000.0)

    call = estimate(
        [trade(entry=(fill, 25020), exit=(ex, 25020), entry_at_open=False)],
        index, vix, config=cfg, tape=tape_for(long),
    ).option.trades[0]
    assert call["entry_premium"] == expect(long, 25020.0)
    assert call["entry_premium"] > 80.0
    assert call["exit_premium"] == 80.0

    put = estimate(
        [trade(direction="SHORT", entry=(fill, 25020), exit=(ex, 25020), entry_at_open=False)],
        index, vix, config=cfg, tape=tape_for(short),
    ).option.trades[0]
    assert put["entry_premium"] == expect(short, 25020.0)
    assert put["entry_premium"] < 80.0

    tight = (80.0, 81.0, 79.0, 80.5)

    class Tight:
        def premium_at(self, expiry, strike, kind, time):
            return 80.0

        def bar_at(self, expiry, strike, kind, time):
            return tight

    clamped = estimate(
        [trade(entry=(fill, 26000), exit=(ex, 25020), entry_at_open=False)],
        index, vix, config=cfg, tape=Tight(),
    ).option.trades[0]
    assert clamped["entry_premium"] == 81.0

    opened = estimate(
        [trade(entry=(fill, 25020), exit=(ex, 25020))],
        index, vix, config=cfg, tape=tape_for(long),
    ).option.trades[0]
    assert opened["entry_premium"] == 80.0 and opened["exit_premium"] == 80.0


def test_delta_adjusted_is_the_default_and_minute_open_is_labelled_optimistic() -> None:
    from app.backtest.catalog import parse_config

    assert OptionModelConfig().option_fill == "delta_adjusted"
    parsed = parse_config({
        "strategy": "log_xz", "symbol": "NIFTY50", "timeframe": "5m",
        "start": "2024-10-03", "end": "2026-06-30",
        "sessions": ["normal", "weekend_full"], "mode": "options",
    })
    assert parsed["option_fill"] == "delta_adjusted"
    renamed = parse_config({**parsed, "option_fill": "minute_open"})
    assert renamed["option_fill"] == "optimistic"

    fill, ex, index, vix = _oct5()
    contract = choose_contract("LONG", 25010, date(2026, 10, 5), calendar=CAL, lots=LOTS, steps=STEPS)
    tape = BarTape(contract.expiry, contract.strike, contract.kind, [fill, ex])
    shipped = load_option_model()
    cfg = OptionModelConfig(**{**shipped.to_dict(), "vix_scales": shipped.vix_scales, "option_fill": "optimistic"})
    out = estimate(
        [trade(entry=(fill, 25010), exit=(ex, 25060), entry_at_open=False, exit_at_open=False)],
        index, vix, config=cfg, tape=tape,
    )
    assert out.option.trades[0]["entry_premium"] == 80.0
    assert out.option.settings["option_fill"] == "optimistic"
    assert any("optimistic" in warning for warning in out.option.warnings)
