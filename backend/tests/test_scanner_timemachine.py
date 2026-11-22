"""Time Machine: fixed seed, and the GOOD / COIN FLIP / AVOID rule."""

from __future__ import annotations

from datetime import date

from app.scanner.config import ScannerSettings
from app.scanner.judge import judge
from app.scanner.timemachine import run_time_machine, simulate


DAY = date(2026, 1, 5)


def _up_bucket() -> dict[int, list[tuple[float, float, float, float]]]:
    # every historical bar rallies: high +3%, low unchanged, close +2%
    return {minute: [(1.001, 1.03, 1.0, 1.02)] for minute in range(9 * 60 + 35, 15 * 60 + 11, 5)}


def test_a_one_sided_tape_hits_the_target_and_repeats() -> None:
    kwargs = dict(
        entry=100.0,
        stop=99.0,
        target=102.0,
        side="long",
        buckets=_up_bucket(),
        minutes=[9 * 60 + 35],
        paths=10_000,
        seed=20261123,
        qty=10,
        on=DAY,
    )
    first = simulate(**kwargs)
    second = simulate(**kwargs)
    assert first.target_pct == 100
    assert first.stop_pct == 0
    assert first.open_pct == 0
    assert first.to_dict() == second.to_dict()
    assert first.avg_rupees > 0


def test_mixed_paths_keep_the_seed() -> None:
    buckets = {600: [(1.0, 1.02, 0.98, 1.0), (1.0, 1.0, 0.97, 0.99), (1.0, 1.04, 1.0, 1.03)]}
    spec = {
        "entry": 100.0,
        "stop": 98.0,
        "target": 104.0,
        "side": "long",
        "buckets": {"600": [list(row) for row in buckets[600]]},
        "minutes": [600, 605],
        "paths": 200,
        "seed": 20261123,
        "qty": 5,
        "on": DAY.isoformat(),
    }
    assert run_time_machine(spec) == run_time_machine(spec)
    total = run_time_machine(spec)
    assert abs(total["target_pct"] + total["stop_pct"] + total["open_pct"] - 100) < 1e-6


def test_verdict_thresholds() -> None:
    settings = ScannerSettings()
    assert judge(target_pct=40, stop_pct=25, win_rate=0.5, fires=8, settings=settings) == "GOOD"
    assert judge(target_pct=40, stop_pct=25, win_rate=0.5, fires=7, settings=settings) == "COIN_FLIP"
    assert judge(target_pct=20, stop_pct=40, win_rate=0.6, fires=20, settings=settings) == "AVOID"
    assert judge(target_pct=30, stop_pct=28, win_rate=0.2, fires=20, settings=settings) == "COIN_FLIP"
