"""Every paper trade, one row each, over the slots and days: what the panel's Trades view shows.

Live day files only (a replay is written apart and never counted), unless `include_replay`. A row is the trade
report (app/exits/report.py) plus the date, slot, lots, Nifty at entry and exit, points moved, gross and charges.
A trade written before these details were stored takes the signal bar, trigger and Nifty entry from its signal
record, and the Nifty at exit from the stored 1m candles (listed in `index_from_candles`).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.exits.report import report_row
from app.exits.rules import ExitRule, parse_exit_rule

IST = timezone(timedelta(hours=5, minutes=30))
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def trade_report_row(t: dict[str, Any], rule: ExitRule | None) -> dict[str, Any]:
    """The trade report row of one paper trade (the day file's `report`)."""
    levels = t.get("levels") or {}
    return {**report_row(
        signal_time=t.get("signal_time"), trigger_index=t.get("trigger_index"),
        fill_time=int(t["entry_at_ms"]) // 1000, contract=t["symbol"], entry_premium=float(t["entry_price"]),
        entry_source="real" if t["entry_source"] == "quote" else str(t["entry_source"]),
        units=int(t["units"]), rule=rule, direction=t["direction"],
        index_entry=t.get("index_entry"), delta=t.get("delta"),
        index_stop=levels.get("index_stop"), index_target=levels.get("index_target"),
        exit_time=int(t["exit_at_ms"]) // 1000, exit_premium=float(t["exit_price"]),
        exit_reason=t["exit_reason"], net=float(t["net"]),
    ), "source": t.get("source", "live")}


def _rule_of(saved: dict[str, Any]) -> ExitRule | None:
    raw = saved.get("exit_rule") or (saved.get("requested_params") or {}).get("exit_rule")
    try:
        return parse_exit_rule(raw)
    except ValueError:
        return None


def _day_files(directory: Path) -> list[tuple[date, Path]]:
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.iterdir()):
        m = DAY_FILE.match(path.name)
        if m:
            out.append((date.fromisoformat(m.group(1)), path))
    return out


def _from_signal(saved: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """An older trade's entry details from the signal that opened it (same fill moment)."""
    for s in saved.get("signals", []):
        if s.get("decided_at_ms") == t["entry_at_ms"] and s.get("side") in ("BUY", "SELL"):
            return {"signal_time": s.get("time"), "trigger_index": s.get("order_price", s.get("index_price")),
                    "index_entry": s.get("index_price")}
    return {}


class _MinuteOpens:
    """The 1m open at a time, from the candle store, one day at a time."""

    def __init__(self, candles: Any, symbol: str) -> None:
        self.candles, self.symbol, self.days = candles, symbol, {}

    def at(self, ts: int) -> float | None:
        if self.candles is None:
            return None
        day = datetime.fromtimestamp(ts, IST).date()
        if day not in self.days:
            start = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
            try:
                bars, _ = self.candles.load(self.symbol, from_time=start, to_time=start + 86_400)
            except Exception:  # noqa: BLE001 - no candles: the cell stays empty
                bars = []
            self.days[day] = {int(b["time"]): float(b["open"]) for b in bars}
        return self.days[day].get(ts - ts % 60)


def trade_rows(
    slot_dirs: dict[str, Path],
    *,
    from_day: date | None = None,
    to_day: date | None = None,
    slot: str | None = None,
    include_replay: bool = False,
    candles: Any = None,
    symbol: str = "NIFTY50",
) -> list[dict[str, Any]]:
    opens = _MinuteOpens(candles, symbol)
    rows: list[dict[str, Any]] = []
    for name, directory in slot_dirs.items():
        if slot is not None and name != slot:
            continue
        places = [directory] + ([directory / "replay"] if include_replay else [])
        for place in places:
            for day, path in _day_files(place):
                if (from_day and day < from_day) or (to_day and day > to_day):
                    continue
                saved = json.loads(path.read_text(encoding="utf-8"))
                file_source = saved.get("source", "live")
                if file_source != "live" and not include_replay:
                    continue
                rule = _rule_of(saved)
                for raw in saved.get("trades", []):
                    t = dict(raw)
                    filled: list[str] = []
                    if t.get("signal_time") is None and t.get("trigger_index") is None:
                        t = {**_from_signal(saved, t), **{k: v for k, v in t.items() if v is not None}}
                    if t.get("index_exit") is None:
                        t["index_exit"] = opens.at(int(t["exit_at_ms"]) // 1000)
                        if t["index_exit"] is not None:
                            filled.append("exit")
                    if t.get("index_entry") is None:
                        t["index_entry"] = opens.at(int(t["entry_at_ms"]) // 1000)
                        if t["index_entry"] is not None:
                            filled.insert(0, "entry")
                    if file_source != "live":
                        t["source"] = file_source
                    rep = trade_report_row(t, rule)
                    ie, ix = t.get("index_entry"), t.get("index_exit")
                    rows.append({
                        **rep,
                        "date": day.isoformat(),
                        "slot": name,
                        "side": t["direction"],
                        "direction": t.get("kind") or ("CE" if t["direction"] == "LONG" else "PE"),
                        "entry_time": rep["fill_time"],
                        "index_entry": ie,
                        "index_exit": ix,
                        "lots": int(t["lots"]),
                        "lot_size": int(t["lot_size"]),
                        "nifty_points": None if ie is None or ix is None else round(float(ix) - float(ie), 2),
                        "premium_points": round(float(t["exit_price"]) - float(t["entry_price"]), 2),
                        "gross": float(t["gross"]),
                        "charges": float(t["charges"]),
                        "index_from_candles": filled,
                    })
    rows.sort(key=lambda r: (r["entry_time"], r["slot"]))
    return rows
