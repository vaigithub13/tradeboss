"""Move paper day files that a replay wrote into the live folders to `replay/` (they then never count).

Stop the backend first (one Ctrl+C): a running backend rewrites its slots' day files when it shuts down.

    uv run python -m scripts.paper_mark_replay --day 2026-10-08 --slot 3 --slot 4
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from app.paper.desk import PaperDesk
from app.paper.store import mark_replay

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--day", required=True, help="YYYY-MM-DD")
    p.add_argument("--slot", action="append", required=True, help="slot number; repeat for more")
    p.add_argument("--paper-dir", type=Path, default=ROOT / "data" / "paper")
    args = p.parse_args()
    day = date.fromisoformat(args.day)
    for slot in args.slot:
        moved = mark_replay(PaperDesk.slot_dir(args.paper_dir, slot), day)
        print(f"slot {slot}: {day} moved to {moved}")


if __name__ == "__main__":
    main()
