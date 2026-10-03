"""Fetch 1m history from Upstox into data/candles/<symbol>/1m.parquet (only the missing ranges).

Needs UPSTOX_ANALYTICS_TOKEN in .env. Read-only. Safe to interrupt and re-run.

    uv run python -m scripts.backfill --defaults            # Nifty 50 + India VIX (from Jan 2022) + current Nifty future
    uv run python -m scripts.backfill --key "NSE_INDEX|Nifty 50" [--from 2022-01-01]
    uv run python -m scripts.backfill --symbol RELIANCE --days 90
    uv run python -m scripts.backfill --update              # bring every stored 1m symbol up to date
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

from app.config import settings
from app.data.history import MIN_HISTORY_DATE, SyncProgress, read_meta, sync_symbol
from app.routes.upstox import DEFAULT_FUTURE_DAYS, default_start
from app.upstox.client import UpstoxError
from app.upstox.deps import make_client
from app.upstox.instruments import (
    IST,
    NIFTY_INDEX_KEY,
    VIX_KEY,
    Instrument,
    InstrumentIndex,
    current_index,
)


def _printer(label: str):  # noqa: ANN202
    last = {"msg": ""}

    def on(p: SyncProgress) -> None:
        if p.message != last["msg"] and p.message.startswith(("fetching", "fetched")):
            last["msg"] = p.message
            print(f"  [{label}] {p.windows_done}/{p.windows_total} {p.message}", flush=True)

    return on


def _sync(client, index: InstrumentIndex, inst: Instrument, start: date) -> None:  # noqa: ANN001
    print(f"{inst.symbol} ({inst.key}) from {start}")
    res = sync_symbol(client, settings.candles_dir, inst, from_date=start, on_progress=_printer(inst.symbol))
    print(f"  -> {res.bars_in_file} 1m bars stored in {res.symbol_dir}/1m.parquet ({res.windows_fetched} window(s) fetched)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--defaults", action="store_true", help="Nifty 50 + India VIX + current Nifty future")
    ap.add_argument("--key", action="append", default=[], help="instrument_key (repeatable)")
    ap.add_argument("--symbol", action="append", default=[], help="exact trading symbol, e.g. RELIANCE (repeatable)")
    ap.add_argument("--update", action="store_true", help="refresh every stored symbol that has 1m data")
    ap.add_argument("--from", dest="from_date", type=date.fromisoformat, help="first date (YYYY-MM-DD)")
    ap.add_argument("--days", type=int, help="how many days back from today")
    args = ap.parse_args(argv)

    client = make_client()
    if client is None:
        print("No UPSTOX_ANALYTICS_TOKEN in .env", file=sys.stderr)
        return 2
    index = current_index(settings.instruments_dir)
    if index is None:
        print("No instrument snapshot yet: run  uv run python -m scripts.snapshot_instruments", file=sys.stderr)
        return 2

    today = datetime.now(IST).date()
    jobs: list[tuple[Instrument, date]] = []

    def start_for(inst: Instrument) -> date:
        if args.from_date:
            return args.from_date
        if args.days:
            return max(MIN_HISTORY_DATE, today - timedelta(days=args.days))
        return default_start(inst, today)

    wanted: list[Instrument] = []
    if args.defaults:
        for key in (NIFTY_INDEX_KEY, VIX_KEY):
            inst = index.get(key)
            if inst is None:
                print(f"{key} not in the instrument file", file=sys.stderr)
                return 2
            wanted.append(inst)
        fut = index.front_future(today)
        if fut is None:
            print("no current Nifty future in the instrument file", file=sys.stderr)
            return 2
        wanted.append(fut)
    for key in args.key:
        inst = index.get(key)
        if inst is None:
            print(f"unknown instrument key {key!r}", file=sys.stderr)
            return 2
        wanted.append(inst)
    for sym in args.symbol:
        hits = [i for i in index.search(sym, limit=50) if i.symbol.lower() == sym.lower()]
        if len(hits) != 1:
            print(f"symbol {sym!r} matched {len(hits)} instruments; use --key", file=sys.stderr)
            return 2
        wanted.append(hits[0])
    for inst in wanted:
        s = start_for(inst)
        if args.defaults and inst.kind == "future" and not (args.from_date or args.days):
            s = today - timedelta(days=DEFAULT_FUTURE_DAYS)
        jobs.append((inst, s))

    if args.update:
        if not settings.candles_dir.is_dir():
            print("nothing stored yet")
        else:
            for d in sorted(p for p in settings.candles_dir.iterdir() if p.is_dir()):
                meta = read_meta(d)
                key = meta.instrument.get("instrument_key")
                inst = index.get(key) if isinstance(key, str) else None
                if inst is None or not meta.covered:
                    continue  # unknown / expired contract / never synced: skip quietly
                jobs.append((inst, meta.covered[0][0]))

    if not jobs:
        ap.print_help()
        return 2
    seen: set[str] = set()
    try:
        for inst, start in jobs:
            if inst.key in seen:
                continue
            seen.add(inst.key)
            _sync(client, index, inst, start)
    except UpstoxError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
