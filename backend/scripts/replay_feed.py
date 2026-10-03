"""Replay a recorded feed session through the SAME pipeline as the live feed (no network).

    uv run python -m scripts.replay_feed data/feed-recordings/2026-10-05.bin --speed 100
    uv run python -m scripts.replay_feed 2026-10-05 --speed 0 --out /tmp/replay

--speed 1 / 10 / 100 spaces the frames by the recorded gaps divided by the speed; 0 = as fast as
possible (default). Writes <out>/<day>.minutes.jsonl (tick | I1 | [official] per minute, plus the
summary line that answers "is I1 the forming or the last completed bar?" and "which 09:15 volume
baseline matches?") and prints the summary. With --official the day's official bars are fetched
once from the historical API (needs the token) to fill the official columns.

Backfill requests are NOT fetched during a replay (they are listed in the summary), so the
replayed bars are exactly what the live pipeline built from the frames.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from app.config import settings
from app.live.engine import EngineConfig, LiveEngine
from app.live.minutelog import minutes_path, write_minute_log
from app.live.recorder import recording_path, replay


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", help="path to a .bin recording, or a date YYYY-MM-DD")
    ap.add_argument("--speed", type=float, default=0.0, help="0 (default, no waiting), 1, 10 or 100")
    ap.add_argument("--out", type=Path, default=None, help="where to write the minute log (default: data/feed-recordings/replay)")
    ap.add_argument("--baseline", choices=["first_tick", "pre_open_inclusive"], default=settings.live_open_volume_baseline)
    ap.add_argument("--official", action="store_true", help="fetch the official 1m bars (network) to fill the official columns")
    args = ap.parse_args(argv)

    path = Path(args.recording)
    if not path.exists():
        try:
            path = recording_path(settings.feed_recordings_dir, date.fromisoformat(args.recording))
        except ValueError:
            pass
    if not path.exists():
        print(f"no such recording: {args.recording}", file=sys.stderr)
        return 2

    eng = LiveEngine(EngineConfig(open_volume_baseline=args.baseline))
    t0 = time.monotonic()
    n = replay(path, eng.on_frame, speed=args.speed)
    eng.end_day()
    close_records = eng.minute_records()
    outstanding = [(r.key, r.first_minute, r.last_minute) for r in eng.outstanding_backfills()]

    if args.official and eng.day is not None:
        from app.live.reconcile import fetch_official
        from app.upstox.deps import make_client

        client = make_client()
        if client is None:
            print("no token: skipping --official", file=sys.stderr)
        else:
            for key in eng.keys():
                eng.apply_official(key, fetch_official(client, key, eng.day))
    summary = eng.daily_summary()
    summary["replay"] = {"recording": str(path), "frames": n, "speed": args.speed, "wall_s": round(time.monotonic() - t0, 2),
                         "baseline": args.baseline, "backfill_not_fetched": outstanding}
    out_dir = args.out or settings.feed_recordings_dir / "replay"
    day_iso = summary["day"] or path.stem
    target = minutes_path(out_dir, day_iso)
    write_minute_log(target, close=close_records, reconciled=eng.minute_records() if args.official else None, summary=summary)
    print(json.dumps({k: summary[k] for k in ("day", "i1_timing", "open_bar_volume", "latency_ms", "replay")}, indent=1, default=str))
    print(f"minute log: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
