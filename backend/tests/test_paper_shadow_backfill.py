"""Shadow exits for day files written before paper recorded them: read from the day's minute log.

`data/feed-recordings/<day>.minutes.jsonl` holds, per contract and minute, the tick-built bar (the traded prices the
feed sent) and the I1 bar. The same rule as live (app/paper/shadow.py) runs on each minute a trade held. The case
is slot 4 on 4 Nov 2026 (rows as logged that day).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from app.backtest.costs import load_default_cost_table
from scripts.paper_shadow_backfill import fill_shadows, minute_bars

IST = timezone(timedelta(hours=5, minutes=30))
KEY = "NSE_FO|39956"


def at(hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(2026, 11, 4, hh, mm, ss, tzinfo=IST).timestamp())


def log_row(hh: int, mm: int, tick: tuple, i1: tuple | None, phase: str = "close") -> dict:
    o, h, lo, c = tick
    return {"phase": phase, "key": KEY, "minute": at(hh, mm) // 60,
            "tick": {"open": o, "high": h, "low": lo, "close": c},
            "i1": None if i1 is None else dict(zip(("open", "high", "low", "close"), i1))}


ROWS = [
    log_row(10, 22, (160.15, 161.1, 155.2, 160.2), (160.15, 161.35, 155.2, 160.95)),
    log_row(10, 23, (160.65, 160.65, 141.8, 144.9), (161.0, 161.0, 140.25, 144.7)),
    log_row(10, 24, (144.85, 150.85, 143.65, 146.8), (145.1, 151.85, 143.2, 146.65)),
    log_row(10, 25, (146.65, 149.6, 138.3, 142.1), (146.25, 149.85, 137.4, 142.4)),
    # a later phase of the same minute (the reconcile) is not what paper saw live
    log_row(10, 23, (160.65, 160.65, 141.8, 144.9), (161.0, 161.0, 120.0, 144.7), phase="reconciled"),
]


def trade() -> dict:
    return {"direction": "LONG", "symbol": "NIFTY 23900 CE 09 NOV 26", "key": KEY, "kind": "CE", "strike": 23900.0,
            "expiry": "2026-11-09", "lots": 1, "units": 65, "lot_size": 65, "entry_at_ms": at(9, 21) * 1000 + 40,
            "exit_at_ms": at(10, 25, 23) * 1000 + 488, "entry_price": 176.55, "exit_price": 143.05,
            "entry_source": "quote", "exit_source": "quote", "entry_slippage": 0.1, "exit_slippage": 0.125,
            "gross": -2177.5, "charges_entry": 29.4, "charges_exit": 40.81, "charges": 70.21, "net": -2247.71,
            "win": False, "entry_reason": "x", "exit_reason": "premium_stop", "signal_time": at(9, 15),
            "trigger_index": 23882.4, "index_entry": 23886.85, "delta": 0.5, "index_exit": 23835.45,
            "source": "live", "index_entry_time": at(9, 21), "index_exit_time": at(10, 25, 23),
            "levels": {"premium_stop": 141.24, "premium_target": 247.17, "index_stop": None, "index_target": None}}


def saved() -> dict:
    t = trade()
    return {"day": "2026-11-04", "source": "live", "exit_rule": {"kind": "premium", "stop": 0.2, "target": 0.4},
            "trades": [t], "report": [], "summary": {"net": t["net"], "trades": 1}}


def write_log(tmp_path) -> dict:
    path = tmp_path / "2026-11-04.minutes.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    return minute_bars(path)


def test_slot_4_on_4_nov_gets_the_10_23_shadow(tmp_path) -> None:
    bars = write_log(tmp_path)
    assert bars[(KEY, at(10, 23))]["i1"]["low"] == 140.25 and bars[(KEY, at(10, 23))]["tick_low"] == 141.8
    s = saved()
    assert fill_shadows(s, bars, load_default_cost_table(), date(2026, 11, 4)) == 1
    sh = s["trades"][0]["shadow"]
    assert (sh["time"], sh["level"], sh["price"], sh["tick_low"]) == (at(10, 23), "premium_stop", 141.24, 141.8)
    (row,) = s["report"]
    assert row["shadow"]["exit_time"] == at(10, 23) and row["shadow_net"] == sh["net"]
    assert s["summary"]["shadow_net"] == sh["net"]
    assert s["summary"]["shadow_diff"] == round(sh["net"] - (-2247.71), 2)


def test_a_second_run_adds_nothing(tmp_path) -> None:
    bars = write_log(tmp_path)
    s = saved()
    fill_shadows(s, bars, load_default_cost_table(), date(2026, 11, 4))
    first = json.dumps(s, sort_keys=True)
    assert fill_shadows(s, bars, load_default_cost_table(), date(2026, 11, 4)) == 0
    assert json.dumps(s, sort_keys=True) == first


def test_a_file_without_a_premium_rule_gets_the_summary_fields_only(tmp_path) -> None:
    bars = write_log(tmp_path)
    s = {**saved(), "exit_rule": None}
    s["trades"][0]["levels"] = None
    assert fill_shadows(s, bars, load_default_cost_table(), date(2026, 11, 4)) == 0
    assert s["trades"][0].get("shadow") is None
    assert s["summary"]["shadow_net"] == -2247.71 and s["summary"]["shadow_diff"] == 0.0


def test_recompute_reprices_a_shadow_already_written(tmp_path) -> None:
    """A rule change (4 Nov: a gapped stop at the bar's open, no longer its low) reprices the saved shadows."""
    bars = write_log(tmp_path)
    s = saved()
    s["trades"][0]["shadow"] = {"time": at(10, 23), "level": "premium_stop", "reason": "stop", "price": 1.0,
                                "net": -99999.0}
    assert fill_shadows(s, bars, load_default_cost_table(), date(2026, 11, 4)) == 0  # kept without recompute
    assert s["trades"][0]["shadow"]["price"] == 1.0
    assert fill_shadows(s, bars, load_default_cost_table(), date(2026, 11, 4), recompute=True) == 1
    assert s["trades"][0]["shadow"]["price"] == 141.24 and s["summary"]["shadow_net"] == s["trades"][0]["shadow"]["net"]
