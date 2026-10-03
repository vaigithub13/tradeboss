import pytest

from app.upstox.ratelimit import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_calls_under_the_cap_never_wait() -> None:
    clock = Clock()
    lim = SlidingWindowLimiter([(5, 1.0)], clock=clock, sleep=clock.sleep)
    for _ in range(5):
        lim.acquire()
    assert clock.slept == []


def test_the_call_over_the_per_second_cap_waits_until_the_oldest_leaves_the_window() -> None:
    clock = Clock()
    lim = SlidingWindowLimiter([(2, 1.0)], clock=clock, sleep=clock.sleep)
    lim.acquire()
    clock.now = 0.25
    lim.acquire()
    lim.acquire()  # 3rd within the window: waits until t=1.0
    assert clock.now == pytest.approx(1.0)


def test_every_window_is_enforced_the_slowest_one_wins() -> None:
    clock = Clock()
    lim = SlidingWindowLimiter([(100, 1.0), (3, 60.0)], clock=clock, sleep=clock.sleep)
    for _ in range(3):
        lim.acquire()
    lim.acquire()  # per-minute cap of 3 reached
    assert clock.now == pytest.approx(60.0)


def test_rejects_nonsense_limits() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter([])
    with pytest.raises(ValueError):
        SlidingWindowLimiter([(0, 1.0)])
