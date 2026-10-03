"""Fit the option overlay against stored (or freshly fetched) 1m option history.

Does not hit the network unless you pass --fetch. Tests never invoke this.

    uv run python -m scripts.calibrate_option_model
    uv run python -m scripts.calibrate_option_model --fetch   # resumable; every expiry from 2024-10
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from app.backtest.expiry import load_default_calendar
from app.backtest.sources import ist_date
from app.data.store import CandleStore
from app.options.calibration import (
    build_report,
    filter_points,
    opens_from_candles,
    pair_points,
    write_report,
)
from app.options.history import (
    OptionHistoryStore,
    RecordedSource,
    UpstoxHistorySource,
    fetch_expiry,
    merge_bars,
    plan_strikes,
    trading_days_before,
)
from app.options.strikes import load_default_step_table

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CAL = load_default_calendar()


def _day_ranges(nifty: list, days: list[date]) -> dict[date, tuple[float, float]]:
    wanted = set(days)
    out: dict[date, tuple[float, float]] = {}
    for c in nifty:
        d = ist_date(c["time"])
        if d not in wanted:
            continue
        lo, hi = float(c["low"]), float(c["high"])
        if d not in out:
            out[d] = (lo, hi)
        else:
            a, b = out[d]
            out[d] = (min(a, lo), max(b, hi))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-expiry", default="2024-10-03")
    p.add_argument("--store", type=Path, default=DATA / "option_history")
    p.add_argument("--report-dir", type=Path, default=DATA / "validation")
    p.add_argument("--fetch", action="store_true", help="pull missing contracts (rate-limited, resumable)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--as-of", default=None)
    p.add_argument("--bootstrap", type=int, default=30)
    args = p.parse_args()
    store = OptionHistoryStore(args.store)
    start = date.fromisoformat(args.from_expiry)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    step = load_default_step_table().step("NIFTY", as_of)
    candles = CandleStore(DATA / "candles")

    if args.fetch:
        from app.upstox.deps import make_client
        from app.upstox.instruments import InstrumentIndex, latest_snapshot, load_instruments

        client = make_client()
        if client is None:
            raise SystemExit("no UPSTOX_ANALYTICS_TOKEN — cannot fetch")
        snap = latest_snapshot(DATA / "instruments")
        instruments = InstrumentIndex(load_instruments(snap[1]), snapshot_day=snap[0]) if snap else None
        src = UpstoxHistorySource(client, instruments, today=as_of)
        expiries = [e for e in src.expiries() if e >= start]
        if instruments is not None:
            listed = {i.expiry for i in instruments.instruments if i.kind == "option" and i.expiry and i.expiry >= as_of}
            expiries = sorted(set(expiries) | listed)
        nifty, _ = candles.load("NIFTY50")
        print(f"fetch {len(expiries)} expiries from {start} (resumable, store={store.base_dir})", flush=True)
        for i, exp in enumerate(expiries, 1):
            days = trading_days_before(exp, args.days, CAL.is_trading_day)
            strikes = plan_strikes(_day_ranges(nifty, days), step, window=1)
            stats = fetch_expiry(src, store, exp, strikes, days)
            print(
                f"  [{i}/{len(expiries)}] {exp} fetched={stats.fetched} skipped={stats.skipped} "
                f"not_listed={stats.not_listed} failed={stats.failed} empty={stats.empty}",
                flush=True,
            )

    bars = merge_bars(store.read(e) for e in store.expiries() if e >= start)
    try:
        nifty, _ = candles.load("NIFTY50")
        vix, _ = candles.load("NSE_INDEX_India_VIX")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"need stored Nifty and India VIX 1m history: {exc}") from exc
    points = pair_points(bars, opens_from_candles(nifty), opens_from_candles(vix), calendar=CAL, step=step)
    kept, counts = filter_points(points)
    sources = {
        "recorded_source": RecordedSource(store).name,
        "option_bars": len(bars),
        "store_expiries": [e.isoformat() for e in store.expiries()],
    }
    report = build_report(kept, counts, as_of=as_of, sources=sources, step=step, bootstrap_n=args.bootstrap)
    md, js = write_report(report, args.report_dir, as_of)
    print(f"wrote {md} and {js}")
    print(f"scale={report['scale_trading']} time_basis winner={report['time_basis']['all'].get('winner')}")
    print(f"implied carry={report['implied_carry']}")


if __name__ == "__main__":
    main()
