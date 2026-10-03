"""Reconcile live-built bars with the official history (manual run of what the backend does itself).

    uv run python -m scripts.reconcile_live --pending          # every past day still marked unreconciled
    uv run python -m scripts.reconcile_live --day 2026-10-05   # re-mark that day (stored symbols only) and reconcile it
    uv run python -m scripts.reconcile_live --status           # list what is pending

The backend also does this on its own: at 15:45 IST (retry until 16:30) for today, and at every
startup for past days still pending. Differences go to data/feed-recordings/<day>.reconcile.jsonl.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from app.config import settings
from app.live.persist import ReconcileState, is_stored
from app.live.reconcile import reconcile_missed
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
        for d, k in state.pending():
            print(d, k)
        print(f"{len(state.pending())} pending")
        return 0
    client = make_client()
    if client is None:
        print("No UPSTOX_ANALYTICS_TOKEN in .env", file=sys.stderr)
        return 2
    if args.day:
        # mark every stored Nifty/VIX/futures/chart symbol that has bars for that day
        from app.data.history import symbol_dir_name  # noqa: F401
        from app.routes.candles import get_store

        store = get_store()
        for sym in store.symbols():
            key = store.meta(sym).instrument.get("instrument_key") if sym != "NIFTY50" else "NSE_INDEX|Nifty 50"
            if key and is_stored(settings.candles_dir, key):
                state.mark(args.day, key)
    today = datetime.now(IST).date()
    reports = reconcile_missed(client, candles_dir=settings.candles_dir, log_dir=settings.feed_recordings_dir, state=state, today=today)
    for r in reports:
        print(f"{r.day} {r.key}: {'ok' if r.ok else 'FAILED ' + str(r.error)}  official={r.official_bars} differences={len(r.diffs)} replaced={r.replaced_rows}")
    print(f"{len(reports)} reconciled/attempted; still pending: {len(state.pending())}")
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
