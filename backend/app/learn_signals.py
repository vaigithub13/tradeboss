"""Watch-only Learn signals. Files under data/learn/signals stay untracked.

The detector is the TypeScript engine. This module loads closed 5-minute
candles, runs that engine, and writes the log. It does not place an order
and it does not touch a paper slot.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import ROOT_DIR

log = logging.getLogger("tradeboss.learn.signals")
IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = ("NIFTY50", "SENSEX", "BANKNIFTY")
SETUPS = ("SWP", "FVG", "AMD")
FIELDS = ["id", "symbol", "setup", "side", "time", "entry", "sl", "tp", "risk", "tpNote", "why", "status", "exitTime", "exit", "r"]
_BUNDLE: Path | None = None


def review_wanted() -> bool:
    """The 16:00 thread stays off under pytest."""
    return "pytest" not in sys.modules and "PYTEST_CURRENTTEST" not in os.environ


def review_due(now: datetime) -> bool:
    local = now.astimezone(IST)
    if local.weekday() >= 5:
        return False
    return local.hour > 16 or (local.hour == 16 and local.minute >= 0)


def signals_dir(data_dir: Path) -> Path:
    return data_dir / "learn" / "signals"


def summary_path(data_dir: Path) -> Path:
    return signals_dir(data_dir) / "summary.json"


def _empty_stats() -> dict[str, Any]:
    return {"signals": 0, "tp": 0, "sl": 0, "expired": 0, "winRate": None, "totalR": 0, "avgR": None}


def empty_symbol_book() -> dict[str, Any]:
    return {"all": _empty_stats(), **{name: _empty_stats() for name in SETUPS}}


def empty_summary() -> dict[str, Any]:
    return {
        "live": {"since": None, "bySymbol": {symbol: empty_symbol_book() for symbol in SYMBOLS}},
        "history": None,
    }


def load_summary(data_dir: Path) -> dict[str, Any]:
    path = summary_path(data_dir)
    if not path.is_file():
        return empty_summary()
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        return empty_summary()
    base = empty_summary()
    live = body.get("live")
    if isinstance(live, dict):
        base["live"]["since"] = live.get("since")
        saved = live.get("bySymbol")
        if isinstance(saved, dict):
            for symbol, book in saved.items():
                if isinstance(book, dict):
                    base["live"]["bySymbol"][symbol] = book
    history = body.get("history")
    base["history"] = history if isinstance(history, dict) else None
    return base


def save_summary(data_dir: Path, body: dict[str, Any]) -> None:
    path = summary_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")


def ist_day(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), IST).date().isoformat()


def load_m5(candles_dir: Path, symbol: str, start: int, end: int) -> list[dict[str, float]]:
    """Closed-session 5-minute candles, resampled the same way the chart is."""
    from app.data.service import get_candles
    from app.data.store import CandleStore, SymbolNotFound

    store = CandleStore(candles_dir)
    try:
        result = get_candles(store, symbol, "5m", from_time=start, to_time=end, session_types=("normal",))
    except SymbolNotFound:
        return []
    return [
        {"time": int(bar["time"]), "open": float(bar["open"]), "high": float(bar["high"]), "low": float(bar["low"]), "close": float(bar["close"])}
        for bar in result.candles
    ]


def bundle_engine() -> Path:
    global _BUNDLE
    if _BUNDLE is not None and _BUNDLE.is_file():
        return _BUNDLE
    esbuild = ROOT_DIR / "frontend" / "node_modules" / "esbuild" / "bin" / "esbuild"
    entry = ROOT_DIR / "frontend" / "src" / "learn" / "reviewMain.ts"
    out = Path(tempfile.gettempdir()) / "tradeboss-learn-signals.mjs"
    subprocess.run(
        [str(esbuild), str(entry), "--bundle", "--platform=node", "--format=esm", f"--outfile={out}"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    _BUNDLE = out
    return out


def run_book(bars: list[dict[str, float]], now: int, symbol: str) -> dict[str, Any]:
    """Score closed 5-minute candles with the same engine the chart uses."""
    payload = {"bars": bars, "now": now, "symbol": symbol, "left": 3, "right": 3}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle)
        src = handle.name
    try:
        done = subprocess.run(
            ["node", str(bundle_engine()), src],
            check=True,
            capture_output=True,
            timeout=300,
            text=True,
        )
    finally:
        Path(src).unlink(missing_ok=True)
    return json.loads(done.stdout)


def _stats_of(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if row.get("status") in {"TP", "SL", "expired"}]
    tp = sum(1 for row in rows if row.get("status") == "TP")
    sl = sum(1 for row in rows if row.get("status") == "SL")
    expired = sum(1 for row in rows if row.get("status") == "expired")
    total = round(sum(float(row.get("r") or 0) for row in done), 2)
    return {
        "signals": len(rows),
        "tp": tp,
        "sl": sl,
        "expired": expired,
        "winRate": round(100 * tp / (tp + sl), 2) if tp + sl else None,
        "totalR": total,
        "avgR": round(total / len(done), 2) if done else None,
    }


def stats_book(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"all": _stats_of(rows), **{name: _stats_of([row for row in rows if row.get("setup") == name]) for name in SETUPS}}


def _read_csv(data_dir: Path) -> list[dict[str, str]]:
    path = signals_dir(data_dir) / "signals.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def upsert_signals(data_dir: Path, rows: list[dict[str, Any]]) -> int:
    """Replace a saved signal when the same id is reviewed again."""
    by_id = {row["id"]: row for row in _read_csv(data_dir)}
    wrote = 0
    for row in rows:
        fresh = {key: "" if row.get(key) is None else row.get(key) for key in FIELDS}
        if by_id.get(fresh["id"]) != fresh:
            wrote += 1
        by_id[str(fresh["id"])] = fresh
    path = signals_dir(data_dir) / "signals.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(by_id.values(), key=lambda row: (int(float(row["time"])), row["id"]))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)
    return wrote


def _clock(epoch: int) -> str:
    local = datetime.fromtimestamp(int(epoch), IST)
    return local.strftime("%H:%M")


def write_daily(data_dir: Path, day: date) -> None:
    key = day.isoformat()
    lines = [
        f"# Learn signals {key}",
        "",
        "Watch only. These are not trades. Each one is scored automatically.",
        "",
    ]
    rows = [row for row in _read_csv(data_dir) if ist_day(int(float(row["time"]))) == key]
    if not rows:
        lines.append("No signal was scored today.")
    for symbol in SYMBOLS:
        own = [row for row in rows if row["symbol"] == symbol]
        if not own:
            continue
        lines.append(f"## {symbol}")
        lines.append("")
        for row in own:
            result = row["status"]
            if row["r"] != "":
                result = f"{result} {row['r']}R"
            lines.append(
                f"- {_clock(int(float(row['time'])))} {row['setup']} {row['side']} "
                f"entry {row['entry']} SL {row['sl']} TP {row['tp']} — {row['why']} — {result}"
            )
        lines.append("")
    path = signals_dir(data_dir) / "daily" / f"{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def live_from_csv(data_dir: Path) -> dict[str, Any]:
    rows = _read_csv(data_dir)
    by_symbol = {}
    for symbol in SYMBOLS:
        own = [row for row in rows if row["symbol"] == symbol]
        by_symbol[symbol] = stats_book(own)
    since = None
    if rows:
        since = ist_day(min(int(float(row["time"])) for row in rows))
    previous = load_summary(data_dir)
    if previous["live"].get("since"):
        since = previous["live"]["since"]
    return {"since": since, "bySymbol": by_symbol}


def apply_rows(data_dir: Path, rows: list[dict[str, Any]], day: date) -> dict[str, Any]:
    """Write today's signals and the daily note. Leave the history block alone."""
    upsert_signals(data_dir, rows)
    write_daily(data_dir, day)
    summary = load_summary(data_dir)
    summary["live"] = live_from_csv(data_dir)
    save_summary(data_dir, summary)
    return summary


