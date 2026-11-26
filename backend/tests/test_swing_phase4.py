"""Phase 4 momentum book. The gate numbers are not computed here."""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from app.swing.delivery import DeliveryTable
from app.swing.momentum import (
    HOLDOUT_END,
    HOLDOUT_START,
    MonthSpec,
    funded_slots,
    _m1_array,
    fair_market_values,
    liquid_mask,
    run_month,
    score_panel,
)
from app.swing.research import shift_param
from app.swing.tax import TaxLedger, TaxTable, grandfather_basis, is_long_term


def test_m1_ignores_the_last_21_sessions_and_the_future():
    quiet = np.linspace(100.0, 160.0, 400)
    jumped = quiet.copy()
    jumped[-21:] = jumped[-21:] * 3
    at = 399
    base = _m1_array(quiet, 252, 21)[at]
    assert base == _m1_array(jumped, 252, 21)[at]
    assert abs(base - (quiet[at - 21] / quiet[at - 252] - 1)) < 1e-12
    later = quiet.copy()
    later = np.append(later, [999.0])
    assert _m1_array(later, 252, 21)[at] == base


def test_m2_score_is_the_average_of_the_two_zscores():
    days = [date(2016, 1, 1) + timedelta(days=i) for i in range(40)]
    rows = []
    closes = {
        "A": np.linspace(100, 130, len(days)),
        "B": np.linspace(100, 110, len(days)),
        "C": np.linspace(100, 150, len(days)),
    }
    for lineage, series in closes.items():
        for i, day in enumerate(days):
            px = float(series[i])
            rows.append(_row(day, lineage, px, turnover=200_000_000))
    panel = score_panel(pd.DataFrame(rows), "M2", [days[-1]], {"short": 10, "long": 20, "skip": 5})
    assert set(panel["lineage"]) == {"A", "B", "C"}
    hand = {}
    for lineage, series in closes.items():
        hand[lineage] = 0.5 * (_hand_ratio(series, 10, 5) + _hand_ratio(series, 20, 5))
    values = np.array(list(hand.values()))
    z = (values - values.mean()) / values.std(ddof=1)
    expected = {lineage: float(z[i]) for i, lineage in enumerate(hand)}
    # The hand z above is the z of the averaged ratios, not the average of the z-scores.
    z6 = _zscore({k: _hand_ratio(v, 10, 5) for k, v in closes.items()})
    z12 = _zscore({k: _hand_ratio(v, 20, 5) for k, v in closes.items()})
    for _, row in panel.iterrows():
        assert abs(row["score"] - 0.5 * (z6[row["lineage"]] + z12[row["lineage"]])) < 1e-9
    del expected


def test_turnover_floor_drops_six_crore_and_keeps_twelve():
    days = [date(2016, 1, d) for d in range(4, 9)]
    rows = []
    for lineage, turn in (("LOW", 60_000_000), ("HIGH", 120_000_000)):
        for i, day in enumerate(days):
            rows.append(_row(day, lineage, 100 + i, turnover=turn))
    panel = score_panel(pd.DataFrame(rows), "M1", [days[-1]], {"far": 4, "skip": 1})
    assert list(panel["lineage"]) == ["HIGH"]
    mask = liquid_mask(pd.DataFrame(rows))
    assert int(mask.sum()) == 5


def test_buffer_keeps_a_top_2n_name_and_buys_the_next_open():
    d0, d1, d2, d3 = date(2016, 1, 29), date(2016, 2, 1), date(2016, 2, 26), date(2016, 3, 1)
    names = ["A", "B", "C", "D", "E"]
    bars = pd.DataFrame([_row(day, name, 100) for day in (d1, d2, d3) for name in names])
    scores = pd.DataFrame(
        [_score(d0, name, score) for name, score in (("A", 5), ("B", 4), ("C", 1), ("D", 2), ("E", 3))]
        + [_score(d2, name, score) for name, score in (("C", 5), ("D", 4), ("E", 3), ("A", 2), ("B", 1))]
    )
    result = run_month(bars, scores, DeliveryTable.load(), TaxTable.load(), _spec(date(2016, 2, 1), d3, n=2), [d0, d1, d2, d3])
    trades = result["trades"]
    sold = trades[(trades["lineage"] == "B") & (trades["reason"] == "rank")]
    assert len(sold) == 1
    assert sold.iloc[0]["exit_date"] == d3.isoformat()
    entered = trades[trades["lineage"] == "C"]
    assert set(entered["entry_date"]) == {d3.isoformat()}
    kept = trades[trades["lineage"] == "A"].iloc[0]
    assert kept["entry_date"] == d1.isoformat()
    assert kept["reason"] == "sample_end"
    assert (trades["entry_date"] > trades["signal_date"]).all()


