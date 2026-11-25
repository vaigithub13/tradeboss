"""Stop today's session backend cleanly (one SIGINT), using the ownership file when present.

    cd backend && .venv/bin/python -m scripts.session_stop

Used by launchd `session-stop.sh` at 16:35 IST. Falls back to pgrep `app.serve` when the ownership
file is missing (older backends).
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.ownership import clear, path_for, pid_alive, read
from scripts.session_start_guard import SERVE_PATTERN

IST = ZoneInfo("Asia/Kolkata")
WAIT_S = 60


def log(msg: str) -> None:
    print(f"{datetime.now(IST):%Y-%m-%d %H:%M:%S} IST session-stop: {msg}", flush=True)


def _pgrep() -> list[str]:
    try:
        out = subprocess.check_output(["/usr/bin/pgrep", "-f", SERVE_PATTERN], text=True)
    except subprocess.CalledProcessError:
        return []
    return [p for p in out.split() if p]


def _record_prediction_review() -> None:
    """Score the day's similar-pattern log before the backend is stopped. Never rewrites a prediction."""
    try:
        from app.data.store import CandleStore
        from app.similar.predictions import write_review_if_due

        wrote = write_review_if_due(settings.data_dir / "similar", CandleStore(settings.data_dir / "candles"))
        log("similar-pattern review written" if wrote else "similar-pattern review already saved or not due")
    except Exception as exc:
        log(f"similar-pattern review skipped: {type(exc).__name__}")


def stop(*, wait_s: int = WAIT_S, state_dir: Path | None = None) -> int:
    """Send one SIGINT to the owned backend (or pgrep fallback). Exit 0 always for launchd."""
    _record_prediction_review()
    own_path = path_for(state_dir or settings.live_state_dir)
    own = read(own_path)
    pids: list[str] = []
    if own is not None and pid_alive(own.pid):
        pids = [str(own.pid)]
        log(f"ownership file {own_path.name}: pid {own.pid} started {own.started_on}")
    else:
        pids = _pgrep()
        if own is not None:
            clear(own_path)  # stale file
    if not pids:
        log("no session backend running")
        clear(own_path)
        return 0
    log(f"sending one SIGINT to the backend ({' '.join(pids)})")
    subprocess.call(["kill", "-INT", *pids], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait_s):
        if own is not None and not pid_alive(own.pid) and not _pgrep():
            log("backend shut down cleanly")
            clear(own_path)
            return 0
        if own is None and not _pgrep():
            log("backend shut down cleanly")
            return 0
        time.sleep(1)
    log(f"backend still running after {wait_s} s: leaving it (check data/logs/backend-$(date).log)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    rc = stop()
    subprocess.call(
        ["/usr/bin/pkill", "-INT", "-f", "vite preview"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