def _session_end(day: date) -> int:
    return int(datetime.combine(day, time(15, 30), IST).timestamp())


def review_symbol(candles_dir: Path, symbol: str, day: date) -> list[dict[str, Any]]:
    start = int(datetime.combine(day - timedelta(days=14), time(0, 0), IST).timestamp())
    bars = load_m5(candles_dir, symbol, start, _session_end(day))
    if len(bars) < 3:
        return []
    book = run_book(bars, _session_end(day), symbol)
    key = day.isoformat()
    return [row for row in book.get("signals", []) if ist_day(int(row["time"])) == key]


def review_day(data_dir: Path, candles_dir: Path, day: date) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        rows.extend(review_symbol(candles_dir, symbol, day))
    return apply_rows(data_dir, rows, day)


def write_history(data_dir: Path, candles_dir: Path, start: date, end: date) -> dict[str, Any]:
    """Score 5-minute history once. The forward log is not replaced."""
    summary = load_summary(data_dir)
    by_symbol: dict[str, Any] = {}
    start_epoch = int(datetime.combine(start, time(0, 0), IST).timestamp())
    end_epoch = _session_end(end)
    for symbol in SYMBOLS:
        bars = load_m5(candles_dir, symbol, start_epoch, end_epoch)
        if len(bars) < 3:
            by_symbol[symbol] = empty_symbol_book()
            continue
        book = run_book(bars, int(bars[-1]["time"]) + 300, symbol)
        stats = book.get("stats") or {}
        by_symbol[symbol] = {
            "all": _from_engine(stats.get("all")),
            **{name: _from_engine(stats.get(name)) for name in SETUPS},
        }
    summary["history"] = {
        "label": "history (no costs)",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "bySymbol": by_symbol,
    }
    save_summary(data_dir, summary)
    return summary


def _from_engine(stats: Any) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return _empty_stats()
    return {
        "signals": stats.get("signals", 0),
        "tp": stats.get("tp", 0),
        "sl": stats.get("sl", 0),
        "expired": stats.get("expired", 0),
        "winRate": stats.get("winRate"),
        "totalR": stats.get("totalR", 0),
        "avgR": stats.get("avgR"),
    }


class LearnSignalSchedule:
    """Wakes on the local clock and reviews the session after 16:00 IST."""

    def __init__(self, data_dir: Path, candles_dir: Path) -> None:
        self._data = data_dir
        self._candles = candles_dir
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._done: date | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="learn-signals", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = datetime.now(IST)
                if review_due(now) and self._done != now.date():
                    review_day(self._data, self._candles, now.date())
                    self._done = now.date()
            except Exception:
                log.exception("learn signal review failed")
            self._stop.wait(30)
