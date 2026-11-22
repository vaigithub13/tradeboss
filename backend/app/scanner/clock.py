"""When the scanner wakes. Bar decisions use exchange timestamps; this clock only schedules."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from app.live.session_days import normal_trading_day
from app.scanner.bars import IST

SCAN_START = time(9, 30)
SCAN_END = time(14, 30)
FLAT_AT = time(15, 15)
REPORT_AT = time(15, 35)


def _on(day_dt: datetime, at: time) -> datetime:
    return day_dt.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)


def scan_bucket(now: datetime) -> datetime | None:
    """The 5-minute mark that a scan at `now` belongs to, or None outside 09:30–14:30."""
    local = now.astimezone(IST)
    if not normal_trading_day(local.date()):
        return None
    if local.time() < SCAN_START or local.time() > SCAN_END:
        return None
    minute = (local.minute // 5) * 5
    bucket = local.replace(minute=minute, second=0, microsecond=0)
    if bucket.time() < SCAN_START:
        return None
    return bucket


def actions_due(now: datetime, *, last_scan: datetime | None, flattened: bool, reported: bool, cutoff_sent: bool) -> list[str]:
    local = now.astimezone(IST)
    if not normal_trading_day(local.date()):
        return []
    out: list[str] = []
    bucket = scan_bucket(local)
    if bucket is not None and (last_scan is None or last_scan.astimezone(IST) < bucket):
        out.append("scan")
    if local.time() >= SCAN_END and not cutoff_sent:
        out.append("cutoff")
    if local.time() >= FLAT_AT and not flattened:
        out.append("flat")
    if local.time() >= REPORT_AT and not reported:
        out.append("report")
    return out


def next_wake(now: datetime) -> datetime:
    """Next moment the loop should look again. Holidays and weekends sleep until the next morning."""
    local = now.astimezone(IST)
    if normal_trading_day(local.date()):
        marks = [SCAN_START, time(14, 30), FLAT_AT, REPORT_AT]
        # also the next 5-minute boundary during the scan window
        if SCAN_START <= local.time() < SCAN_END:
            nxt_min = ((local.minute // 5) + 1) * 5
            hour = local.hour
            if nxt_min >= 60:
                nxt_min -= 60
                hour += 1
            candidate = local.replace(hour=hour, minute=nxt_min, second=0, microsecond=0)
            if candidate > local:
                return candidate
        for mark in marks:
            candidate = _on(local, mark)
            if candidate > local:
                return candidate
    nxt = (local + timedelta(days=1)).replace(hour=8, minute=45, second=0, microsecond=0)
    while not normal_trading_day(nxt.date()):
        nxt += timedelta(days=1)
    return nxt
