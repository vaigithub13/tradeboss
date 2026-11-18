"""Save today's NSE + BSE instrument masters: data/instruments/YYYY-MM-DD/{NSE,BSE}.json.gz

Public files, no token needed. Run by launchd every day at 08:30 IST (see scripts/launchd/) and by
the backend at startup as a fallback. Safe to run any number of times: today's file is only
downloaded once (use --force to replace it). If it is byte-identical to the previous snapshot
(weekends / holidays) it is not saved again: the log says "unchanged".

    uv run python -m scripts.snapshot_instruments [--force]

Exit code 0 = today's snapshots exist, 1 = NSE could not be taken (network down, bad file, ...).
BSE failure is logged but does not fail the job (Nifty path stays intact).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import httpx

from app.config import settings
from app.upstox.instruments import IST, SnapshotError, list_snapshots, take_bse_snapshot, take_snapshot


def _report(stamp: str, label: str, result) -> None:  # noqa: ANN001
    size_kb = result.path.stat().st_size // 1024
    if result.outcome == "unchanged":
        print(
            f"[{stamp}] {label} snapshot {result.day} unchanged (identical to {result.same_as}, not saved again)"
        )
    else:
        what = "saved" if result.created else "already present"
        print(f"[{stamp}] {label} snapshot {result.day} {what}: {result.path} ({size_kb} KB)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="download again even if today's snapshot exists")
    args = ap.parse_args(argv)
    stamp = datetime.now(IST).isoformat(timespec="seconds")
    try:
        result = take_snapshot(settings.instruments_dir, force=args.force)
    except (SnapshotError, httpx.HTTPError, OSError) as e:
        print(f"[{stamp}] NSE snapshot FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    _report(stamp, "NSE", result)
    try:
        bse = take_bse_snapshot(settings.instruments_dir, force=args.force)
        _report(stamp, "BSE", bse)
    except (SnapshotError, httpx.HTTPError, OSError) as e:
        print(f"[{stamp}] BSE snapshot FAILED (Nifty ok): {type(e).__name__}: {e}", file=sys.stderr)
    total = len(list_snapshots(settings.instruments_dir))
    print(f"[{stamp}] {total} NSE snapshot day(s) kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
