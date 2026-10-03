"""Record one session of currently listed Nifty ATM±1 options into data/option_history/.

This is source (b): the store that grows every week. Uses the regular historical / intraday
API (not the live feed, so Monday's live-feed verification is not disturbed). A later live
recorder can call `ingest_recorded_bars` with the same store.

    uv run python -m scripts.capture_option_day
    uv run python -m scripts.capture_option_day --day 2026-10-06
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from app.backtest.expiry import load_default_calendar
from app.data.store import CandleStore
from app.options.history import OptionHistoryStore, UpstoxHistorySource, capture_listed_day, plan_strikes
from app.options.strikes import load_default_step_table
from app.upstox.deps import make_client
from app.upstox.instruments import InstrumentIndex, latest_snapshot, load_instruments

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CAL = load_default_calendar()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--day", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--store", type=Path, default=DATA / "option_history")
    args = p.parse_args()
    day = date.fromisoformat(args.day) if args.day else date.today()
    snap = latest_snapshot(DATA / "instruments")
    if snap is None:
        raise SystemExit("no instrument snapshot")
    instruments = InstrumentIndex(load_instruments(snap[1]), snapshot_day=snap[0])
    src = UpstoxHistorySource(make_client(), instruments, today=day)
    expiry = CAL.next_expiry(day).date
    candles = CandleStore(DATA / "candles")
    nifty, _ = candles.load("NIFTY50")
    from app.backtest.sources import ist_date

    lo = hi = None
    for c in nifty:
        if ist_date(c["time"]) != day:
            continue
        lo = float(c["low"]) if lo is None else min(lo, float(c["low"]))
        hi = float(c["high"]) if hi is None else max(hi, float(c["high"]))
    if lo is None or hi is None:
        raise SystemExit(f"no Nifty 1m bars stored for {day}")
    step = load_default_step_table().step("NIFTY", day)
    strikes = plan_strikes({day: (lo, hi)}, step, window=1)
    stats = capture_listed_day(src, OptionHistoryStore(args.store), day, expiry, strikes, label="live-recorded")
    print(f"{day} expiry={expiry} strikes={strikes} fetched={stats.fetched} not_listed={stats.not_listed} failed={stats.failed}")


if __name__ == "__main__":
    main()
