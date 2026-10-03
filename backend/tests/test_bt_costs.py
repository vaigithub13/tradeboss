"""Cost model (7). The rates below are SYNTHETIC test numbers, not Upstox's.

The real table is seeded only from the contract note (see test_7d, waiting for the note)."""

from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal

import pytest

from app.backtest.costs import (
    CostConfigError,
    CostModel,
    CostTable,
    NoRatesForDate,
    Slippage,
    UnknownRate,
    get_cost_model,
    load_default_cost_table,
)
from tests.bt_helpers import MON, Scripted, buy, day_bars, exit_, run

RATES = {
    "brokerage_flat": "20",
    "stt_sell_pct": "0.1",
    "exchange_pct": "0.03503",
    "sebi_per_crore": "10",
    "stamp_buy_pct": "0.003",
    "gst_pct": "18",
}
ROW = {"effective_from": "2020-01-01", "rates": RATES}
TABLE = CostTable.from_dict({"rows": [ROW]})
D = date(2026, 1, 5)


def model(table: CostTable = TABLE) -> CostModel:
    return CostModel("options", table)


def test_7b_buy_leg_has_no_stt_and_pays_stamp_duty() -> None:
    leg = model().leg_cost("BUY", 100.0, 75, D)  # turnover 7500
    assert leg.components == {
        "brokerage": Decimal("20.00"),
        "stt": Decimal("0.00"),
        "exchange": Decimal("2.63"),  # 7500 * 0.0003503 = 2.62725
        "sebi": Decimal("0.01"),  # 7500 * 10 / 1e7 = 0.0075
        "stamp": Decimal("0.23"),  # 0.225 -> half up
        "gst": Decimal("4.08"),  # 18% of (20 + 2.63 + 0.01) = 4.0752
    }
    assert leg.total == Decimal("26.95")


def test_7b_sell_leg_has_stt_and_no_stamp_duty() -> None:
    leg = model().leg_cost("SELL", 120.0, 75, D)  # turnover 9000
    assert leg.components == {
        "brokerage": Decimal("20.00"),
        "stt": Decimal("9.00"),
        "exchange": Decimal("3.15"),  # 3.1527
        "sebi": Decimal("0.01"),
        "stamp": Decimal("0.00"),
        "gst": Decimal("4.17"),  # 18% of 23.16 = 4.1688
    }
    assert leg.total == Decimal("36.33")


def test_7c_rounding_is_half_up_per_component_and_configurable_per_component() -> None:
    assert model().leg_cost("SELL", 100.0, 95, D).components["stt"] == Decimal("9.50")
    rupee = CostTable.from_dict({"rows": [{**ROW, "rounding": {"stt": "1"}}]})
    assert model(rupee).leg_cost("SELL", 100.0, 95, D).components["stt"] == Decimal("10")  # 9.5 -> 10
    with pytest.raises(CostConfigError):
        CostTable.from_dict({"rows": [{**ROW, "rounding": {"nonsense": "1"}}]})


def test_7e_brokerage_flat_or_percent_whichever_is_lower() -> None:
    both = CostTable.from_dict({"rows": [{**ROW, "rates": {**RATES, "brokerage_pct": "0.1"}}]})
    assert model(both).leg_cost("BUY", 100.0, 75, D).components["brokerage"] == Decimal("7.50")  # 0.1% of 7500
    assert model(both).leg_cost("BUY", 1000.0, 100, D).components["brokerage"] == Decimal("20.00")  # 0.1% of 100000 = 100


def test_7a_rates_are_dated_inclusive_and_never_fall_back_to_today() -> None:
    old = {"effective_from": "2024-10-01", "rates": {**RATES, "stt_sell_pct": "0.0625"}}
    new = {"effective_from": "2026-04-01", "rates": {**RATES, "stt_sell_pct": "0.1"}}
    t = CostTable.from_dict({"rows": [new, old]})  # order in the file does not matter
    m = model(t)
    assert m.leg_cost("SELL", 120.0, 75, date(2025, 1, 1)).components["stt"] == Decimal("5.63")  # 5.625 half up
    assert m.leg_cost("SELL", 120.0, 75, date(2026, 3, 31)).components["stt"] == Decimal("5.63")
    assert m.leg_cost("SELL", 120.0, 75, date(2026, 4, 1)).components["stt"] == Decimal("9.00")  # inclusive
    with pytest.raises(NoRatesForDate):
        m.leg_cost("SELL", 120.0, 75, date(2024, 9, 30))


def test_7a_a_rate_that_is_not_known_stays_unknown_instead_of_being_guessed() -> None:
    partial = {k: v for k, v in RATES.items() if k != "stt_sell_pct"}
    t = CostTable.from_dict({"rows": [{"effective_from": "2020-01-01", "rates": partial}]})
    assert model(t).leg_cost("BUY", 100.0, 75, D).total == Decimal("26.95")  # a buy leg never needs STT
    with pytest.raises(UnknownRate, match="stt_sell_pct"):
        model(t).leg_cost("SELL", 100.0, 75, D)


