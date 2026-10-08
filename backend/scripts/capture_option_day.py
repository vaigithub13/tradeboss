"""Record one session of the nearest weekly Nifty expiry's options, ATM +/- 5 over the day's range, into
data/option_history/.

The live backend does this itself after the 15:45 reconcile. By hand (read-only Upstox calls; the current
session comes from the intraday endpoint, an earlier day from the historical one):

    uv run python -m scripts.capture_option_day
    uv run python -m scripts.capture_option_day --day 2026-10-08
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from app.backtest.sources import ist_date
from app.data.store import CandleStore
from app.live.model import IST
from app.options.history import CAPTURE_WINDOW, OptionHistoryStore, capture_session
from app.upstox.deps import make_client
from app.upstox.instruments import InstrumentIndex, latest_snapshot, load_instruments

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--day", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--window", type=int, default=CAPTURE_WINDOW, help="strikes each side of ATM")
    p.add_argument("--store", type=Path, default=DATA / "option_history")
    args = p.parse_args()
    today = datetime.now(IST).date()
    day = date.fromisoformat(args.day) if args.day else today
    snap = latest_snapshot(DATA / "instruments")
    if snap is None:
        raise SystemExit("no instrument snapshot")
    instruments = InstrumentIndex(load_instruments(snap[1]), snapshot_day=snap[0])
    nifty, _ = CandleStore(DATA / "candles").load("NIFTY50")
    lows = [float(c["low"]) for c in nifty if ist_date(c["time"]) == day]
    highs = [float(c["high"]) for c in nifty if ist_date(c["time"]) == day]
    if not lows:
        raise SystemExit(f"no Nifty 1m bars stored for {day}")
    result = capture_session(day, (min(lows), max(highs)), client=make_client(), instruments=instruments,
                             store=OptionHistoryStore(args.store), today=today, window=args.window)
    print(result)


if __name__ == "__main__":
    main()
