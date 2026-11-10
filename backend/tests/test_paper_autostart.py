"""scripts/paper_autostart.py: which slots it starts, waiting, retries, and the shipped config. No socket."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.paper_autostart import CONFIG, load_config, run, running_slots, to_start

IST = timezone(timedelta(hours=5, minutes=30))
CFG = {
    "enabled": True,
    "start_at_ist": "09:16",
    "give_up_at_ist": "09:20",
    "slots": [
        {"slot": "1", "strategy": "log_xz", "params": {}},
        {"slot": "2", "strategy": "price_channel", "params": {"length": 20}},
    ],
}


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += timedelta(seconds=s)


def test_shipped_config_matches_the_checklist() -> None:
    cfg = load_config(CONFIG)
    got = [(s["slot"], s["strategy"], s["params"]) for s in cfg["slots"]]
    assert got == [
        ("1", "log_xz", {}),
        ("2", "price_channel", {"length": 20}),
        ("3", "log_xz", {"exit_rule": "premium_1to2"}),
        ("4", "price_channel", {"length": 20, "exit_rule": "premium_1to2"}),
    ]


def test_running_slots_and_plan() -> None:
    status = {"slots": [{"slot": "1", "state": "running"}, {"slot": "2", "state": "idle"}]}
    assert running_slots(status) == {"1"}
    assert [s["slot"] for s in to_start(CFG, {"1"}, set())] == ["2"]


def test_waits_until_0916_then_starts_each_slot_once() -> None:
    clock = Clock(datetime(2026, 11, 9, 8, 41, tzinfo=IST))
    calls: list[tuple[str, str, Any]] = []

    def req(method: str, path: str, body: Any) -> tuple[int, Any]:
        calls.append((method, path, body))
        if path == "/api/paper/status":
            return 200, {"slots": []}
        return 200, {"slot": body["slot"]}

    out = run(CFG, request=req, now=clock.now, sleep=clock.sleep)
    assert out == {"1": "started", "2": "started"}
    assert clock.slept[0] == 35 * 60
    posts = [c[2] for c in calls if c[0] == "POST"]
    assert posts == [
        {"slot": "1", "strategy": "log_xz", "params": {}, "start_source": "auto"},
        {"slot": "2", "strategy": "price_channel", "params": {"length": 20}, "start_source": "auto"},
    ]


def test_skips_muhurat_and_other_special_sessions_with_no_posts() -> None:
    # 8 Nov 2026 is Diwali Muhurat: backend may record, paper slots must not auto-start.
    clock = Clock(datetime(2026, 11, 8, 9, 17, tzinfo=IST))
    posts: list[Any] = []

    def req(method: str, path: str, body: Any) -> tuple[int, Any]:
        if method == "POST":
            posts.append(body)
        return 200, {"slots": []}

    assert run(CFG, request=req, now=clock.now, sleep=clock.sleep, start_now=True) == {}
    assert posts == []
    assert clock.slept == []


def test_a_running_slot_is_left_alone_and_a_failure_is_retried_then_given_up() -> None:
    clock = Clock(datetime(2026, 11, 9, 9, 17, tzinfo=IST))

    def req(method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "/api/paper/status":
            return 200, {"slots": [{"slot": "1", "state": "running"}]}
        return 503, {"detail": "no bars yet"}

    out = run(CFG, request=req, now=clock.now, sleep=clock.sleep)
    assert out["1"] == "already running"
    assert out["2"].startswith("failed: HTTP 503")
    assert clock.slept and all(s == 30 for s in clock.slept)
    assert clock.t >= datetime(2026, 11, 9, 9, 20, tzinfo=IST)


def test_a_bad_setting_is_not_retried_and_disabled_does_nothing() -> None:
    clock = Clock(datetime(2026, 11, 9, 9, 17, tzinfo=IST))
    posts: list[Any] = []

    def req(method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "/api/paper/status":
            return 200, {"slots": []}
        posts.append(body)
        return 400, {"detail": "unknown strategy"}

    out = run(CFG, request=req, now=clock.now, sleep=clock.sleep)
    assert len(posts) == 2 and clock.slept == []
    assert out["2"] == "failed: unknown strategy"
    assert run({**CFG, "enabled": False}, request=req, now=clock.now, sleep=clock.sleep) == {}
