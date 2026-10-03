"""Results format, warnings, determinism (cases 23–26). The engine result is not rewritten."""

from __future__ import annotations

from app.backtest.result import canonical
from app.options.model import OptionModelConfig
from tests.opt_helpers import bar, bare_result, estimate, ist, trade


def _run(**kw):  # noqa: ANN001, ANN201
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]
    return estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix, **kw)


def test_23_index_and_estimated_sit_side_by_side_and_the_index_json_is_untouched() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    trades = [trade(entry=(fill, 25010), exit=(ex, 25060))]
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]
    raw = bare_result(trades)
    before = raw.to_json()
    out = estimate(trades, index, vix, run_id=raw.run_id)
    assert out.label == "ESTIMATED" and out.option.label == "ESTIMATED"
    assert all(t["label"] == "ESTIMATED" for t in out.option.trades)
    assert out.index.to_json() == before
    assert raw.to_json() == before
    assert "vix_scale" in out.option.settings
    assert out.option.carry == {"r": 0.0, "q": 0.0, "note": out.option.settings["rates_note"]}


def test_24_each_row_carries_contract_iv_T_premiums_costs_slippage_and_net() -> None:
    row = _run().option.trades[0]
    for key in (
        "contract", "iv", "T", "entry_premium", "exit_premium", "entry_fill", "exit_fill",
        "charges_buy", "charges_sell", "charges_total", "slippage_cost", "net_pnl", "flags",
    ):
        assert key in row
    assert row["net_pnl"] == 1483.79
    assert _run().option.metrics["trades"] == 1
    assert _run().option.metrics["net_pnl"] == 1483.79


def test_25_unverified_costs_and_uncalibrated_model_are_warned() -> None:
    w = _run().option.warnings
    assert any("UNVERIFIED" in x and "cost" in x for x in w)
    assert any("uncalibrated premium model" in x for x in w)
    assert any("r=q=0" in x for x in w)
    calibrated = _run(config=OptionModelConfig(calibration_ref="data/validation/option_model_calibration_2026-10-03.json"))
    assert not any("uncalibrated" in x for x in calibrated.option.warnings)


def test_26_output_is_byte_identical_and_the_run_id_covers_overlay_and_vix() -> None:
    a, b = _run(), _run()
    assert a.to_json() == b.to_json()
    assert a.option.to_json() == b.option.to_json()
    assert a.run_id == b.run_id
    other = _run(config=OptionModelConfig(vix_scale=1.18))
    assert other.run_id != a.run_id
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix2 = [bar(fill, 14.5), bar(ex, 14.5)]
    shifted = estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix2)
    assert shifted.run_id != a.run_id
    assert canonical(a.to_dict()) == a.to_json()
