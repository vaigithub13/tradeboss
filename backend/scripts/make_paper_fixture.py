"""Build the committed paper-day fixture (tests/fixtures/paper_day/) from the local data/ folder.

One recorded session, trimmed to what the paper-vs-backtest parity test needs, so that test runs on a clean
checkout (M5, review 2026-10-11):

* recording.bin.gz - the day's received feed frames, re-encoded with only what the paper pipeline reads, gzipped:
  - the Nifty index: its last trade when it changed, its OHLC block (the 1-minute I1 bar) only when that bar
    changed;
  - India VIX: only the frames where its I1 bar changed (paper reads VIX from the 1-minute bars);
  - the option contracts the chosen slots traded, from 90 s before each trade's entry to 90 s after its exit:
    last trade and the best bid/ask level (a paper fill reads level 1), only when they changed or 10 s after the
    last kept copy (a quote older than 60 s is no quote). Outside those windows paper has no use for the
    contract; a replay that wanted it there would fall back to the model and the parity test would fail;
  - market-info frames as they were;
* candles/NIFTY50 and candles/NSE_INDEX_India_VIX - 1m bars from 16 calendar days before the day through the day
  (enough for the 500-bar 5m warm-up the backtest and paper both use);
* options/NIFTY_<expiry>.parquet - the traded contracts' 1m bars on the day (the backtest's real premiums);
* manifest.json - the day, the slots (strategy and params) and their live trades, the symbol -> key map.

Market data only: no token, no account data, no request headers (the recorder keeps none of those, and only
received frames are copied). Run from backend/:

    uv run python -m scripts.make_paper_fixture --day 2026-10-09 --slot 1 --slot 4
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from app.config import settings
from app.live.recorder import BINARY, MAGIC, RECV, _REC, iter_records, recording_path
from app.options.history import default_history_store
from app.paper.desk import PaperDesk
from app.paper.store import load_day
from app.upstox.feed import MarketDataFeedV3_pb2 as pb
from app.upstox.instruments import NIFTY_INDEX_KEY, VIX_KEY

IST = timezone(timedelta(hours=5, minutes=30))
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "paper_day"
WARMUP_DAYS = 16


def _q(path: Path) -> str:
    return str(path).replace("'", "''")


def _copy_rows(src: Path, dst: Path, where: str) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT * FROM read_parquet('{_q(src)}') WHERE {where} ORDER BY time) "
                    f"TO '{_q(dst)}' (FORMAT PARQUET)")
        row = con.execute(f"SELECT count(*) FROM read_parquet('{_q(dst)}')").fetchone()
    finally:
        con.close()
    return 0 if row is None else int(row[0])


def _i1_key(ohlc: "pb.MarketOHLC") -> tuple | None:
    for o in ohlc.ohlc:
        if o.interval == "I1":
            return (o.ts, o.open, o.high, o.low, o.close, o.vol)
    return None


QUOTE_REFRESH_MS = 10_000


WINDOW_MS = 90_000


def _trim_recording(src: Path, dst: Path, keep: set[str], windows: dict[str, list[tuple[int, int]]]) -> tuple[int, int]:
    kept = total = 0
    last_tick: dict[str, tuple] = {}
    last_i1: dict[str, tuple | None] = {}
    last_quote: dict[str, tuple[tuple, int]] = {}
    with gzip.open(dst, "wb", compresslevel=9) as out:
        out.write(MAGIC)
        for rec in iter_records(src):
            if rec.direction != RECV or rec.kind != BINARY:
                continue
            total += 1
            msg = pb.FeedResponse()
            msg.ParseFromString(rec.payload)
            now = int(msg.currentTs)
            for key in list(msg.feeds):
                feed = msg.feeds[key]
                if key not in keep:
                    del msg.feeds[key]
                elif key in (NIFTY_INDEX_KEY, VIX_KEY):
                    i = feed.fullFeed.indexFF
                    i1 = _i1_key(i.marketOHLC)
                    if i1 == last_i1.get(key):
                        tick = (i.ltpc.ltp, i.ltpc.ltt)
                        if key == VIX_KEY or last_tick.get(key) == tick:
                            del msg.feeds[key]
                            continue
                        i.ClearField("marketOHLC")  # unchanged: the last trade is enough
                    else:
                        last_i1[key] = i1
                    last_tick[key] = (i.ltpc.ltp, i.ltpc.ltt)
                else:
                    if not any(a - WINDOW_MS <= now <= b + WINDOW_MS for a, b in windows.get(key, [])):
                        del msg.feeds[key]
                        continue
                    m = feed.fullFeed.marketFF
                    for name in ("marketOHLC", "optionGreeks"):
                        if m.HasField(name):
                            m.ClearField(name)
                    while len(m.marketLevel.bidAskQuote) > 1:
                        del m.marketLevel.bidAskQuote[-1]
                    top = m.marketLevel.bidAskQuote[0] if m.marketLevel.bidAskQuote else None
                    state = (m.ltpc.ltp, m.ltpc.ltt, None if top is None else (top.bidP, top.askP))
                    seen = last_quote.get(key)
                    if seen is not None and seen[0] == state and now - seen[1] < QUOTE_REFRESH_MS:
                        del msg.feeds[key]
                        continue
                    last_quote[key] = (state, now)
            if not msg.feeds and not msg.HasField("marketInfo"):
                continue
            payload = msg.SerializeToString()
            out.write(_REC.pack(RECV, BINARY, rec.wall_ms, len(payload)) + payload)
            kept += 1
    return kept, total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--day", required=True, type=date.fromisoformat)
    ap.add_argument("--slot", action="append", required=True, help="paper slot whose live day file is used")
    args = ap.parse_args()
    day: date = args.day

    slots, keys, symbols = [], {NIFTY_INDEX_KEY, VIX_KEY}, {}
    windows: dict[str, list[tuple[int, int]]] = {}
    expiries: set[str] = set()
    for slot in args.slot:
        saved = load_day(PaperDesk.slot_dir(settings.data_dir / "paper", slot), day)
        if saved is None or saved.get("source", "live") != "live":
            raise SystemExit(f"slot {slot}: no live day file for {day}")
        trades = [t for t in saved["trades"] if t.get("source", "live") == "live"]
        for t in trades:
            keys.add(t["key"])
            symbols[t["symbol"]] = t["key"]
            windows.setdefault(t["key"], []).append((int(t["entry_at_ms"]), int(t["exit_at_ms"])))
            expiries.add(t["expiry"])
        slots.append({
            "slot": slot, "strategy": saved["requested_strategy"], "params": saved["requested_params"],
            "uses_stops": bool(saved.get("uses_stops")),
            "live_trades": [{"symbol": t["symbol"], "direction": t["direction"], "entry_at_ms": t["entry_at_ms"],
                             "exit_at_ms": t["exit_at_ms"], "exit_reason": t["exit_reason"]} for t in trades],
        })

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    kept, total = _trim_recording(recording_path(settings.feed_recordings_dir, day), OUT / "recording.bin.gz", keys,
                                  windows)

    start = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    lo, hi = start - WARMUP_DAYS * 86_400, start + 86_400 - 1
    counts = {}
    for symbol in ("NIFTY50", "NSE_INDEX_India_VIX"):
        src_dir = settings.candles_dir / symbol
        counts[symbol] = _copy_rows(src_dir / "1m.parquet", OUT / "candles" / symbol / "1m.parquet",
                                    f"time BETWEEN {lo} AND {hi}")
        if (src_dir / "meta.json").exists():
            meta = json.loads((src_dir / "meta.json").read_text())
            meta["covered_1m"] = [[(day - timedelta(days=WARMUP_DAYS)).isoformat(), day.isoformat()]]  # this slice
            (OUT / "candles" / symbol / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")

    store = default_history_store()
    wanted = []
    for name in symbols:
        parts = name.split()  # NIFTY 22400 CE 13 OCT 26
        wanted.append(f"(strike = {float(parts[1])} AND kind = '{parts[2]}')")
    for expiry in sorted(expiries):
        src = store.base_dir / f"NIFTY_{expiry}.parquet"
        counts[f"NIFTY_{expiry}"] = _copy_rows(src, OUT / "options" / src.name,
                                               f"time BETWEEN {start} AND {hi} AND ({' OR '.join(wanted)})")

    manifest = {
        "day": day.isoformat(), "slots": slots, "keys": sorted(keys), "symbols": symbols,
        "frames": {"kept": kept, "recorded": total}, "rows": counts,
        "built_by": "uv run python -m scripts.make_paper_fixture " + " ".join(
            ["--day", day.isoformat()] + [f"--slot {s}" for s in args.slot]),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"wrote {OUT} ({size / 1e6:.2f} MB): frames {kept} of {total}, rows {counts}")


if __name__ == "__main__":
    main()
