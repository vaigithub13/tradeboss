"""End-of-day check: exit-minute differences that come from fill-dependent stop and target levels.

A premium (or index) stop or target is set from each side's own entry fill, so paper's real fill and the backtest's
model fill give two different levels. When the bar at the earlier exit crossed only the earlier side's level, the
exit minutes differ for that reason alone: a "fill-driven level difference", listed apart from the real differences
with both levels and the bar that crossed between them. The case is slot 3 on 12 Oct 2026 (Log XZ, 1:2 premium).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.paper.check import FILL_DRIVEN, compare_trades

IST = timezone(timedelta(hours=5, minutes=30))
CONTRACT = "NIFTY 22500 PE 13 OCT 26"


def at(hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(2026, 10, 12, hh, mm, ss, tzinfo=IST).timestamp())


def row(*, entry: float, stop: float, target: float, exit_time: int, exit_premium: float,
        reason: str = "premium_target", contract: str = CONTRACT) -> dict[str, Any]:
    return {"direction": "SHORT", "signal_time": at(9, 45), "fill_time": at(9, 51), "contract": contract,
            "entry_premium": entry, "premium_stop": stop, "premium_target": target,
            "index_stop": None, "index_target": None, "exit_time": exit_time, "exit_premium": exit_premium,
            "exit_reason": "target" if reason.endswith("target") else "stop", "exit_reason_raw": reason,
            "net": 1.0}


PAPER = row(entry=88.75, stop=71.0, target=124.25, exit_time=at(10, 51, 33), exit_premium=123.2)
BACKTEST = row(entry=88.4, stop=70.72, target=123.76, exit_time=at(10, 50), exit_premium=123.56)
# the stored 1m bars of NIFTY 22500 PE 13 OCT 26 on 12 Oct
BARS = {at(10, 50): (113.35, 123.85, 113.0, 120.6), at(10, 51): (120.6, 126.75, 120.05, 123.95)}


def option_bars(bars: dict[int, tuple]) -> Any:
    def bar_at(contract: str, minute: int) -> tuple | None:
        return bars.get(minute) if contract == CONTRACT else None
    return bar_at


def check(live: list, backtest: list, bars: dict[int, tuple] = BARS) -> list[dict[str, Any]]:
    signals = [{"time": at(9, 45), "side": "SELL", "decided_at_ms": at(9, 51) * 1000, "status": "filled"}]
    return compare_trades(live, signals, backtest, live_bars={}, bt_bars={}, incomplete=set(), step=300,
                          basis="decision", option_bar=option_bars(bars))


def test_slot_3_on_12_oct_is_a_fill_driven_level_difference() -> None:
    diffs = check([PAPER], [BACKTEST])
    assert len(diffs) == 1
    d = diffs[0]
    assert d["kind"] == FILL_DRIVEN == "fill-driven level difference"
    assert d["fields"] == ["exit_time"]
    assert d["level"] == {"name": "premium_target", "paper": 124.25, "backtest": 123.76}
    assert d["crossing_bar"] == {"time": "10:50", "of": CONTRACT, "open": 113.35, "high": 123.85, "low": 113.0,
                                 "close": 120.6, "crossed": "backtest"}
    assert "124.25" in d["reason"] and "123.76" in d["reason"] and "10:50 high 123.85" in d["reason"]


def test_a_bar_that_crossed_both_levels_stays_a_real_difference() -> None:
    bars = {**BARS, at(10, 50): (113.35, 125.0, 113.0, 120.6)}  # paper should have exited at 10:50 too
    (d,) = check([PAPER], [BACKTEST], bars)
    assert d["kind"] == "difference" and "level" not in d


def test_no_stored_bar_stays_a_real_difference() -> None:
    (d,) = check([PAPER], [BACKTEST], {})
    assert d["kind"] == "difference"


def test_a_different_exit_reason_stays_a_real_difference() -> None:
    bt = row(entry=88.4, stop=70.72, target=123.76, exit_time=at(10, 50), exit_premium=70.5, reason="premium_stop")
    (d,) = check([PAPER], [bt])
    assert d["kind"] == "difference" and d["fields"] == ["exit_time", "exit_reason"]


def test_the_same_levels_are_never_fill_driven() -> None:
    bt = row(entry=88.75, stop=71.0, target=124.25, exit_time=at(10, 50), exit_premium=124.25)
    (d,) = check([PAPER], [bt])
    assert d["kind"] == "difference"


def test_a_paper_stop_hit_a_minute_earlier_by_its_higher_stop() -> None:
    """The other way round: paper's stop (set from a higher real fill) is crossed first."""
    live = row(entry=88.75, stop=71.0, target=124.25, exit_time=at(10, 5, 12), exit_premium=70.9,
               reason="premium_stop")
    bt = row(entry=88.4, stop=70.72, target=123.76, exit_time=at(10, 6), exit_premium=70.6, reason="premium_stop")
    (d,) = check([live], [bt], {at(10, 5): (72.0, 72.5, 70.85, 71.2)})
    assert d["kind"] == FILL_DRIVEN
    assert d["level"] == {"name": "premium_stop", "paper": 71.0, "backtest": 70.72}
    assert d["crossing_bar"]["crossed"] == "paper" and d["crossing_bar"]["low"] == 70.85


# ------------------------------------------------- the real day: slot 3's saved 12 Oct file, rechecked
REPO = Path(__file__).resolve().parents[2]
DAY_FILE = REPO / "data" / "paper" / "slot3" / "2026-10-12.json"
OPTIONS = REPO / "data" / "option_history" / "NIFTY_2026-10-13.parquet"
CANDLES = REPO / "data" / "candles" / "NIFTY50" / "1m.parquet"
MISSING = next((str(p) for p in (DAY_FILE, OPTIONS, CANDLES) if not p.exists()), None)


@pytest.mark.skipif(MISSING is not None, reason=f"no {MISSING} in data/")
def test_the_saved_slot_3_day_has_no_real_differences_and_one_fill_driven() -> None:
    from app.data.store import CandleStore
    from app.options.history import OptionHistoryStore
    from app.paper.check import end_of_day_check

    saved = json.loads(DAY_FILE.read_text())
    report = end_of_day_check(
        day=date(2026, 10, 12), signals=saved["signals"], live_report=saved["report"],
        live_bars={int(b["time"]): b for b in saved["bars"]}, incomplete=set(),
        store=CandleStore(REPO / "data" / "candles"), symbol="NIFTY50", strategy=saved["requested_strategy"],
        params=saved["requested_params"], tape=OptionHistoryStore(REPO / "data" / "option_history"),
    )
    assert report.get("error") is None
    assert report["differences"] == []
    (f,) = report["fill_driven"]
    assert (f["time"], f["side"], f["fields"]) == ("09:51", "SELL", ["exit_time"])
    assert f["level"] == {"name": "premium_target", "paper": 124.25, "backtest": 123.76}
    assert (f["crossing_bar"]["time"], f["crossing_bar"]["high"], f["crossing_bar"]["crossed"]) == (
        "10:50", 123.85, "backtest")
