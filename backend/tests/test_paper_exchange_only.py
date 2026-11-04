"""End-of-day check: an "exchange-only crossing", kept apart from real and fill-driven differences.

The official 1m bar crossed paper's level, but no feed tick reached it, so paper exited later (on a later tick, or
not on that level at all) while the backtest, which reads the bar, exited in that minute. Paper's trade carries the
shadow exit it recorded for that minute (app/paper/shadow.py). The case is slot 4 on 4 Nov 2026: 23900 CE stop,
paper 141.24 (from 176.55), backtest 140.52 (from 175.65); the 10:23 bar's low 140.25 crossed both, paper's lowest
10:23 tick was 141.80, paper sold at 10:25:23.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.paper.check import EXCHANGE_ONLY, FILL_DRIVEN, compare_trades

IST = timezone(timedelta(hours=5, minutes=30))
CONTRACT = "NIFTY 23900 CE 09 NOV 26"


def at(hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(2026, 11, 4, hh, mm, ss, tzinfo=IST).timestamp())


def row(*, entry: float, stop: float, exit_time: int, exit_premium: float, net: float,
        shadow: dict | None = None) -> dict[str, Any]:
    r = {"direction": "LONG", "signal_time": at(9, 15), "fill_time": at(9, 21), "contract": CONTRACT,
         "entry_premium": entry, "premium_stop": stop, "premium_target": round(entry * 1.4, 2),
         "index_stop": None, "index_target": None, "exit_time": exit_time, "exit_premium": exit_premium,
         "exit_reason": "stop", "exit_reason_raw": "premium_stop", "net": net}
    if shadow is not None:
        r["shadow"] = shadow
    return r


SHADOW = {"exit_time": at(10, 23), "exit_premium": 141.24, "exit_reason": "stop", "net": -2425.0}
PAPER = row(entry=176.55, stop=141.24, exit_time=at(10, 25, 23), exit_premium=143.05, net=-2247.71, shadow=SHADOW)
BACKTEST = row(entry=175.65, stop=140.52, exit_time=at(10, 23), exit_premium=140.32, net=-2366.3)
BARS = {at(10, 23): (161.0, 161.0, 140.25, 144.7), at(10, 25): (146.25, 149.85, 137.4, 142.4)}


def check(live: list, backtest: list) -> list[dict[str, Any]]:
    signals = [{"time": at(9, 15), "side": "BUY", "decided_at_ms": at(9, 21) * 1000, "status": "filled"}]

    def bar_at(contract: str, minute: int) -> tuple | None:
        return BARS.get(minute) if contract == CONTRACT else None

    return compare_trades(live, signals, backtest, live_bars={}, bt_bars={}, incomplete=set(), step=300,
                          basis="fill", option_bar=bar_at)


def test_slot_4_on_4_nov_is_an_exchange_only_crossing() -> None:
    (d,) = check([PAPER], [BACKTEST])
    assert d["kind"] == EXCHANGE_ONLY == "exchange-only crossing"
    assert d["fields"] == ["exit_time"]
    assert d["shadow"] == SHADOW
    assert d["crossing_bar"] == {"time": "10:23", "of": CONTRACT, "open": 161.0, "high": 161.0, "low": 140.25,
                                 "close": 144.7}
    assert "10:23" in d["reason"] and "10:25" in d["reason"] and "141.24" in d["reason"]


def test_without_papers_shadow_it_stays_a_real_difference() -> None:
    paper = {k: v for k, v in PAPER.items() if k != "shadow"}
    (d,) = check([paper], [BACKTEST])
    assert d["kind"] == "difference"


def test_a_shadow_in_another_minute_stays_a_real_difference() -> None:
    paper = {**PAPER, "shadow": {**SHADOW, "exit_time": at(10, 24)}}
    (d,) = check([paper], [BACKTEST])
    assert d["kind"] == "difference"


def test_a_shadow_with_another_reason_stays_a_real_difference() -> None:
    paper = {**PAPER, "shadow": {**SHADOW, "exit_reason": "target"}}
    (d,) = check([paper], [BACKTEST])
    assert d["kind"] == "difference" and d["kind"] != FILL_DRIVEN


def test_end_of_day_check_lists_it_apart() -> None:
    from app.paper import check as chk

    diffs = check([PAPER], [BACKTEST])
    split = chk.split_kinds(diffs)
    assert split == {"differences": [], "fill_driven": [], "exchange_only": diffs}
