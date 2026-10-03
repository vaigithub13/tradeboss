"""A backtest spanning 2022-2026 uses the right lot, expiry and cost row on EACH side of every change date.

One run over 34 chosen days (a day before and a day after each lot / cost / expiry-rule change). Each day the
strategy buys at the second minute and sells at the fourth (index 100 -> 110), with the shipped lot table, the
shipped expiry calendar and the shipped cost table. All expectations below are written by hand from the
circulars - not computed by the code under test."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

from app.backtest.contracts import Signal, Strategy
from app.backtest.costs import CostModel, load_default_cost_table
from app.backtest.engine import run_backtest
from app.backtest.expiry import load_default_calendar
from app.backtest.lots import load_default_lot_table
from app.backtest.sources import ListSource
from tests.bt_helpers import cfg, day_bars, hhmm

CAL = load_default_calendar()
D = date.fromisoformat

# trade date -> (expiry of the nearest contract, its cycle, lot size)
EXPECTED: dict[str, tuple[str, str, int]] = {
    "2022-01-03": ("2022-01-06", "weekly", 50),
    "2023-03-31": ("2023-04-06", "weekly", 50),
    "2023-04-03": ("2023-04-06", "weekly", 50),
    "2024-03-28": ("2024-03-28", "monthly", 50),
    "2024-04-01": ("2024-04-04", "weekly", 50),
    "2024-04-25": ("2024-04-25", "monthly", 50),  # last monthly with the 50 lot
    "2024-04-26": ("2024-05-02", "weekly", 25),  # first weekly with the 25 lot
    "2024-09-30": ("2024-10-03", "weekly", 25),
    "2024-10-01": ("2024-10-03", "weekly", 25),
    "2024-11-18": ("2024-11-21", "weekly", 25),  # 75-lot contracts exist from 20 Nov, but not for this expiry
    "2024-11-21": ("2024-11-21", "weekly", 25),
    "2024-12-19": ("2024-12-19", "weekly", 25),  # last weekly with 25
    "2024-12-20": ("2024-12-26", "monthly", 25),  # December monthly: still 25
    "2025-01-02": ("2025-01-02", "weekly", 75),  # first weekly with 75
    "2025-01-29": ("2025-01-30", "monthly", 25),  # last monthly with 25
    "2025-01-31": ("2025-02-06", "weekly", 75),
    "2025-08-28": ("2025-08-28", "monthly", 75),  # last Thursday expiry
    "2025-09-01": ("2025-09-02", "weekly", 75),  # first Tuesday expiry
    "2025-10-28": ("2025-10-28", "monthly", 75),
    "2025-10-29": ("2025-11-04", "weekly", 75),
    "2025-12-23": ("2025-12-23", "weekly", 75),  # last weekly with 75
    "2025-12-24": ("2025-12-30", "monthly", 75),
    "2025-12-30": ("2025-12-30", "monthly", 75),  # last monthly with 75
    "2025-12-31": ("2026-01-06", "weekly", 65),  # first weekly with 65
    "2026-01-06": ("2026-01-06", "weekly", 65),
    "2026-02-27": ("2026-03-02", "weekly", 65),  # Holi (3-Mar) moves the Tuesday expiry to Monday
    "2026-03-02": ("2026-03-02", "weekly", 65),
    "2026-03-30": ("2026-03-30", "monthly", 65),  # 31-Mar is a holiday: the monthly moves to Monday
    "2026-04-01": ("2026-04-07", "weekly", 65),
    "2026-09-30": ("2026-10-06", "weekly", 65),
}

# trade date -> (STT %, exchange %, IPFT % or None) of the cost row in force, from the circulars
RATES = [
    ("2022-01-01", "0.05", "0.053", None),
    ("2023-04-01", "0.0625", "0.05", "0.0005"),
    ("2024-04-01", "0.0625", "0.0495", "0.0005"),
    ("2024-10-01", "0.1", "0.03503", "0.0005"),
    ("2026-03-01", "0.1", "0.03553", None),
    ("2026-04-01", "0.15", "0.03553", None),
]


def rates_on(day: str) -> tuple[str, str, str | None]:
    row = [r for r in RATES if r[0] <= day][-1]
    return row[1], row[2], row[3]


class BuyThenSell(Strategy):
    name = "buy_then_sell"

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        minute = hhmm(bar["time"])
        if minute == "09:15":
            return [Signal("BUY", 1)]
        if minute == "09:17":
            return [Signal("EXIT", 1)]
        return []


def q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def run_all():  # noqa: ANN201
    candles = []
    for d in EXPECTED:
        y, m, dd = (int(x) for x in d.split("-"))
        candles += day_bars((y, m, dd), [(100, 100, 100, 100)] * 2 + [(110, 110, 110, 110)] * 2)
    asked: dict[date, tuple[date, str]] = {}

    def contract(day: date) -> tuple[date, str]:
        e = CAL.next_expiry(day)  # the nearest weekly contract, today's expiry included
        asked[day] = (e.date, e.kind)
        return e.date, e.kind

    res = run_backtest(
        BuyThenSell(), ListSource(candles, 1),
        cfg(lot_size=None, lot_table=load_default_lot_table(), underlying="NIFTY", contract=contract,
            cost_model=CostModel("options", load_default_cost_table())),
    )
    return res, asked


RES, ASKED = run_all()
TRADES = {max(k for k in EXPECTED if D(k) <= date.fromtimestamp(t.entry_time + 19_800)): t for t in RES.trades}


def test_the_whole_span_runs_without_a_missing_rate_a_missing_lot_or_an_ambiguous_lot() -> None:
    assert len(RES.trades) == len(EXPECTED) == 30
    assert RES.data["first_time"] < RES.data["last_time"]
    assert RES.metrics["trades"] == 30 and RES.metrics["total_charges"] > 0


@pytest.mark.parametrize("day", list(EXPECTED))
def test_the_right_expiry_and_lot_on_each_side_of_every_change(day: str) -> None:
    expiry, cycle, lot = EXPECTED[day]
    assert ASKED[D(day)] == (D(expiry), cycle)
    t = TRADES[day]
    assert (t.lot_size, t.units) == (lot, lot)
    assert t.net_pnl == pytest.approx(10.0 * lot - t.charges_total, abs=0.005)


@pytest.mark.parametrize("day", list(EXPECTED))
def test_the_right_cost_row_on_each_side_of_every_change(day: str) -> None:
    stt, exch, ipft = rates_on(day)
    lot = EXPECTED[day][2]
    c = TRADES[day].charges
    buy_turnover, sell_turnover = Decimal(100 * lot), Decimal(110 * lot)
    assert c["stt"] == float(q(sell_turnover * Decimal(stt) / 100))  # STT on the sell leg only
    assert c["exchange"] == float(q(buy_turnover * Decimal(exch) / 100) + q(sell_turnover * Decimal(exch) / 100))
    assert c["brokerage"] == 40.0 and c["stamp"] == float(q(buy_turnover * Decimal("0.003") / 100))
    taxable_buy, taxable_sell = Decimal(20) + q(buy_turnover * Decimal(exch) / 100), Decimal(20) + q(sell_turnover * Decimal(exch) / 100)
    if ipft is None:
        assert "ipft" not in c
    else:
        legs = (q(buy_turnover * Decimal(ipft) / 100), q(sell_turnover * Decimal(ipft) / 100))
        assert c["ipft"] == float(sum(legs))
        taxable_buy, taxable_sell = taxable_buy + legs[0], taxable_sell + legs[1]  # GST on IPFT too (decision)
    assert c["gst"] == float(q(taxable_buy * 18 / 100) + q(taxable_sell * 18 / 100))


def test_the_result_names_every_unverified_row_it_used_and_nothing_else() -> None:
    warned = [w for w in RES.warnings if "UNVERIFIED" in w]
    assert [w.split()[3] for w in warned] == [r[0] for r in RATES]
