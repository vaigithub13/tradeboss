"""Decide whether launchd session-start should leave, recycle, or start the backend.

    cd backend && .venv/bin/python -m scripts.session_start_guard

Exit codes for the shell:
  0  start   — port free (or stale process cleared): proceed to start npm run session
  10 leave   — a backend started today is already listening: do not start a second one
  1         — failed to stop a stale backend

Never stops a backend whose process start calendar day (IST) is today.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
Action = Literal["start", "leave", "stop_then_start"]
SERVE_PATTERN = r"app\.serve app\.main:app"
WAIT_S = 60


def log(msg: str) -> None:
    print(f"{datetime.now(IST):%Y-%m-%d %H:%M:%S} IST session-start: {msg}", flush=True)


def parse_lstart_date_ist(lstart: str) -> date | None:
    """Parse `ps -o lstart=` (e.g. 'Mon Nov  9 08:51:19 2026') to a calendar date.

    The Mac runs in IST for TradeBoss; lstart is local wall clock.
    """
    text = " ".join(lstart.strip().split())
    if not text:
        return None
    try:
        return datetime.strptime(text, "%a %b %d %H:%M:%S %Y").date()
    except ValueError:
        return None


def decide_backend(*, listening: bool, started_on: date | None, today: date) -> Action:
    if not listening:
        return "start"
    if started_on == today:
        return "leave"
    return "stop_then_start"


def _pgrep() -> list[str]:
    try:
        out = subprocess.check_output(["/usr/bin/pgrep", "-f", SERVE_PATTERN], text=True)
    except subprocess.CalledProcessError:
        return []
    return [p for p in out.split() if p]


def _port_listening() -> bool:
    return subprocess.call(
        ["/usr/sbin/lsof", "-nP", "-iTCP:8000", "-sTCP:LISTEN"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0


def _lstart(pid: str) -> str:
    try:
        return subprocess.check_output(["/bin/ps", "-o", "lstart=", "-p", pid], text=True)
    except subprocess.CalledProcessError:
        return ""


def _oldest_start_date(pids: list[str]) -> date | None:
    dates = [parse_lstart_date_ist(_lstart(pid)) for pid in pids]
    known = [d for d in dates if d is not None]
    return min(known) if known else None


def stop_backend_cleanly(*, wait_s: int = WAIT_S) -> bool:
    """One SIGINT to app.serve (same as Ctrl+C / session-stop), wait for exit."""
    pids = _pgrep()
    if not pids:
        log("no session backend running")
        return True
    log(f"sending one SIGINT to the stale backend ({' '.join(pids)})")
    subprocess.call(["kill", "-INT", *pids], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait_s):
        if not _pgrep():
            log("stale backend shut down cleanly")
            return True
        time.sleep(1)
    log(f"backend still running after {wait_s} s: leaving it (check data/logs/backend-$(date).log)")
    return False


def run(*, today: date | None = None) -> int:
    today = today or datetime.now(IST).date()
    listening = _port_listening()
    pids = _pgrep() if listening else []
    started_on = _oldest_start_date(pids) if pids else None
    action = decide_backend(listening=listening, started_on=started_on, today=today)
    if action == "start":
        log("port 8000 free: starting a fresh session")
        return 0
    if action == "leave":
        log(f"a backend is already listening on :8000 (started today {started_on}): leaving it alone")
        return 10
    log(
        f"a backend is already listening on :8000 but was started on {started_on or 'an unknown day'} "
        f"(today is {today}): stopping it cleanly, then starting fresh"
    )
    if not stop_backend_cleanly():
        return 1
    # Vite preview from the leftover session, if any.
    subprocess.call(["/usr/bin/pkill", "-INT", "-f", "vite preview"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if _port_listening():
        log("port 8000 still listening after stop: not starting a second backend")
        return 1
    log("stale backend cleared: starting a fresh session")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guard for launchd session-start against overnight leftovers")
    parser.parse_args(argv)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
