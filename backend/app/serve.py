"""Run the backend for a market session: uvicorn without auto-reload, and a Ctrl+C that always shuts down cleanly.

    uv run python -m app.serve [app.main:app] [--host 127.0.0.1] [--port 8000] [--app-dir DIR]

One Ctrl+C in the terminal reaches uvicorn more than once: the terminal signals the whole process group, and
`concurrently` (and `uv run`) forward it again. Uvicorn treats a second SIGINT as "force exit" and then skips
the app's shutdown, which writes the spread report and the paper day file (7 Oct 2026: no spread report).
Here a repeat SIGINT within `DUPLICATE_S` of the first is ignored; a real second Ctrl+C after that still
forces the exit. The graceful wait for open connections (the browser's /ws) is capped.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from types import FrameType

import uvicorn

DUPLICATE_S = 5.0
GRACEFUL_TIMEOUT_S = 5


class SessionServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, clock=time.monotonic) -> None:  # noqa: ANN001
        super().__init__(config)
        self._clock = clock
        self._first_exit_at: float | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        now = self._clock()
        if self._first_exit_at is None:
            self._first_exit_at = now
        elif sig == signal.SIGINT and now - self._first_exit_at < DUPLICATE_S:
            return  # the same Ctrl+C, delivered again by a parent process
        super().handle_exit(sig, frame)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app", nargs="?", default="app.main:app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--app-dir", default=None)
    args = ap.parse_args(argv)
    if args.app_dir:
        sys.path.insert(0, args.app_dir)
    config = uvicorn.Config(args.app, host=args.host, port=args.port, timeout_graceful_shutdown=GRACEFUL_TIMEOUT_S)
    SessionServer(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