def test_7a_table_is_validated() -> None:
    bad = copy.deepcopy(ROW)
    bad["rates"]["gst_pct"] = "-1"  # type: ignore[index]
    with pytest.raises(CostConfigError):
        CostTable.from_dict({"rows": [bad]})
    with pytest.raises(CostConfigError):
        CostTable.from_dict({"rows": [{**ROW, "rates": {**RATES, "made_up_fee": "1"}}]})
    with pytest.raises(CostConfigError):
        CostTable.from_dict({"rows": [ROW, ROW]})  # duplicate effective_from
    with pytest.raises(CostConfigError):
        CostTable.from_dict({"rows": [{"effective_from": "01-01-2020", "rates": RATES}]})


def test_7a_extra_note_lines_can_be_added_without_code_changes() -> None:
    extra = {"name": "ipft", "pct": "0.0001", "sides": ["BUY", "SELL"]}
    t = CostTable.from_dict({"rows": [{**ROW, "rates": {**RATES, "extra": [extra]}}]})
    leg = model(t).leg_cost("BUY", 100.0, 75, D)
    assert leg.components["ipft"] == Decimal("0.01") and leg.total == Decimal("26.96")  # 7500 * 0.000001 = 0.0075


def test_7a_table_loads_from_an_editable_json_file(tmp_path) -> None:  # noqa: ANN001
    p = tmp_path / "rates.json"
    p.write_text(json.dumps({"rows": [ROW]}))
    assert CostTable.load(p).leg_cost("BUY", 100.0, 75, D) == TABLE.leg_cost("BUY", 100.0, 75, D)


def test_7f_zero_preset_charges_nothing_and_unknown_presets_are_refused() -> None:
    assert get_cost_model("zero").leg_cost("SELL", 120.0, 75, D).total == Decimal("0.00")
    assert get_cost_model("options", table=TABLE).name == "options"
    with pytest.raises(ValueError):
        get_cost_model("equity")


def test_the_shipped_table_exists_parses_and_refuses_dates_it_has_no_note_for() -> None:
    shipped = load_default_cost_table()
    for row in shipped.rows:  # whatever has been seeded must be complete or explicitly unknown
        assert isinstance(row.effective_from, date)
    with pytest.raises((NoRatesForDate, UnknownRate)):
        CostModel("options", shipped).leg_cost("SELL", 100.0, 75, date(1999, 1, 1))


def test_7f_slippage_is_validated_and_adverse() -> None:
    assert Slippage.points(0.5).apply("BUY", 100.0) == 100.5 and Slippage.points(0.5).apply("SELL", 100.0) == 99.5
    assert Slippage.pct(0.1).apply("BUY", 100.0) == pytest.approx(100.1)
    assert Slippage.none().apply("SELL", 100.0) == 100.0
    with pytest.raises(ValueError):
        Slippage.points(-1)


def test_7g_a_trade_carries_its_charges_and_net_pnl_is_after_costs() -> None:
    rows = [(100, 100, 100, 100)] * 2 + [(110, 110, 110, 110), (120, 120, 120, 120)]
    res = run(Scripted({0: [buy()], 2: [exit_()]}), day_bars(MON, rows), lot_size=75, cost_model=model())
    (t,) = res.trades
    assert (t.entry_price, t.exit_price, t.units) == (100.0, 120.0, 75)
    assert t.gross_pnl == 1500.0
    assert t.charges == {"brokerage": 40.0, "stt": 9.0, "exchange": 5.78, "sebi": 0.02, "stamp": 0.23, "gst": 8.25}
    assert t.charges_total == 63.28 and t.net_pnl == 1436.72
    assert res.metrics["net_pnl"] == 1436.72 and res.metrics["total_charges"] == 63.28 and res.metrics["gross_pnl"] == 1500.0


# ---------------------------------------------------------------- the shipped 2026-10-01 row (UNVERIFIED seed)
SEED_DAY = date(2026, 10, 1)


def test_the_shipped_row_is_a_published_rate_seed_and_every_rate_is_marked_unverified() -> None:
    row = load_default_cost_table().row_for(SEED_DAY)
    assert row.effective_from == date(2026, 4, 1)  # the row in force on 2026-10-01
    assert {k: str(v) for k, v in row.rates.items()} == {
        "brokerage_flat": "20", "brokerage_pct": "None", "stt_sell_pct": "0.15", "exchange_pct": "0.03553",
        "sebi_per_crore": "10", "stamp_buy_pct": "0.003", "gst_pct": "18",
    }
    assert row.gst_on == ("brokerage", "exchange")  # Upstox's page: GST on brokerage + transaction charges + IPFT
    assert sorted(row.unverified()) == sorted(
        ["brokerage_flat", "stt_sell_pct", "exchange_pct", "sebi_per_crore", "stamp_buy_pct", "gst_pct", "gst_on"])
    assert all(v == "unverified" for v in row.verification.values())
    assert set(row.sources) >= set(row.verification)  # every rate says where it came from
    assert all("http" in v for v in row.sources.values())


