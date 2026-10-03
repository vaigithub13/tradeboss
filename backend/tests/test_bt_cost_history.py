"""The shipped cost-rate history (approved follow-up A): six dated rows, every rate "unverified"."""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from app.backtest.costs import CostModel, NoRatesForDate, load_default_cost_table

PATH = Path(__file__).resolve().parents[1] / "app/backtest/data/cost_rates.json"
TABLE = load_default_cost_table()
MODEL = CostModel("options", TABLE)
CHANGE_DATES = ["2022-01-01", "2023-04-01", "2024-04-01", "2024-10-01", "2026-03-01", "2026-04-01"]


def test_the_shipped_table_is_the_six_approved_rows() -> None:
    assert [r.effective_from.isoformat() for r in TABLE.rows] == CHANGE_DATES
    assert not (PATH.parent / "proposals").exists()


def test_every_rate_of_every_row_is_unverified_and_has_a_source() -> None:
    for row in TABLE.rows:
        assert row.unverified() and "gst_on" in row.sources
        assert all(v == "unverified" for v in row.verification.values()), row.effective_from
        for key in ("brokerage_flat", "stt_sell_pct", "exchange_pct", "sebi_per_crore", "stamp_buy_pct", "gst_pct"):
            assert "http" in row.sources[key], (row.effective_from, key)
        for e in row.extra:
            assert "http" in row.sources[e.name]


def test_ipft_and_its_gst_are_charged_from_2023_04_01_by_decision() -> None:
    by = {r.effective_from.isoformat(): r for r in TABLE.rows}
    assert not by["2022-01-01"].extra and not by["2026-03-01"].extra and not by["2026-04-01"].extra  # combined / negligible
    for d in ("2023-04-01", "2024-04-01", "2024-10-01"):
        (e,) = by[d].extra
        assert (e.name, str(e.pct), e.gst, e.sides) == ("ipft", "0.0005", True, ("BUY", "SELL"))


def test_nse_total_outflow_per_crore_is_what_the_circulars_say() -> None:
    def per_crore(row) -> Decimal:  # noqa: ANN001
        return (row.rates["exchange_pct"] + sum((e.pct for e in row.extra), Decimal("0"))) * Decimal(100_000)

    assert [str(per_crore(r)) for r in TABLE.rows] == [
        "5300.000", "5050.0000", "5000.0000", "3553.00000", "3553.00000", "3553.00000"]  # 53/50/49.5 per lakh + 50 IPFT; 3,503 + 50; 3,553


def test_nothing_changes_except_what_the_circulars_changed() -> None:
    for r in TABLE.rows:
        assert str(r.rates["brokerage_flat"]) == "20" and str(r.rates["sebi_per_crore"]) == "10"
        assert str(r.rates["stamp_buy_pct"]) == "0.003" and str(r.rates["gst_pct"]) == "18"
    assert [str(r.rates["stt_sell_pct"]) for r in TABLE.rows] == ["0.05", "0.0625", "0.0625", "0.1", "0.1", "0.15"]
    assert [str(r.rates["exchange_pct"]) for r in TABLE.rows] == ["0.053", "0.05", "0.0495", "0.03503", "0.03553", "0.03553"]


def q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@pytest.mark.parametrize(
    "day, stt_pct, exch_pct, ipft_pct",
    [
        ("2022-01-03", "0.05", "0.053", None),
        ("2023-03-31", "0.05", "0.053", None),
        ("2023-04-03", "0.0625", "0.05", "0.0005"),
        ("2024-03-28", "0.0625", "0.05", "0.0005"),
        ("2024-04-01", "0.0625", "0.0495", "0.0005"),
        ("2024-09-30", "0.0625", "0.0495", "0.0005"),
        ("2024-10-01", "0.1", "0.03503", "0.0005"),
        ("2026-02-27", "0.1", "0.03503", "0.0005"),
        ("2026-03-02", "0.1", "0.03553", None),
        ("2026-03-31", "0.1", "0.03553", None),
        ("2026-04-01", "0.15", "0.03553", None),
        ("2026-10-01", "0.15", "0.03553", None),
    ],
)
def test_each_side_of_every_change_date_uses_its_own_row(day: str, stt_pct: str, exch_pct: str, ipft_pct: str | None) -> None:
    on = date.fromisoformat(day)
    sell = MODEL.leg_cost("SELL", 120.0, 75, on)  # turnover 9,000
    turnover = Decimal(9000)
    assert sell.components["stt"] == q(turnover * Decimal(stt_pct) / 100)
    assert sell.components["exchange"] == q(turnover * Decimal(exch_pct) / 100)
    taxable = Decimal("20.00") + sell.components["exchange"]
    if ipft_pct is None:
        assert "ipft" not in sell.components
    else:
        assert sell.components["ipft"] == q(turnover * Decimal(ipft_pct) / 100)
        taxable += sell.components["ipft"]  # GST on IPFT
    assert sell.components["gst"] == q(taxable * 18 / 100)
    buy = MODEL.leg_cost("BUY", 100.0, 75, on)
    assert buy.components["stt"] == Decimal("0.00") and buy.components["stamp"] == Decimal("0.23")


def test_the_day_before_the_table_starts_is_refused() -> None:
    with pytest.raises(NoRatesForDate):
        MODEL.leg_cost("BUY", 100.0, 75, date(2021, 12, 31))


def test_the_seed_note_mentions_the_decisions() -> None:
    text = json.loads(PATH.read_text())["_note"]
    assert "unverified" in text.lower() and "Rs 20 flat" in text and "contract note" in text
