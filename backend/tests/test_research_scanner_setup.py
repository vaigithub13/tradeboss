"""Scanner setups on the Phase C path: plan rules, one engine fill, history slice."""

from __future__ import annotations

import inspect
from datetime import date, timedelta

from app.backtest.costs import Slippage
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.instruments import cost_model_for
from app.backtest.risk_wrap import WithRiskSizing
from app.backtest.sizing import RiskSizing
from app.backtest.sources import ListSource
from app.research.scanner_setups import (
    ScannerSetupStrategy,
    collect_history,
    history_counts,
    history_settings,
)
from app.scanner.history import occurrences
from app.scanner.models import Hit
from app.scanner.setups.gap import GAP_MIN, find_gaps
from app.scanner.setups.orb15 import VOLUME_MULT as ORB_VOL
from app.scanner.setups.orb15 import find_orb
from app.scanner.setups.vwap_cross import VOLUME_MULT as VWAP_VOL
from app.scanner.setups.vwap_cross import find_vwap_crosses
from tests.scanner_bars import bar, clock_minutes, flat_session


class _Pos:
    def __init__(self, side: int = 0) -> None:
        self.side = side


class _Ctx:
    def __init__(self) -> None:
        self.position = _Pos()
        self.symbol = "TEST"
        self.open_orders: list[dict] = []


def test_setup_thresholds_keep_the_scanner_defaults() -> None:
    assert inspect.signature(find_orb).parameters["volume_mult"].default == 1.5
    assert inspect.signature(find_orb).parameters["min_width"].default == 0.003
    assert inspect.signature(find_orb).parameters["max_width"].default == 0.02
    assert ORB_VOL == 1.5
    assert inspect.signature(find_vwap_crosses).parameters["volume_mult"].default == 1.2
    assert VWAP_VOL == 1.2
    assert inspect.signature(find_vwap_crosses).parameters["hold_time"].default is None
    assert inspect.signature(find_gaps).parameters["gap_min"].default == 0.01
    assert GAP_MIN == 0.01


def test_wrong_side_tight_stop_and_duplicate_plan_do_not_emit() -> None:
    day = date(2024, 1, 8)
    strategy = ScannerSetupStrategy("vwap_cross")
    ctx = _Ctx()
    good = Hit(setup="vwap_cross", side="long", time=bar(day, 10, 0, 100, 101, 99, 100)["time"], close=100, stop=99)
    wrong = Hit(setup="vwap_cross", side="short", time=bar(day, 10, 5, 100, 101, 99, 100)["time"], close=100, stop=99)
    tight = Hit(setup="vwap_cross", side="long", time=bar(day, 10, 10, 100, 101, 99, 100)["time"], close=100, stop=99.6)
    again = Hit(setup="vwap_cross", side="long", time=bar(day, 10, 15, 100, 101, 99, 100)["time"], close=100, stop=99)
    queued = [[good], [wrong], [tight], [again]]

    def hits(window, *, as_of, prev_close, hold_time):  # noqa: ARG001
        return queued.pop(0)

    strategy._hits = hits  # type: ignore[method-assign]
    first = strategy.on_bar(bar(day, 10, 0, 100, 101, 99, 100, 1000), ctx)
    assert len(first) == 1
    assert first[0].side == "BUY"
    assert first[0].stop == 99
    assert first[0].stop < 100
    assert strategy.on_bar(bar(day, 10, 5, 100, 101, 99, 100, 1000), ctx) == []
    assert strategy.on_bar(bar(day, 10, 10, 100, 101, 99, 100, 1000), ctx) == []
    assert strategy.on_bar(bar(day, 10, 15, 100, 101, 99, 100, 1000), ctx) == []


def test_history_slice_matches_the_scanner_occurrences() -> None:
    start = date(2024, 1, 8)
    bars = []
    for i in range(4):
        bars.extend(flat_session(start + timedelta(days=i), price=100 + i))
    collected = collect_history(bars, "orb15")
    up_to = start + timedelta(days=4)
    fires, rate = history_counts(collected, up_to)
    own = occurrences(bars, setup="orb15", up_to=up_to, settings=history_settings("orb15"))
    assert fires == own.fires
    assert rate == own.win_rate


def test_history_counts_use_the_last_59_sessions_before_the_day() -> None:
    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(70)]
    fires = [{"day": days[i], "reason": "target" if i % 2 == 0 else "stop"} for i in range(70)]
    collected = {"days": days, "fires": fires}
    up_to = date.fromisoformat(days[-1]) + timedelta(days=1)
    count, rate = history_counts(collected, up_to, limit=59)
    assert count == 59
    # the kept days are the last 59, which start at index 11
    kept = fires[-59:]
    wins = sum(1 for row in kept if row["reason"] == "target")
    assert rate == wins / 59
    earlier, _ = history_counts(collected, date.fromisoformat(days[10]))
    assert earlier == 10


def _one_minute(five: dict) -> list[dict]:
    vol = float(five["volume"]) / 5.0
    out = []
    for i in range(5):
        out.append(
            {
                "time": int(five["time"]) + 60 * i,
                "open": five["open"],
                "high": five["high"],
                "low": five["low"],
                "close": five["close"],
                "volume": vol,
                "oi": None,
            }
        )
    return out


def test_orb_break_is_sized_and_filled_under_the_phase_c_cap() -> None:
    day1 = date(2024, 1, 8)
    day2 = date(2024, 1, 9)
    bars: list[dict] = []
    for h, m in clock_minutes():
        bars.extend(_one_minute(dict(bar(day1, h, m, 100, 100.05, 99.95, 100, 1000))))
    for i, (h, m) in enumerate(clock_minutes()):
        if i < 3:
            five = bar(day2, h, m, 100.2, 101.0, 100.0, 100.4, 1000)
        elif i == 3:
            five = bar(day2, h, m, 101.2, 102.4, 101.1, 102.0, 4000)
        else:
            five = bar(day2, h, m, 102.0, 102.2, 101.8, 102.0, 1000)
        bars.extend(_one_minute(five))
    strategy = WithRiskSizing(
        ScannerSetupStrategy("orb15"),
        RiskSizing(risk_rupees=300, notional_cap=75_000, lot_size=1),
    )
    cfg = BacktestConfig(
        timeframe="5m",
        start="2024-01-09",
        end="2024-01-09",
        session_types=("normal",),
        live_timing=True,
        square_off="15:15",
        warmup_bars=80,
        lot_size=1,
        slippage=Slippage.points(0.05),
        cost_model=cost_model_for("equity_intraday"),
    )
    result = run_backtest(strategy, ListSource(bars, 1, symbol="TEST"), cfg)
    assert result.trades, result.warnings
    trade = result.trades[0]
    assert trade.direction == "LONG"
    assert trade.stop_level is not None and trade.stop_level < trade.entry_price
    assert trade.entry_tag.startswith("orb15:")
    assert strategy.sizing_stats()["trades_sized"] >= 1
