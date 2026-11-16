"""Backend ownership file: pid + IST start date so session-start/stop recognise any backend.

Written when the FastAPI app starts (hand `npm run session`, launchd, or uvicorn), cleared on shutdown.
`scripts/session_start_guard` and `scripts/session_stop` read it so a process that does not match the old
`app.serve` pgrep pattern is still owned.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
Action = Literal["start", "leave", "stop_then_start"]
FILE_NAME = "backend.ownership"


@dataclass(frozen=True)
class Ownership:
    pid: int
    started_on: date
    started_at: datetime | None = None


def path_for(state_dir: Path) -> Path:
    return state_dir / FILE_NAME


def write(path: Path, *, pid: int | None = None, started_on: date | None = None,
          started_at: datetime | None = None) -> Ownership:
    """Create/replace the ownership file. Defaults: this process, today IST, now IST."""
    now = datetime.now(IST)
    own = Ownership(
        pid=os.getpid() if pid is None else pid,
        started_on=now.date() if started_on is None else started_on,
        started_at=now if started_at is None else started_at,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": own.pid,
        "started_on": own.started_on.isoformat(),
        "started_at": None if own.started_at is None else own.started_at.isoformat(),
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return own


def read(path: Path) -> Ownership | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        started_at = raw.get("started_at")
        return Ownership(
            pid=int(raw["pid"]),
            started_on=date.fromisoformat(str(raw["started_on"])),
            started_at=None if not started_at else datetime.fromisoformat(str(started_at)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear(path: Path) -> None:
    path.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours to signal with 0 on some systems
    return True


def decide(
    *,
    listening: bool,
    ownership: Ownership | None,
    today: date,
    pid_alive: Callable[[int], bool] = pid_alive,
    pgrep_started_on: date | None = None,
) -> Action:
    """How session-start should treat :8000.

    Ownership wins when its pid is still alive. A dead pid's file is ignored (start / fall through).
    With no usable ownership file, fall back to the pgrep start-date (legacy).
    """
    if ownership is not None and pid_alive(ownership.pid):
        if ownership.started_on == today:
            return "leave"
        return "stop_then_start"
    if not listening:
        return "start"
    if pgrep_started_on == today:
        return "leave"
    return "stop_then_start"
