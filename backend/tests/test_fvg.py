"""Fair value gap spec. Written before the indicator. See PROJECT_PLAN.md.

Bullish: low of candle 3 is strictly above high of candle 1.
Bearish: high of candle 3 is strictly below low of candle 1.
The box does not exist until candle 3 has closed. Later bars do not rewrite it.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from app.indicators.fvg import fvg_boxes
from app.indicators.registry import OUTPUTS, PANES, compute, validate_params


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": [1_700_000_000 + i * 60 for i in range(len(rows))],
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": [0.0] * len(rows),
        }
    )


# candle 1 high 100, candle 3 low 105: a 5-point bullish gap. Then a bar that trades 104.
BULL = _frame(
    [
        (98, 100, 97, 99),
        (101, 112, 100, 110),
        (108, 112, 105, 111),
        (106, 108, 104, 105),
    ]
)

# candle 1 low 100, candle 3 high 90: a bearish gap.
BEAR = _frame(
    [
        (102, 104, 100, 101),
        (99, 101, 88, 90),
        (88, 90, 86, 87),
    ]
)


def test_defaults_and_a_bad_mitigation_mode() -> None:
    params = validate_params("fvg", {})
    assert params["min_gap"] == 0
    assert params["min_gap_mode"] == "points"
    assert params["mitigation"] == "touch"
    assert params["when_mitigated"] == "stop"
    assert params["show_last"] == 10
    assert params["timeframe"] == ""
    assert PANES["fvg"] == "price"
    assert OUTPUTS["fvg"] == ["bull_bottom", "bull_top", "bear_bottom", "bear_top"]
    with pytest.raises(ValueError):
        validate_params("fvg", {"mitigation": "wick"})


def test_a_bullish_gap_appears_only_when_candle_3_has_closed() -> None:
    assert fvg_boxes(BULL.iloc[:2], {"show_last": 10}) == []
    boxes = fvg_boxes(BULL.iloc[:3], {"show_last": 10, "mitigation": "touch"})
    assert len(boxes) == 1
    box = boxes[0]
    assert box["direction"] == "bull"
    assert box["start_index"] == 0
    assert box["formed_index"] == 2
    assert box["bottom"] == 100
    assert box["top"] == 105
    assert box["mitigated"] is False
    assert box["extends"] is True
    assert box["end_index"] is None


def test_a_bearish_gap_uses_candle_1_low_and_candle_3_high() -> None:
    box = fvg_boxes(BEAR, {"show_last": 10})[0]
    assert box["direction"] == "bear"
    assert box["bottom"] == 90
    assert box["top"] == 100
    assert box["formed_index"] == 2


def test_no_gap_when_candle_3_does_not_clear_candle_1() -> None:
    flat = _frame(
        [
            (98, 100, 97, 99),
            (100, 103, 99, 102),
            (101, 104, 100, 103),
        ]
    )
    assert fvg_boxes(flat, {"show_last": 10}) == []


def test_minimum_gap_in_points_and_percent() -> None:
    assert fvg_boxes(BULL.iloc[:3], {"show_last": 10, "min_gap": 5, "min_gap_mode": "points"}) != []
    assert fvg_boxes(BULL.iloc[:3], {"show_last": 10, "min_gap": 5.01, "min_gap_mode": "points"}) == []
    # 5 / 111 * 100 is about 4.50 percent of candle 3's close.
    assert fvg_boxes(BULL.iloc[:3], {"show_last": 10, "min_gap": 4.5, "min_gap_mode": "percent"}) != []
    assert fvg_boxes(BULL.iloc[:3], {"show_last": 10, "min_gap": 4.51, "min_gap_mode": "percent"}) == []


def test_touch_half_and_full_mitigation_stop_or_fade() -> None:
    touch = fvg_boxes(BULL, {"show_last": 10, "mitigation": "touch", "when_mitigated": "stop"})[0]
    assert touch["mitigated"] is True
    assert touch["end_index"] == 3
    assert touch["extends"] is False
    assert touch["faded"] is False

    halfway = _frame(
        [
            (98, 100, 97, 99),
            (101, 112, 100, 110),
            (108, 112, 105, 111),
            (104, 106, 102.5, 104),
        ]
    )
    half = fvg_boxes(halfway, {"show_last": 10, "mitigation": "half", "when_mitigated": "fade"})[0]
    assert half["mitigated"] is True  # low 102.5 reaches the midpoint and not the bottom at 100
    assert half["extends"] is True
    assert half["faded"] is True

    still_open = fvg_boxes(BULL, {"show_last": 10, "mitigation": "full"})[0]
    assert still_open["mitigated"] is False  # low 104 never reaches the bottom at 100

    filled = _frame(
        [
            (98, 100, 97, 99),
            (101, 112, 100, 110),
            (108, 112, 105, 111),
            (104, 106, 100, 101),
        ]
    )
    full = fvg_boxes(filled, {"show_last": 10, "mitigation": "full", "when_mitigated": "stop"})[0]
    assert full["mitigated"] is True
    assert full["end_index"] == 3


def test_show_last_keeps_the_newest_gap() -> None:
    two = _frame(
        [
            (98, 100, 97, 99),
            (101, 112, 100, 110),
            (108, 112, 105, 111),
            (110, 112, 109, 111),
            (113, 120, 112, 118),
            (116, 122, 116, 121),
        ]
    )
    boxes = fvg_boxes(two, {"show_last": 1, "mitigation": "touch"})
    assert [box["formed_index"] for box in boxes] == [5]


def test_later_bars_do_not_rewrite_the_gap_or_draw_it_early() -> None:
    params = {"show_last": 10, "mitigation": "touch", "when_mitigated": "stop"}
    early = compute(BULL.iloc[:3], "fvg", validate_params("fvg", params))
    assert math.isnan(early["bull_top"][0])
    assert math.isnan(early["bull_top"][1])
    assert early["bull_top"][2] == 105
    assert early["bull_bottom"][2] == 100

    later = compute(BULL, "fvg", validate_params("fvg", params))
    assert later["bull_top"][2] == 105
    assert later["bull_bottom"][2] == 100
    assert math.isnan(later["bull_top"][0])
    assert math.isnan(later["bull_top"][3])