def test_an_unsizable_leader_lets_the_next_name_fill_the_slot():
    d0, d1 = date(2016, 1, 29), date(2016, 2, 1)
    bars = pd.DataFrame([_row(d1, "BIG", 4000), _row(d1, "SMALL", 100)])
    scores = pd.DataFrame([_score(d0, "BIG", 2), _score(d0, "SMALL", 1)])
    spec = MonthSpec(Decimal("7000"), 1, d1, d1, "zero", slip_liquid=Decimal("0"), slip_other=Decimal("0"))
    trades = run_month(bars, scores, DeliveryTable.load(), TaxTable.load(), spec, [d0, d1])["trades"]
    assert set(trades["lineage"]) == {"SMALL"}


def test_regime_off_holds_cash():
    d0, d1 = date(2016, 1, 29), date(2016, 2, 1)
    bars = pd.DataFrame([_row(d1, "A", 100)])
    scores = pd.DataFrame([_score(d0, "A", 1)])
    spec = MonthSpec(Decimal("50000"), 10, d1, d1, "zero", regime=True, slip_liquid=Decimal("0"), slip_other=Decimal("0"))
    result = run_month(bars, scores, DeliveryTable.load(), TaxTable.load(), spec, [d0, d1], regime={d0: False})
    assert result["trades"].empty
    assert result["daily"].iloc[-1]["positions"] == 0


def test_min_notional_skips_a_four_thousand_rupee_fill():
    d0, d1 = date(2016, 1, 29), date(2016, 2, 1)
    bars = pd.DataFrame([_row(d1, "A", 4000)])
    scores = pd.DataFrame([_score(d0, "A", 1)])
    spec = MonthSpec(Decimal("7000"), 1, d1, d1, "zero", slip_liquid=Decimal("0"), slip_other=Decimal("0"))
    result = run_month(bars, scores, DeliveryTable.load(), TaxTable.load(), spec, [d0, d1])
    assert result["trades"].empty
    # A ₹5,000 slot does not divide a ₹100 share once slippage is on. The remainder is still a position.
    held = pd.DataFrame([_row(d1, "A", 100)])
    book = MonthSpec(Decimal("50000"), 10, d1, d1, "zero", slip_liquid=Decimal("0.001"), slip_other=Decimal("0.0025"))
    opened = run_month(held, scores, DeliveryTable.load(), TaxTable.load(), book, [d0, d1])
    assert not opened["trades"].empty


def test_five_missing_sessions_and_a_delisting():
    days = [date(2016, 1, 29), date(2016, 2, 1), date(2016, 2, 2), date(2016, 2, 3), date(2016, 2, 4), date(2016, 2, 5), date(2016, 2, 8), date(2016, 2, 9)]
    gap_rows = [_row(days[1], "GAP", 100, close=110)] + [_row(days[-1], "GAP", 110)]
    end_rows = [_row(days[1], "END", 100, close=105)]
    bars = pd.DataFrame(gap_rows + end_rows)
    scores = pd.DataFrame([_score(days[0], "GAP", 2), _score(days[0], "END", 1)])
    result = run_month(bars, scores, DeliveryTable.load(), TaxTable.load(), _spec(days[1], days[-1], n=2), days)
    trades = result["trades"].set_index("lineage")
    assert trades.loc["GAP", "reason"] == "no_bar"
    assert trades.loc["GAP", "exit_date"] == days[-2].isoformat()
    assert trades.loc["GAP", "exit_px"] == 110
    assert trades.loc["END", "reason"] == "delisted"
    assert trades.loc["END", "exit_date"] == days[2].isoformat()
    assert trades.loc["END", "exit_px"] == 105


