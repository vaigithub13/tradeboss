"""compute_indicators = candles (with warm-up history) + registry math, sliced to the visible range."""

from __future__ import annotations

import numpy as np
import pytest

from app.data.service import get_candles
from app.data.store import CandleStore
from app.indicators.frame import candles_to_frame
from app.indicators.registry import IndicatorSpec, compute, validate_params
from app.indicators.service import IndicatorNotAvailable, compute_indicators
from app.indicators.volume import VolumeRequired
from tests.conftest import ist_ts

TUE_OPEN = ist_ts(2024, 10, 29, 9, 15)  # 76th bar of the synthetic store (index 75)


def arr(values: list[float | None]) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def full_history(store: CandleStore, tf: str, spec: IndicatorSpec) -> dict[str, np.ndarray]:
    candles = get_candles(store, "NIFTY50", tf).candles
    return compute(candles_to_frame(candles), spec.type, spec.params)


def spec(id_: str, type_: str, **params: object) -> IndicatorSpec:
    return IndicatorSpec(id=id_, type=type_, params=validate_params(type_, dict(params)))


@pytest.mark.parametrize(
    "s",
    [
        spec("e", "ema", length=3),
        spec("r", "rsi", length=3),
        spec("m", "macd", fast=3, slow=5, signal=2),
        spec("st", "supertrend", atr_length=3, multiplier=2),
        spec("b", "bb", length=4, mult=2),
        spec("v", "vwap"),
    ],
)
def test_visible_range_values_equal_full_history_values(store: CandleStore, s: IndicatorSpec) -> None:
    res = compute_indicators(store, "NIFTY50", "5m", [s], from_time=TUE_OPEN)
    expected = full_history(store, "5m", s)

    assert res.times[0] == TUE_OPEN and len(res.times) == 300 - 75
    out = res.indicators[0]
    assert (out.id, out.type, out.params) == (s.id, s.type, s.params)
    for name, values in out.outputs.items():
        assert len(values) == len(res.times)
        np.testing.assert_allclose(
            arr(values), expected[name][75:], rtol=1e-6, atol=1e-6, equal_nan=True, err_msg=name
        )


def test_times_are_exactly_the_candles_in_the_range(store: CandleStore) -> None:
    lo, hi = TUE_OPEN, ist_ts(2024, 10, 29, 10, 0)
    res = compute_indicators(store, "NIFTY50", "5m", [spec("e", "ema", length=3)],
                             from_time=lo, to_time=hi)  # fmt: skip
    candles = get_candles(store, "NIFTY50", "5m", from_time=lo, to_time=hi).candles
    assert res.times == [c["time"] for c in candles]
    assert len(res.indicators[0].outputs["ema"]) == len(candles) == 10


def test_nan_becomes_none_and_start_of_data_is_not_padded(store: CandleStore) -> None:
    res = compute_indicators(store, "NIFTY50", "5m", [spec("s", "sma", length=3)])
    assert len(res.times) == 300
    values = res.indicators[0].outputs["sma"]
    assert values[0] is None and values[1] is None and values[2] is not None


def test_multiple_copies_of_the_same_indicator(store: CandleStore) -> None:
    res = compute_indicators(
        store, "NIFTY50", "5m",
        [spec("ema-fast", "ema", length=3), spec("ema-slow", "ema", length=9)],
        from_time=TUE_OPEN,
    )  # fmt: skip
    assert [o.id for o in res.indicators] == ["ema-fast", "ema-slow"]
    fast, slow = (o.outputs["ema"] for o in res.indicators)
    assert fast != slow


def test_works_on_resampled_timeframes(store: CandleStore) -> None:
    s = spec("e", "ema", length=3)
    res = compute_indicators(store, "NIFTY50", "1h", [s])
    candles = get_candles(store, "NIFTY50", "1h").candles
    assert res.times == [c["time"] for c in candles]
    np.testing.assert_allclose(
        arr(res.indicators[0].outputs["ema"]), full_history(store, "1h", s)["ema"], rtol=1e-9
    )


