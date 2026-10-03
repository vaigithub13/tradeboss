"""Glue: one feed frame updates the strike set and appends depth rows. No second connection."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from app.backtest.expiry import load_default_calendar
from app.backtest.lots import load_default_lot_table, lot_size_for_contract
from app.live.frames import decode_frame
from app.live.model import ist_date
from app.live.spreads.decode import depth_quotes
from app.live.spreads.report import write_report_files
from app.live.spreads.rows import quote_row
from app.live.spreads.select import StrikeSelector, nearest_weekly
from app.live.spreads.writer import SpreadWriter
from app.options.strikes import load_default_step_table
from app.upstox.instruments import NIFTY_INDEX_KEY

log = logging.getLogger("tradeboss.live.spreads")


class SpreadSession:
    def __init__(self, directory: Path, listed) -> None:
        self.writer = SpreadWriter(directory, enabled=True)
        self.selector = StrikeSelector()
        self.listed = listed
        self.steps = load_default_step_table()
        self.lots = load_default_lot_table()
        self.calendar = load_default_calendar()
        self.nifty = 0.0
        self._logged_missing: tuple | None = None
        self.directory = directory

    def keys(self) -> list[str]:
        return self.selector.keys()

    def on_frame(self, raw: bytes, *, market_open: bool) -> None:
        frame = decode_frame(raw)
        for item in frame.items:
            if item.key == NIFTY_INDEX_KEY and item.ltp > 0:
                self.nifty = float(item.ltp)
        if self.nifty > 0 and frame.current_ts:
            self._retarget(frame.current_ts)
        known = self.selector.contracts()
        for quote in depth_quotes(raw):
            self.selector.on_depth(quote.key, quote.ts_ms)
            contract = known.get(quote.key)
            if contract is None or self.nifty <= 0:
                continue
            cycle = self._cycle(contract.expiry)
            lot = lot_size_for_contract(self.lots, "NIFTY", contract.expiry, cycle)
            row = quote_row(
                key=quote.key, ts_ms=quote.ts_ms, levels=quote.levels, expiry=contract.expiry,
                strike=contract.strike, kind=contract.kind, nifty_ltp=self.nifty, lot=lot, cycle=cycle,
            )
            self.writer.append(row, market_open=market_open)

    def flush(self) -> None:
        self.writer.flush()

    def close(self, nifty_bars: list[dict]) -> None:
        self.writer.flush()
        day = self.writer.day
        if day is None:
            return
        try:
            write_report_files(self.directory, day, nifty_bars)
        except Exception as exc:  # noqa: BLE001 - a report must not take the feed down
            log.error("spread report failed: %s", exc)

    def _retarget(self, ts_ms: int) -> None:
        day = ist_date(ts_ms)
        try:
            expiry = nearest_weekly(self.calendar, day)
            step = self.steps.step("NIFTY", day)
        except Exception as exc:  # noqa: BLE001
            log.warning("spread strike selection skipped: %s", exc)
            return
        before = self.selector.missing
        self.selector.on_spot(self.nifty, step, expiry, self.listed(expiry), ts_ms)
        missing = tuple(self.selector.missing)
        if missing and missing != before and missing != self._logged_missing:
            self._logged_missing = missing
            log.info("spread recorder missing %d contracts on %s: %s", len(missing), expiry, missing)

    def _cycle(self, expiry: date) -> str:
        kind = self.calendar.next_expiry(expiry, "weekly").kind
        return kind if kind in ("weekly", "monthly") else "weekly"
