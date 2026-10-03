"""One-time validation: our 5m (resampled from stored 1m) vs Upstox's own 5m candles.

For each sample month it compares, bar by bar (open/high/low/close/volume, and the 09:15 bars
separately):
  A. our 5m-from-1m            vs  Upstox 5m (fetched live now, NOT stored)
  B. our 5m-from-1m            vs  the saved 5m sample (data/candles/NIFTY50/5m.parquet)
  C. the saved 5m sample       vs  Upstox 5m
Writes data/validation/5m_vs_upstox_<today>.md and prints the summary.

    uv run python -m scripts.validate_5m [--months 2022-03 2024-03 2025-02 2026-09]
    uv run python -m scripts.validate_5m --key "NSE_EQ|INE002A01018" --months 2026-09   # any stored symbol

For any symbol other than Nifty 50 there is no saved 5m sample, so only comparison A runs. Every
run also adds the per-day volume sums (ours vs Upstox) next to the bar-by-bar volume check.
"""

from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, datetime, timedelta

from app.config import settings
from app.data.history import read_parquet, split_windows
from app.data.importer import build_frame
from app.data.resampler import resample
from app.data.sessions import SESSION_TYPES
from app.data.store import CandleStore
from app.data.history import symbol_dir_name
from app.data.validation import (
    CompareReport,
    compare_bars,
    daily_volume_sums,
    render_report,
    render_volume_sums,
)
from app.upstox.client import parse_candles
from app.upstox.deps import make_client
from app.upstox.instruments import IST, NIFTY_INDEX_KEY

DEFAULT_MONTHS = ["2022-03", "2024-03", "2025-02", "2026-09"]  # + special Saturday 2024-03-02, Budget Saturday 2025-02-01


def month_range(ym: str) -> tuple[date, date]:
    y, m = (int(p) for p in ym.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def ts_range(a: date, b: date) -> tuple[int, int]:
    lo = int(datetime(a.year, a.month, a.day, tzinfo=IST).timestamp())
    hi = int(datetime(b.year, b.month, b.day, tzinfo=IST).timestamp()) + 86400 - 1
    return lo, hi


def upstox_5m(client, key: str, a: date, b: date) -> list[dict]:  # noqa: ANN001
    rows: list = []
    for w in split_windows((a, b)):
        rows += client.historical_candles(key, w[0], w[1], interval=5)
    new, _ = build_frame(parse_candles(rows), bar_minutes=5)  # same session filter + dedupe as our import
    return [{"time": int(r.time), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
            for r in new.itertuples()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", nargs="+", default=None, help="YYYY-MM (default: the four Nifty sample months)")
    ap.add_argument("--key", default=NIFTY_INDEX_KEY, help="instrument key (default: Nifty 50)")
    args = ap.parse_args(argv)
    client = make_client()
    if client is None:
        print("No UPSTOX_ANALYTICS_TOKEN in .env", file=sys.stderr)
        return 2
    is_nifty = args.key == NIFTY_INDEX_KEY
    months = args.months or (DEFAULT_MONTHS if is_nifty else None)
    if not months:
        print("--months is required for a symbol other than Nifty 50", file=sys.stderr)
        return 2
    args.months = months
    sym = symbol_dir_name(args.key)
    cdir = settings.candles_dir
    store = CandleStore(cdir)
    sample_all = read_parquet(cdir / "NIFTY50" / "5m.parquet") if is_nifty else None
    volume_blocks: list[str] = []
    reports: list[CompareReport] = []
    special: list[str] = []
    for ym in args.months:
        a, b = month_range(ym)
        lo, hi = ts_range(a, b)
        theirs = upstox_5m(client, args.key, a, b)
        src, _ = store.load(sym, from_time=lo, to_time=hi, session_types=SESSION_TYPES)
        ours = resample(src, "5m", 1)
        reports.append(compare_bars(ours, theirs, label=f"{ym}: A. ours (5m from 1m) vs Upstox 5m"))
        volume_blocks.append(render_volume_sums(f"{ym}: daily volume sums, ours vs Upstox 5m", daily_volume_sums(ours, theirs)))
        sample: list[dict] = []
        if sample_all is not None:
            sample_df = sample_all[(sample_all["time"] >= lo) & (sample_all["time"] <= hi)]
            sample = sample_df[["time", "open", "high", "low", "close", "volume"]].to_dict("records")
            reports.append(compare_bars(ours, sample, label=f"{ym}: B. ours (5m from 1m) vs saved 5m sample"))
            reports.append(compare_bars(sample, theirs, label=f"{ym}: C. saved 5m sample vs Upstox 5m"))
        sat = sorted({datetime.fromtimestamp(c["time"], IST).date() for c in ours if datetime.fromtimestamp(c["time"], IST).weekday() >= 5})
        for d in sat:
            n_ours = sum(1 for c in ours if datetime.fromtimestamp(c["time"], IST).date() == d)
            n_up = sum(1 for c in theirs if datetime.fromtimestamp(c["time"], IST).date() == d)
            special.append(f"- {d} ({d:%a}): ours {n_ours} 5m bars, Upstox {n_up} 5m bars")
        print(f"{ym}: ours {len(ours)} bars, Upstox {len(theirs)}, sample {len(sample)}", flush=True)

    body = render_report(reports)
    out = [f"# 5m validation ({sym}): our 5m (resampled from 1m) vs Upstox 5m", "",
           f"Generated {datetime.now(IST).isoformat(timespec='seconds')}; months: {', '.join(args.months)}.",
           "All sessions are compared (normal, weekend_full, special_short, muhurat). Price tolerance 0.005; volume exact.", "",
           "## Weekend / special sessions in these months", "", *(special or ["- none"]), "", "## Results", "", body, "## Volume sums", "", *volume_blocks]
    text = "\n".join(out)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if is_nifty else f"_{sym}"
    path = out_dir / f"5m_vs_upstox{suffix}_{datetime.now(IST).date()}.md"
    path.write_text(text)
    print()
    print(text)
    print(f"\nreport written to {path}")
    return 0 if all(r.ok for r in reports if ": A." in r.label) else 1


if __name__ == "__main__":
    raise SystemExit(main())
