"""Reconcile live-built bars with official candles (manual run of what the backend does itself).

    uv run python -m scripts.reconcile_live --pending          # historical pass for every day not yet final
    uv run python -m scripts.reconcile_live --day 2026-10-05   # intraday now, then historical if it is published
    uv run python -m scripts.reconcile_live --status           # days that are not final, with their stage

The backend also does this on its own: intraday at 15:45 IST (retry until 16:30), historical at
the next startup and at 09:00. A day that is not final is retried daily. The report is
data/feed-recordings/<day>.reconcile.jsonl. Expected rows (futures 15:30-15:39, open-interest
only) are separate from differences.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from app.config import settings
from app.live.persist import ReconcileState, stored_day
from app.live.reconcile import reconcile_intraday_day, reconcile_missed
from app.live.model import IST
from app.upstox.deps import make_client


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pending", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--day", type=date.fromisoformat)
    args = ap.parse_args(argv)

    state = ReconcileState(settings.live_state_dir)
    if args.status:
        rows = state.not_final()
        for d, k, stage in rows:
            print(d, k, stage)
        print(f"{len(rows)} not final")
        return 0
    client = make_client()
    if client is None:
        print("No UPSTOX_ANALYTICS_TOKEN in .env", file=sys.stderr)
        return 2
    today = datetime.now(IST).date()
    reports = []
    if args.day:
        keys = [k for d, k, _stage in state.not_final() if d == args.day]
        if not keys:
            from app.routes.candles import get_store

            store = get_store()
            for sym in store.symbols():
                key = store.meta(sym).instrument.get("instrument_key") if sym != "NIFTY50" else "NSE_INDEX|Nifty 50"
                if key and stored_day(settings.candles_dir, key, args.day) and state.status(args.day, key) != "final":
                    state.mark(args.day, key)
                    keys.append(key)
        intra = reconcile_intraday_day(
            client, args.day, keys,
            candles_dir=settings.candles_dir, log_dir=settings.feed_recordings_dir, state=state,
        )
        hist = reconcile_missed(
            client, candles_dir=settings.candles_dir, log_dir=settings.feed_recordings_dir,
            state=state, today=today, only_day=args.day,
        )
        reports = [*intra, *hist]
    else:
        reports = reconcile_missed(
            client, candles_dir=settings.candles_dir, log_dir=settings.feed_recordings_dir,
            state=state, today=today,
        )
    for r in reports:
        stage = state.status(r.day, r.key) or "pending"
        print(
            f"{r.day} {r.key}: {'ok' if r.ok else 'FAILED ' + str(r.error)}  "
            f"official={r.official_bars} differences={len(r.diffs)} expected={len(r.expected)} "
            f"replaced={r.replaced_rows} stage={stage}"
        )
    print(f"{len(reports)} attempted; not final: {len(state.not_final())}")
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
