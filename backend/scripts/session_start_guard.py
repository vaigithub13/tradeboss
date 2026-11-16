"""Decide whether launchd session-start should leave, recycle, or start the backend.

    cd backend && .venv/bin/python -m scripts.session_start_guard

Exit codes for the shell:
  0  start   — port free (or stale process cleared): proceed to start npm run session
  10 leave   — a backend started today is already listening: do not start a second one
  1         — failed to stop a stale backend

Prefers `data/live-state/backend.ownership` (pid + IST start date) written by the app on startup.
Falls back to pgrep `app.serve` when the file is missing. Never stops a backend whose start
calendar day (IST) is today.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.config import settings
from app.ownership import clear, decide, path_for, pid_alive, read

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
    """Legacy helper (tests): pgrep-only view when no ownership file is in play."""
    from app.ownership import decide as decide_own

    return decide_own(listening=listening, ownership=None, today=today, pgrep_started_on=started_on)


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


def stop_backend_cleanly(*, wait_s: int = WAIT_S, pids: list[str] | None = None) -> bool:
    """One SIGINT (same as Ctrl+C / session-stop), wait for exit."""
    targets = pids if pids is not None else _pgrep()
    own_path = path_for(settings.live_state_dir)
    own = read(own_path)
    if not targets and own is not None and pid_alive(own.pid):
        targets = [str(own.pid)]
    if not targets:
        log("no session backend running")
        clear(own_path)
        return True
    log(f"sending one SIGINT to the stale backend ({' '.join(targets)})")
    subprocess.call(["kill", "-INT", *targets], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait_s):
        alive = False
        for p in targets:
            try:
                if pid_alive(int(p)):
                    alive = True
                    break
            except ValueError:
                continue
        if not alive and not _pgrep():
            log("stale backend shut down cleanly")
            clear(own_path)
            return True
        time.sleep(1)
    log(f"backend still running after {wait_s} s: leaving it (check data/logs/backend-$(date).log)")
    return False


def run(*, today: date | None = None) -> int:
    today = today or datetime.now(IST).date()
    listening = _port_listening()
    own_path = path_for(settings.live_state_dir)
    own = read(own_path)
    pids = _pgrep() if listening else []
    pgrep_started = _oldest_start_date(pids) if pids else None
    action = decide(
        listening=listening, ownership=own, today=today,
        pgrep_started_on=pgrep_started, pid_alive=pid_alive,
    )
    if action == "start":
        if own is not None and not pid_alive(own.pid):
            clear(own_path)
        log("port 8000 free: starting a fresh session" if not listening else "starting a fresh session")
        return 0
    if action == "leave":
        src = f"ownership pid {own.pid} started {own.started_on}" if own is not None else f"pgrep started {pgrep_started}"
        log(f"a backend is already listening on :8000 ({src}): leaving it alone")
        return 10
    # stop_then_start
    if own is not None:
        log(
            f"a backend is already listening on :8000 but ownership says started on {own.started_on} "
            f"(today is {today}): stopping it cleanly, then starting fresh"
        )
        targets = [str(own.pid)] if pid_alive(own.pid) else _pgrep()
    else:
        log(
            f"a backend is already listening on :8000 but was started on {pgrep_started or 'an unknown day'} "
            f"(today is {today}): stopping it cleanly, then starting fresh"
        )
        targets = _pgrep()
    if not stop_backend_cleanly(pids=targets):
        return 1
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
