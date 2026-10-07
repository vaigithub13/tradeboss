"""One Ctrl+C reaches uvicorn twice (terminal + concurrently/uv). The repeat must not force-skip the app's shutdown."""

from __future__ import annotations

import signal

import uvicorn

from app.serve import SessionServer


def server(times: list[float]) -> SessionServer:
    it = iter(times)
    return SessionServer(uvicorn.Config("app.main:app"), clock=lambda: next(it))


def test_a_repeated_sigint_within_five_seconds_is_the_same_ctrl_c() -> None:
    s = server([0.0, 0.2])
    s.handle_exit(signal.SIGINT, None)
    s.handle_exit(signal.SIGINT, None)
    assert s.should_exit and not s.force_exit


def test_a_second_ctrl_c_later_still_forces_the_exit() -> None:
    s = server([0.0, 6.0])
    s.handle_exit(signal.SIGINT, None)
    s.handle_exit(signal.SIGINT, None)
    assert s.force_exit


def test_sigterm_shuts_down_gracefully() -> None:
    s = server([0.0])
    s.handle_exit(signal.SIGTERM, None)
    assert s.should_exit and not s.force_exit