def test_the_shipped_row_charges_as_the_published_rates_say_NOT_a_contract_note() -> None:
    m = CostModel("options", load_default_cost_table())
    buy_leg = m.leg_cost("BUY", 100.0, 75, SEED_DAY)  # turnover 7,500
    assert buy_leg.components == {
        "brokerage": Decimal("20.00"), "stt": Decimal("0.00"), "exchange": Decimal("2.66"),  # 2.66475
        "sebi": Decimal("0.01"), "stamp": Decimal("0.23"),  # 0.225 half up
        "gst": Decimal("4.08"),  # 18% of (20 + 2.66): the SEBI fee is NOT in the base
    }
    assert buy_leg.total == Decimal("26.98")
    sell_leg = m.leg_cost("SELL", 120.0, 75, SEED_DAY)  # turnover 9,000
    assert sell_leg.components == {
        "brokerage": Decimal("20.00"), "stt": Decimal("13.50"), "exchange": Decimal("3.20"),  # 3.1977
        "sebi": Decimal("0.01"), "stamp": Decimal("0.00"), "gst": Decimal("4.18"),  # 18% of 23.20 = 4.176
    }
    assert sell_leg.total == Decimal("40.89")
    with pytest.raises(NoRatesForDate):
        m.leg_cost("SELL", 120.0, 75, date(2021, 12, 31))


def test_a_run_that_pays_unverified_rates_says_so_in_its_warnings() -> None:
    rows = [(100, 100, 100, 100)] * 2 + [(110, 110, 110, 110)] * 2
    res = run(Scripted({0: [buy()], 2: [exit_()]}), day_bars((2026, 10, 1), rows), lot_size=75,
              cost_model=CostModel("options", load_default_cost_table()))
    (w,) = [w for w in res.warnings if "UNVERIFIED" in w]
    assert "2026-04-01" in w and "stt_sell_pct" in w and "contract note" in w  # the row in force on 2026-10-01
    assert res.trades[0].charges_total == 66.43  # buy leg 26.98 + sell leg 39.45 (turnover 8,250: STT 12.38)
    zero = run(Scripted({0: [buy()], 2: [exit_()]}), day_bars((2026, 10, 1), rows), lot_size=75)
    assert not any("UNVERIFIED" in w for w in zero.warnings)


def test_a_row_can_mark_rates_verified_and_the_warning_names_only_what_is_left() -> None:
    verified = {k: "verified" for k in RATES} | {"gst_on": "verified"}
    t = CostTable.from_dict({"rows": [{**ROW, "verification": verified}]})
    assert t.row_for(D).unverified() == []
    assert CostModel("options", t).unverified_warnings({D}) == []
    part = CostTable.from_dict({"rows": [{**ROW, "verification": {**verified, "stt_sell_pct": "unverified"}}]})
    (msg,) = CostModel("options", part).unverified_warnings({D})
    assert "stt_sell_pct" in msg and "brokerage_flat" not in msg


def test_rows_without_a_verification_entry_count_as_unverified_and_bad_entries_are_refused() -> None:
    assert "stt_sell_pct" in CostTable.from_dict({"rows": [ROW]}).row_for(D).unverified()
    for bad in (
        {"verification": {"stt_sell_pct": "probably"}},
        {"verification": {"made_up": "verified"}},
        {"sources": {"made_up": "x"}},
        {"gst_on": ["brokerage", "nonsense"]},
    ):
        with pytest.raises(CostConfigError):
            CostTable.from_dict({"rows": [{**ROW, **bad}]})


def test_gst_base_is_configurable_per_row() -> None:
    no_sebi = CostTable.from_dict({"rows": [{**ROW, "gst_on": ["brokerage", "exchange"]}]})
    assert model(no_sebi).leg_cost("BUY", 100.0, 75, D).components["gst"] == Decimal("4.07")  # 18% of 22.63 = 4.0734
    assert model().leg_cost("BUY", 100.0, 75, D).components["gst"] == Decimal("4.08")  # default base includes SEBI


@pytest.mark.skip(reason="no contract note yet (trade date 2026-10-01): the 2026-10-01 row is an UNVERIFIED seed from published rate cards; this test is un-skipped when a note is provided")
def test_7d_contract_note_matches_to_the_paisa() -> None:
    raise AssertionError("to be written from the contract note: every charge line, to the paisa")
