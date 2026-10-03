"""Walk-forward. The hand example is the approved one. No network, no candle files.

Holdout is frozen in app/backtest/data/holdout.json. A non-positive best train
stays flat through its test window.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.backtest.holdout import HOLDOUT_PATH, load_holdout, resolve_period
from app.backtest.jobs import JobBusy, JobService
from app.backtest.runs import GitState, RunStore
from app.backtest.walkforward import (
    NO_CHOICE_FLAT,
    TRIED,
    build_windows,
    parse_walk_forward,
    pick_params,
    run_holdout,
    run_walk_forward,
)

START = date(2024, 10, 3)
RESEARCH_END = date(2025, 8, 2)
W1 = (date(2024, 10, 3), date(2025, 4, 2))
T1 = (date(2025, 4, 3), date(2025, 6, 2))
W2 = (date(2024, 12, 3), date(2025, 6, 2))
T2 = (date(2025, 6, 3), date(2025, 8, 2))


def _train() -> dict:
    return {
        (W1, "A"): {"net_pnl": 100.0, "max_drawdown": 50.0, "trades": 3, "pnls": [40.0, 40.0, 20.0]},
        (W1, "B"): {"net_pnl": 90.0, "max_drawdown": 30.0, "trades": 3, "pnls": [30.0, 30.0, 30.0]},
        (T1, "B"): {"net_pnl": 20.0, "max_drawdown": 10.0, "trades": 2, "pnls": [30.0, -10.0]},
        (W2, "A"): {"net_pnl": 60.0, "max_drawdown": 20.0, "trades": 4, "pnls": [15.0, 15.0, 15.0, 15.0]},
        (W2, "B"): {"net_pnl": 40.0, "max_drawdown": 40.0, "trades": 4, "pnls": [10.0, 10.0, 10.0, 10.0]},
        (T2, "A"): {"net_pnl": 5.0, "max_drawdown": 0.0, "trades": 1, "pnls": [5.0]},
    }


def _spec(**overrides):
    base = dict(
        strategy="demo",
        symbol="NIFTY50",
        timeframe="5m",
        sessions=("normal", "weekend_full"),
        mode="options",
        strike_offset=0,
        slippage_points=1.0,
        start=START,
        research_end=RESEARCH_END,
        train_months=6,
        test_months=2,
        step_months=2,
        min_trades=2,
        max_combinations=50,
        include_forward=False,
        grid=({"name": "A"}, {"name": "B"}),
    )
    base.update(overrides)
    return base


def _evaluate(table):
    def evaluate(params, start, end):
        key = ((start, end), params["name"])
        if key not in table:
            raise AssertionError(f"unexpected range {start}..{end} for {params}")
        return dict(table[key])

    return evaluate


# ---------------------------------------------------------------- frozen holdout
def test_new_sessions_after_the_holdout_do_not_move_it_or_the_research_end() -> None:
    raw = json.loads(HOLDOUT_PATH.read_text())
    assert raw["start"] == "2026-07-01"
    assert raw["end"] == "2026-10-01"
    early = resolve_period(date(2026, 10, 1))
    later = resolve_period(date(2027, 1, 15))
    assert load_holdout() == (date(2026, 7, 1), date(2026, 10, 1))
    assert early.holdout == later.holdout == (date(2026, 7, 1), date(2026, 10, 1))
    assert early.research_end == later.research_end == date(2026, 6, 30)
    assert early.forward_end is None and later.forward_end is None


def test_include_forward_reaches_past_october_without_moving_the_holdout() -> None:
    period = resolve_period(date(2026, 12, 15), include_forward=True)
    assert period.holdout == (date(2026, 7, 1), date(2026, 10, 1))
    assert period.research_end == date(2026, 6, 30)
    assert period.forward_end == date(2026, 12, 15)


# ---------------------------------------------------------------- W1 hand example
def test_w1_hand_example() -> None:
    windows = build_windows(START, RESEARCH_END, 6, 2, 2)
    assert [(w.train_start, w.train_end, w.test_start, w.test_end) for w in windows] == [
        (*W1, *T1),
        (*W2, *T2),
    ]
    result = run_walk_forward(_spec(), _evaluate(_train()), peeks=0)
    assert [row["params"] for row in result["windows"]] == [{"name": "B"}, {"name": "A"}]
    assert [point["equity"] for point in result["equity"]["option"]] == [30.0, 20.0, 25.0]
    assert result["summary"]["option"]["net_pnl"] == 25.0
    assert result["summary"]["option"]["max_drawdown"] == 10.0
    assert [row["ratio"] for row in result["degradation"]] == pytest.approx([1 / 3, 1 / 3])
    assert result["param_changes"] == 1
    assert TRIED.format(n=4) in result["warnings"]
    assert result["holdout"] == {"start": "2026-07-01", "end": "2026-10-01", "peeks": 0}


# ---------------------------------------------------------------- W2 score gates
def test_w2_minimum_trades_and_deterministic_ties() -> None:
    chosen = pick_params(
        [
            {"params": {"k": 1}, "net_pnl": 10.0, "max_drawdown": 1.0, "trades": 1},
            {"params": {"k": 2}, "net_pnl": 9.0, "max_drawdown": 10.0, "trades": 2},
        ],
        min_trades=2,
    )
    assert chosen["params"] == {"k": 2}
    tied = pick_params(
        [
            {"params": {"x": 2}, "net_pnl": 10.0, "max_drawdown": 5.0, "trades": 3},
            {"params": {"x": 1}, "net_pnl": 10.0, "max_drawdown": 5.0, "trades": 3},
        ],
        min_trades=2,
    )
    assert tied["params"] == {"x": 1}


def test_best_non_positive_train_stays_flat_and_skips_degradation() -> None:
    called: list[tuple] = []

    def evaluate(params, start, end):
        called.append((params["name"], start, end))
        if (start, end) == W1:
            nets = {"A": -5.0, "B": -20.0}
            return {"net_pnl": nets[params["name"]], "max_drawdown": 20.0, "trades": 4, "pnls": [nets[params["name"]]]}
        if (start, end) == W2 and params["name"] == "A":
            return {"net_pnl": 40.0, "max_drawdown": 10.0, "trades": 4, "pnls": [10.0, 10.0, 10.0, 10.0]}
        if (start, end) == W2 and params["name"] == "B":
            return {"net_pnl": 10.0, "max_drawdown": 10.0, "trades": 4, "pnls": [10.0, 0.0, 0.0, 0.0]}
        if (start, end) == T2 and params["name"] == "A":
            return {"net_pnl": 8.0, "max_drawdown": 2.0, "trades": 2, "pnls": [5.0, 3.0]}
        raise AssertionError(f"test window was run: {params} {start} {end}")

    result = run_walk_forward(_spec(), evaluate, peeks=0)
    flat, chosen = result["windows"]
    assert flat["reason"] == NO_CHOICE_FLAT
    assert flat["test"]["net_pnl"] == 0.0
    assert flat["test"]["trades"] == 0
    assert flat["test"]["flat"] is True
    assert all((start, end) != T1 for _name, start, end in called)
    assert [row["window"] for row in result["degradation"]] == [2]
    assert chosen["params"] == {"name": "A"}
    assert result["degradation"][0]["ratio"] == pytest.approx(8 / 2 / (40 / 4))


# ---------------------------------------------------------------- W3 no feedback
def test_w3_test_window_data_cannot_change_the_choice() -> None:
    seen: list[tuple[str, tuple[date, date]]] = []

    def evaluate(params, start, end):
        seen.append((params["name"], (start, end)))
        return _train()[((start, end), params["name"])]

    first = run_walk_forward(_spec(), evaluate, peeks=0)
    # Each window is scored on its train range, then the chosen params are run once on the test range.
    assert [span for _name, span in seen] == [W1, W1, T1, W2, W2, T2]
    assert seen[2] == ("B", T1)
    assert seen[5] == ("A", T2)

    mutated = dict(_train())
    mutated[(T1, "A")] = {"net_pnl": 10_000.0, "max_drawdown": 1.0, "trades": 5, "pnls": [10_000.0]}
    mutated[(T1, "B")] = {"net_pnl": -10_000.0, "max_drawdown": 1.0, "trades": 5, "pnls": [-10_000.0]}

    def second(params, start, end):
        if (start, end) == T1:
            return mutated[(T1, params["name"])]
        return _train()[((start, end), params["name"])]

    again = run_walk_forward(_spec(), second, peeks=0)
    assert first["windows"][0]["params"] == again["windows"][0]["params"] == {"name": "B"}


# ---------------------------------------------------------------- W4 holdout unread
def test_w4_walk_forward_never_reads_the_holdout_or_the_forward_period() -> None:
    def evaluate(params, start, end):
        if start >= date(2026, 7, 1) or end >= date(2026, 7, 1):
            raise AssertionError(f"read {start}..{end}")
        return {"net_pnl": 10.0, "max_drawdown": 5.0, "trades": 3, "pnls": [10.0]}

    spec = _spec(start=date(2026, 1, 2), research_end=date(2026, 6, 30), grid=({"name": "A"},), min_trades=1)
    # one window would need 8 months and does not fit; the guard is also direct
    from app.backtest.holdout import assert_research_range

    assert_research_range(date(2026, 1, 2), date(2026, 6, 30), include_forward=False)
    with pytest.raises(Exception):
        assert_research_range(date(2026, 7, 1), date(2026, 7, 31), include_forward=False)
    with pytest.raises(Exception):
        assert_research_range(date(2026, 10, 2), date(2026, 10, 15), include_forward=False)
    assert_research_range(date(2026, 10, 2), date(2026, 10, 15), include_forward=True)
    run_walk_forward(spec, evaluate, peeks=0)
    opened: list[str] = []

    def guarded(params, start, end):
        opened.append(f"{start}:{end}")
        return {"net_pnl": 12.0, "max_drawdown": 4.0, "trades": 2, "pnls": [12.0]}

    hold = run_holdout({"params": {"name": "A"}}, guarded, peeks=Peek())
    assert hold["holdout"]["peeks"] == 1
    assert opened == ["2026-07-01:2026-10-01"]


def test_w4_a_holdout_only_file_is_not_part_of_the_research_hash(tmp_path: Path) -> None:
    from app.backtest.execute import research_option_files

    (tmp_path / "NIFTY_2026-06-30.parquet").write_bytes(b"june")
    (tmp_path / "NIFTY_2026-07-07.parquet").write_bytes(b"holdout")
    (tmp_path / "NIFTY_2026-10-13.parquet").write_bytes(b"forward")
    files = research_option_files(date(2024, 10, 3), date(2026, 6, 30), root=tmp_path)
    assert [path.name for path in files] == ["NIFTY_2026-06-30.parquet"]


def test_the_frozen_research_span_has_seven_windows() -> None:
    windows = build_windows(date(2024, 10, 3), date(2026, 6, 30), 6, 2, 2)
    assert len(windows) == 7
    assert windows[0].train_start == date(2024, 10, 3)
    assert windows[-1].test_end == date(2026, 6, 2)
    assert all(w.test_end < date(2026, 7, 1) and w.train_end < date(2026, 7, 1) for w in windows)


class Peek:
    def __init__(self) -> None:
        self.n = 0

    def accept(self) -> int:
        self.n += 1
        return self.n

    def count(self) -> int:
        return self.n


# ---------------------------------------------------------------- W5 peek count
def test_w5_peek_count_survives_a_failure_and_is_on_the_result() -> None:
    peeks = Peek()

    def ok(params, start, end):
        return {"net_pnl": 1.0, "max_drawdown": 1.0, "trades": 1, "pnls": [1.0]}

    def boom(params, start, end):
        raise RuntimeError("holdout failed")

    run_holdout({"params": {"name": "A"}}, ok, peeks=peeks)
    with pytest.raises(RuntimeError):
        run_holdout({"params": {"name": "A"}}, boom, peeks=peeks)
    assert peeks.count() == 2
    before = peeks.count()
    stamped = run_walk_forward(_spec(), _evaluate(_train()), peeks=peeks.count())
    assert peeks.count() == before == 2
    assert stamped["holdout"]["peeks"] == 2
    assert stamped["holdout"]["start"] == "2026-07-01"


# ---------------------------------------------------------------- W6 determinism
def test_w6_same_fixture_same_canonical_result() -> None:
    from app.backtest.result import canonical

    one = run_walk_forward(_spec(), _evaluate(_train()), peeks=0)
    two = run_walk_forward(_spec(), _evaluate(_train()), peeks=0)
    assert canonical(one) == canonical(two)
    assert build_windows(START, RESEARCH_END, 6, 2, 2) == build_windows(START, RESEARCH_END, 6, 2, 2)


# ---------------------------------------------------------------- W7 reject
def test_w7_rejects_bad_requests_and_a_second_start(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        parse_walk_forward(_body(start="2024-09-01"))
    with pytest.raises(Exception):
        parse_walk_forward(_body(end="2026-07-01"))
    with pytest.raises(Exception):
        parse_walk_forward(_body(step_months=1))
    with pytest.raises(Exception):
        parse_walk_forward(_body(grid={"fast": list(range(1, 12)), "slow": list(range(20, 30))}))
    with pytest.raises(Exception):
        parse_walk_forward(_body(mode="index"))
    with pytest.raises(Exception):
        parse_walk_forward(_body(symbol="RELIANCE"))
    store = RunStore(tmp_path / "runs.sqlite")
    service = JobService(store, execute=lambda c, r: {}, git_reader=lambda: GitState("abc", False))
    service._active = "already"
    with pytest.raises(JobBusy):
        service.start(_body(kind="walk_forward"))


def _body(**overrides) -> dict:
    body = {
        "kind": "walk_forward",
        "strategy": "opening_range_breakout",
        "symbol": "NIFTY50",
        "timeframe": "5m",
        "start": "2024-10-03",
        "sessions": ["normal", "weekend_full"],
        "mode": "options",
        "strike_offset": 0,
        "slippage_points": 1.0,
        "train_months": 6,
        "test_months": 2,
        "step_months": 2,
        "min_trades": 30,
    }
    body.update(overrides)
    return body
