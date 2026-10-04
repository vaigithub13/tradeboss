"""Backfill Nifty futures 1m volume for the index VWAP.

Listed contracts use the same historical sync as every other symbol. Expired contracts use
Upstox's expired-instrument candles and land in the same Parquet layout, so the rate limiter
and the coverage ledger apply to both. History starts 2024-10-01 (Upstox's expired tape).
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from pathlib import Path

from app.backtest.expiry import ExpiryCalendar, load_default_calendar
from app.data.history import (
    SyncResult,
    clip_source_empty,
    merge_window,
    note_source_empty,
    ranges_to_fetch,
    read_meta,
    read_parquet,
    split_windows,
    symbol_dir_name,
    sync_symbol,
    write_meta,
    write_parquet_atomic,
)
from app.data.importer import build_frame
from app.indicators.futures_vwap import HISTORY_FROM
from app.upstox.client import UpstoxClient, parse_candles
from app.upstox.instruments import NIFTY_INDEX_KEY, Instrument, InstrumentIndex

log = logging.getLogger("tradeboss.futures_volume")

LISTED_LOOKBACK_DAYS = 120
_lock = threading.Lock()
_started: set[str] = set()


def monthly_expiries(calendar: ExpiryCalendar, today: date) -> list[date]:
    """Monthly Nifty expiries from 2024-10 through the month after the front contract."""
    front = calendar.next_expiry(today, "monthly")
    following = calendar.next_expiry(front.date, "monthly", skip_expiry_day=True)
    # one month further: after the front rolls, that contract's "next" is needed for the volume test
    horizon = calendar.next_expiry(following.date, "monthly", skip_expiry_day=True).date
    out: list[date] = []
    year, month = HISTORY_FROM.year, HISTORY_FROM.month
    while date(year, month, 1) <= horizon:
        expiry = calendar.monthly_of(year, month).date
        if expiry >= HISTORY_FROM:
            out.append(expiry)
        year, month = year + (month == 12), month % 12 + 1
    return out


def fetch_window(expiry: date) -> tuple[date, date]:
    """Days of 1m volume we keep for one contract: from listing-ish lookback, not before 2024-10-01."""
    start = max(HISTORY_FROM, expiry - timedelta(days=LISTED_LOOKBACK_DAYS))
    return start, expiry


def sync_expired_future(
    client: UpstoxClient,
    candles_dir: Path,
    *,
    expiry: date,
    expired_key: str,
    symbol: str,
    lot_size: int | None = None,
    now_date: date | None = None,
) -> SyncResult:
    """Fetch the missing 1m windows of one expired future. A second call fetches nothing new."""
    start, end = fetch_window(expiry)
    today = now_date or date.today()
    end = min(end, today)
    sdir = candles_dir / symbol_dir_name(expired_key)
    parquet = sdir / "1m.parquet"
    meta = read_meta(sdir)
    meta.instrument = {
        "instrument_key": expired_key,
        "symbol": symbol,
        "name": "NIFTY",
        "kind": "future",
        "segment": "NSE_FO",
        "instrument_type": "FUT",
        "expiry": expiry.isoformat(),
        "strike": None,
        "lot_size": lot_size,
        "underlying_key": NIFTY_INDEX_KEY,
    }
    gaps = ranges_to_fetch(meta.covered, meta.source_empty, start, end, today)
    windows = [w for gap in gaps for w in split_windows(gap)]
    windows.sort(reverse=True)
    fetched = 0
    for window in windows:
        rows = client.expired_historical_candles(expired_key, window[0], window[1])
        new, _ = build_frame(parse_candles(rows), bar_minutes=1, keep_oi=True, in_progress_date=None)
        if len(new) == 0:
            meta.source_empty = note_source_empty(meta.source_empty, window, today)
        else:
            merged = merge_window(read_parquet(parquet), new, window)
            write_parquet_atomic(merged, parquet)
            meta.covered = [*meta.covered, window]
            meta.source_empty = clip_source_empty(meta.source_empty, window)
        write_meta(sdir, meta)
        fetched += 1
    if not windows:
        write_meta(sdir, meta)
    total = int(len(read_parquet(parquet))) if parquet.is_file() else 0
    return SyncResult(sdir.name, fetched, total, False, (start, end))


def _listed(index: InstrumentIndex | None, expiry: date) -> Instrument | None:
    if index is None:
        return None
    matches = [i for i in index.instruments if i.kind == "future" and i.expiry == expiry]
    return matches[0] if matches else None


def backfill_nifty_futures(
    client: UpstoxClient,
    candles_dir: Path,
    index: InstrumentIndex | None,
    *,
    today: date,
    calendar: ExpiryCalendar | None = None,
) -> list[SyncResult]:
    """Live contract via the normal history sync; expired contracts via the expired-candle API."""
    calendar = calendar or load_default_calendar()
    results: list[SyncResult] = []
    for expiry in monthly_expiries(calendar, today):
        listed = _listed(index, expiry)
        if expiry >= today and listed is not None:
            results.append(sync_symbol(client, candles_dir, listed, from_date=fetch_window(expiry)[0], to_date=today))
            continue
        if expiry >= today:
            continue
        rows = client.expired_future_contracts(NIFTY_INDEX_KEY, expiry)
        if not rows:
            log.info("no expired Nifty future for %s", expiry)
            continue
        row = rows[0]
        key = str(row.get("instrument_key") or "")
        if not key:
            continue
        lot = row.get("lot_size")
        results.append(
            sync_expired_future(
                client, candles_dir,
                expiry=expiry, expired_key=key,
                symbol=str(row.get("trading_symbol") or f"NIFTY FUT {expiry.isoformat()}"),
                lot_size=int(lot) if isinstance(lot, int) else None,
                now_date=today,
            )
        )
    return results


def schedule_backfill(candles_dir: Path, today: date | None = None) -> None:
    """Start one background backfill for this candle directory. Further calls do nothing."""
    key = str(candles_dir.resolve()) if candles_dir.exists() else str(candles_dir)
    with _lock:
        if key in _started:
            return
        _started.add(key)

    def run() -> None:
        try:
            from app.upstox.deps import get_client, get_instruments_dir
            from app.upstox.instruments import current_index

            client = get_client()
            if client is None:
                log.info("futures volume backfill skipped: no data token")
                return
            when = today or datetime_today()
            backfill_nifty_futures(client, candles_dir, current_index(get_instruments_dir()), today=when)
            log.info("futures volume backfill finished")
        except Exception:
            log.exception("futures volume backfill failed")

    threading.Thread(target=run, name="futures-volume", daemon=True).start()


def datetime_today() -> date:
    from datetime import datetime

    from app.live.model import IST

    now = datetime.now(IST)
    # a session still open belongs to today; after the close, history through today is finished
    return now.date()


def reset_backfill_schedule() -> None:
    """Tests only."""
    with _lock:
        _started.clear()
