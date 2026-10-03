"""Save today's NSE instrument master as a dated snapshot: data/instruments/YYYY-MM-DD/NSE.json.gz

Public file, no token needed. Run by launchd every day at 08:30 IST (see scripts/launchd/) and by
the backend at startup as a fallback. Safe to run any number of times: today's file is only
downloaded once (use --force to replace it). If it is byte-identical to the previous snapshot
(weekends / holidays) it is not saved again: the log says "unchanged".

    uv run python -m scripts.snapshot_instruments [--force]

Exit code 0 = today's snapshot exists, 1 = it could not be taken (network down, bad file, ...).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import httpx

from app.config import settings
from app.upstox.instruments import IST, SnapshotError, list_snapshots, take_snapshot


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="download again even if today's snapshot exists")
    args = ap.parse_args(argv)
    stamp = datetime.now(IST).isoformat(timespec="seconds")
    try:
        result = take_snapshot(settings.instruments_dir, force=args.force)
    except (SnapshotError, httpx.HTTPError, OSError) as e:
        print(f"[{stamp}] snapshot FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    size_kb = result.path.stat().st_size // 1024
    total = len(list_snapshots(settings.instruments_dir))
    if result.outcome == "unchanged":
        print(
            f"[{stamp}] snapshot {result.day} unchanged (identical to {result.same_as}, not saved again); "
            f"{total} snapshot(s) kept"
        )
    else:
        what = "saved" if result.created else "already present"
        print(f"[{stamp}] snapshot {result.day} {what}: {result.path} ({size_kb} KB); {total} snapshot(s) kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