def test_vwap_mid_day_start_still_uses_the_whole_day(store: CandleStore) -> None:
    s = spec("v", "vwap")
    mid = ist_ts(2024, 10, 29, 12, 0)
    res = compute_indicators(store, "NIFTY50", "5m", [s], from_time=mid)
    expected = full_history(store, "5m", s)["vwap"]
    first_idx = 75 + 33  # 12:00 is 33 five-minute bars after 09:15
    assert res.times[0] == mid
    assert res.indicators[0].outputs["vwap"][0] == pytest.approx(expected[first_idx])


# --------------------------------------------------------------------------- session filter
ALL_TYPES = ("normal", "weekend_full", "special_short", "muhurat")
MON_NOV4_OPEN = ist_ts(2024, 11, 4, 9, 15)


@pytest.mark.parametrize("tf", ["5m", "1h", "1D"])
@pytest.mark.parametrize("types", [("normal",), ("normal", "weekend_full"), ALL_TYPES])
def test_indicators_are_computed_on_the_same_session_filtered_series_the_chart_shows(
    store: CandleStore, tf: str, types: tuple[str, ...]
) -> None:
    s = spec("e", "ema", length=3)
    r = spec("r", "rsi", length=3)
    res = compute_indicators(store, "NIFTY50", tf, [s, r], session_types=types)
    chart = get_candles(store, "NIFTY50", tf, session_types=types).candles

    assert res.times == [c["time"] for c in chart]  # exactly the chart's bars
    frame = candles_to_frame(chart)  # math on exactly the chart's series
    for out in res.indicators:
        expected = compute(frame, out.type, out.params)
        for name, values in out.outputs.items():
            np.testing.assert_allclose(arr(values), expected[name], rtol=1e-9, equal_nan=True)


def test_excluded_sessions_do_not_leak_into_values(store: CandleStore) -> None:
    """The muhurat (base 1000) and special_short (base 600) bars must not feed the EMA of Mon 11-04
    when those session types are hidden; when shown, they do."""
    s = spec("e", "ema", length=3)
    hidden = compute_indicators(store, "NIFTY50", "5m", [s], from_time=MON_NOV4_OPEN,
                                to_time=MON_NOV4_OPEN, session_types=("normal",))  # fmt: skip
    shown = compute_indicators(store, "NIFTY50", "5m", [s], from_time=MON_NOV4_OPEN,
                               to_time=MON_NOV4_OPEN, session_types=ALL_TYPES)  # fmt: skip
    first_hidden = hidden.indicators[0].outputs["ema"][0]
    first_shown = shown.indicators[0].outputs["ema"][0]
    assert first_hidden is not None and first_shown is not None
    assert first_hidden != pytest.approx(first_shown, abs=1.0)
    # Hidden: the previous bar is Tue 10-29's last bar (close ~275), so the EMA stays near 300.
    # Shown: the previous bar is the muhurat session (~1011), dragging the EMA far above.
    assert first_hidden < 400 < first_shown


def test_default_session_types_match_the_chart_default(store: CandleStore) -> None:
    res = compute_indicators(store, "NIFTY50", "1D", [spec("e", "ema", length=3)])
    chart = get_candles(store, "NIFTY50", "1D").candles  # default = normal + weekend_full
    assert res.times == [c["time"] for c in chart]


# --------------------------------------------------------------------------- refusals
def test_vwap_is_refused_on_zero_volume_data(zero_volume_store: CandleStore) -> None:
    with pytest.raises(VolumeRequired):
        compute_indicators(zero_volume_store, "NIFTY50", "5m", [spec("v", "vwap")])


def test_other_indicators_still_work_on_zero_volume_data(zero_volume_store: CandleStore) -> None:
    res = compute_indicators(zero_volume_store, "NIFTY50", "5m", [spec("e", "ema", length=3)])
    assert res.indicators[0].outputs["ema"][-1] is not None


@pytest.mark.parametrize("tf", ["1D", "1W"])
def test_vwap_is_intraday_only(store: CandleStore, tf: str) -> None:
    with pytest.raises(IndicatorNotAvailable) as exc:
        compute_indicators(store, "NIFTY50", tf, [spec("v", "vwap")])
    assert "intraday" in str(exc.value).lower()


def test_duplicate_ids_are_rejected(store: CandleStore) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        compute_indicators(store, "NIFTY50", "5m", [spec("x", "ema"), spec("x", "sma")])