def test_tax_stcg_ltcg_exemption_and_grandfather():
    table = TaxTable.load()
    short = TaxLedger(table)
    assert short.on_sale(date(2017, 1, 2), date(2017, 6, 1), Decimal("1000")) == Decimal("154.50")
    assert is_long_term(date(2016, 2, 29), date(2017, 2, 28)) is False
    assert is_long_term(date(2016, 2, 29), date(2017, 3, 1)) is True
    long = TaxLedger(table)
    assert long.on_sale(date(2018, 5, 2), date(2019, 5, 3), Decimal("150000")) == Decimal("5200.00")
    assert long.on_sale(date(2018, 6, 1), date(2019, 6, 1), Decimal("-40000")) == Decimal("-4160.00")
    revised = TaxLedger(table)
    assert revised.on_sale(date(2023, 1, 2), date(2024, 4, 2), Decimal("200000")) == Decimal("7800.00")
    newer = TaxLedger(table)
    assert newer.on_sale(date(2024, 7, 1), date(2024, 7, 23), Decimal("10000")) == Decimal("2080.00")
    assert grandfather_basis(Decimal("100"), Decimal("180"), Decimal("150")) == Decimal("150")
    assert grandfather_basis(Decimal("100"), Decimal("120"), Decimal("150")) == Decimal("120")

    signal, entry, exit_day = date(2017, 12, 29), date(2018, 1, 2), date(2019, 4, 3)
    bars = pd.DataFrame([_row(entry, "OLD", 100), _row(exit_day, "OLD", 200)])
    scores = pd.DataFrame([_score(signal, "OLD", 1)])
    spec = MonthSpec(Decimal("500000"), 1, entry, exit_day, "zero", slip_liquid=Decimal("0"), slip_other=Decimal("0"))
    plain = run_month(bars, scores, DeliveryTable.load(), table, spec, [signal, entry, exit_day])
    lifted = run_month(
        bars, scores, DeliveryTable.load(), table, spec, [signal, entry, exit_day], fmv={"OLD": Decimal("150")},
    )
    assert plain["metrics"]["end_equity_tax"] < plain["metrics"]["end_equity"]
    assert lifted["metrics"]["end_equity_tax"] > plain["metrics"]["end_equity_tax"]
    assert fair_market_values(pd.DataFrame([_row(date(2018, 1, 31), "OLD", 150)]))["OLD"] == Decimal("150")


def test_holdout_window_is_refused():
    try:
        MonthSpec(Decimal("50000"), 10, HOLDOUT_START, HOLDOUT_END)
    except ValueError as exc:
        assert "holdout" in str(exc)
    else:
        raise AssertionError("holdout window was accepted")
    MonthSpec(Decimal("50000"), 10, HOLDOUT_START, HOLDOUT_END, holdout=True)


def test_a_loss_reduces_the_names_held_instead_of_stopping_the_book():
    assert funded_slots(Decimal("50000"), 10, Decimal("5000")) == 10
    assert funded_slots(Decimal("41588"), 10, Decimal("5000")) == 8
    assert funded_slots(Decimal("4999"), 10, Decimal("5000")) == 0


def test_lookback_shift_is_half_up():
    assert shift_param(252, -0.2) == 202
    assert shift_param(252, 0.2) == 302
    assert shift_param(21, -0.2) == 17
    assert shift_param(21, 0.2) == 25
    assert shift_param(126, -0.2) == 101
    assert shift_param(126, 0.2) == 151


def _hand_ratio(closes: np.ndarray, lookback: int, skip: int) -> float:
    i = len(closes) - 1
    last = closes[i - skip]
    base = closes[i - skip - lookback]
    window = [closes[j] / closes[j - 1] - 1 for j in range(i - skip - lookback + 1, i - skip + 1)]
    vol = float(np.std(window, ddof=1))
    return (last / base - 1) / vol


def _zscore(values: dict[str, float]) -> dict[str, float]:
    series = np.array(list(values.values()))
    z = (series - series.mean()) / series.std(ddof=1)
    return {key: float(z[i]) for i, key in enumerate(values)}


def _spec(start: date, end: date, n: int = 10) -> MonthSpec:
    return MonthSpec(Decimal("20000"), n, start, end, "zero", slip_liquid=Decimal("0"), slip_other=Decimal("0"))


def _row(day: date, lineage: str, price: float, turnover: float = 200_000_000, close: float | None = None) -> dict:
    px = close if close is not None else price
    return {
        "date": day,
        "lineage": lineage,
        "symbol": lineage,
        "size": "large",
        "passes": True,
        "turnover_20d": turnover,
        "px_open": price,
        "px_high": max(price, px),
        "px_low": min(price, px),
        "px_close": px,
    }


def _score(day: date, lineage: str, score: float) -> dict:
    return {"date": day, "lineage": lineage, "symbol": lineage, "size": "large", "score": score}
