"""Live scanner state on disk under data/scanner. Replay files stay in replay/ and are not the live book."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.scanner.alerts import cutoff_message, flat_message
from app.scanner.bars import IST, LAST_BAR_MIN, day_of, minute_of
from app.scanner.book import flatten
from app.scanner.clock import actions_due, next_wake
from app.scanner.config import ScannerSettings, load_settings, save_settings
from app.scanner.runner import (
    apply_until,
    build_report,
    expire_untriggered,
    fresh_state,
    merge_signals,
    note_alerts,
    note_entries,
    proven_pairs,
    scan_feed,
    walk_day,
)
from app.scanner.backtest import rank_pairs
from app.scanner.universe import load_universes, resolve_universe, universe_names

HEATMAP_EMPTY: list[dict[str, Any]] = []


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class ScannerService:
    def __init__(self, root: Path, *, feed=None, store=None, instruments_dir: Path | None = None) -> None:
        self.root = root
        self.feed = feed  # test double: () -> (symbols, sectors, nifty, unmapped, day, now)
        self.store = store
        self.instruments_dir = instruments_dir
        self._state: dict[str, Any] | None = None
        self._executor: Any = None

    def _run_tm(self, spec: dict) -> dict:
        """Run one Time Machine outside the request thread when we are not under pytest."""
        from app.scanner.timemachine import run_time_machine

        pool = self._pool()
        if pool is None:
            return run_time_machine(spec)
        try:
            return pool.submit(run_time_machine, spec).result(timeout=120)
        except Exception:
            return run_time_machine(spec)

    def _pool(self) -> Any:
        if not scheduler_wanted():
            return None
        if self._executor is None:
            from concurrent.futures import ProcessPoolExecutor

            self._executor = ProcessPoolExecutor(max_workers=2)
        return self._executor

    # ---------------------------------------------------------------- settings / files
    @property
    def settings_path(self) -> Path:
        return self.root / "settings.json"

    def settings(self) -> ScannerSettings:
        return load_settings(self.settings_path)

    def update_settings(self, raw: dict) -> ScannerSettings:
        if raw.get("auto_trade") is True:
            ready, reason = replay_gate(self.root)
            if not ready:
                raise ValueError(reason)
        current = self.settings().to_dict()
        current.update(raw)
        updated = ScannerSettings.from_dict(current)
        save_settings(self.settings_path, updated)
        return updated

    def rankings(self) -> dict | None:
        return _read(self.root / "rankings" / "latest.json")

    def _day_path(self, day: date) -> Path:
        return self.root / "days" / f"{day.isoformat()}.json"

    def _load_state(self, day: date) -> dict[str, Any]:
        if self._state is not None and self._state.get("day") == day.isoformat() and self._state.get("source") == "live":
            return self._state
        saved = _read(self._day_path(day))
        if saved and saved.get("source", "live") == "live":
            saved.setdefault("applied", {})
            saved.setdefault("alerts", [])
            saved.setdefault("open", [])
            saved.setdefault("trades", [])
            saved.setdefault("signals", [])
            saved.setdefault("net", 0.0)
            self._state = saved
            return saved
        self._state = fresh_state(day)
        return self._state

    def _save_state(self, state: dict[str, Any]) -> None:
        if state.get("source") == "replay":
            _write(self.root / "replay" / f"{state['day']}.json", state)
            return
        _write(self._day_path(date.fromisoformat(state["day"])), state)
        self._state = state

    # ---------------------------------------------------------------- market data
    def _live_feed(self, now: datetime):
        if self.feed is not None:
            return self.feed(now)
        from app.data.service import get_candles
        from app.data.store import SymbolNotFound
        from app.upstox.instruments import current_index

        settings = self.settings()
        index = current_index(self.instruments_dir) if self.instruments_dir else None
        try:
            resolved, unmapped = resolve_universe(index, settings.universe)
        except KeyError:
            resolved, unmapped = [], []
        start = int((now - timedelta(days=120)).timestamp())
        end = int(now.timestamp())
        symbols: dict[str, list] = {}
        sectors: dict[str, str] = {}
        self._folders: dict[str, str] = {}
        if self.store is not None:
            for stock in resolved:
                try:
                    result = get_candles(self.store, stock.folder, "5m", from_time=start, to_time=end)
                except SymbolNotFound:
                    unmapped.append(stock.symbol)
                    continue
                symbols[stock.symbol] = result.candles
                sectors[stock.symbol] = stock.sector
                self._folders[stock.symbol] = stock.folder
            try:
                nifty = get_candles(self.store, "NIFTY50", "5m", from_time=start, to_time=end).candles
            except SymbolNotFound:
                nifty = []
        else:
            nifty = []
        return symbols, sectors, nifty, unmapped

    # ---------------------------------------------------------------- actions
    def scan(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(IST)).astimezone(IST)
        symbols, sectors, nifty, unmapped = self._live_feed(now)
        day = now.date()
        state = self._load_state(day)
        settings = self.settings()
        proven = proven_pairs(self.rankings())
        as_of = int(now.timestamp())
        apply_until(state, symbols, day, as_of, settings)
        rows = scan_feed(
            symbols,
            sectors,
            nifty,
            day=day,
            as_of=as_of,
            settings=settings,
            state=state,
            proven=proven,
            folders=getattr(self, "_folders", None),
            tm=self._run_tm,
        )
        merge_signals(state, rows)
        note_alerts(state, rows, [])
        taken = take_from(state, settings)
        note_entries(state, taken)
        state["last_scan"] = now.isoformat()
        state["unmapped"] = unmapped
        self._save_state(state)
        return self.status(now)

    def flatten_now(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(IST)).astimezone(IST)
        symbols, _sectors, _nifty, _unmapped = self._live_feed(now)
        state = self._load_state(now.date())
        settings = self.settings()
        apply_until(state, symbols, now.date(), int(now.timestamp()), settings)
        last: dict[str, tuple[float, int]] = {}
        for symbol, bars in symbols.items():
            today = [b for b in bars if day_of(int(b["time"])) == now.date() and minute_of(int(b["time"])) <= LAST_BAR_MIN]
            if today:
                last[symbol] = (float(today[-1]["close"]), int(today[-1]["time"]))
        closed = flatten(state, last, settings)
        from app.scanner.runner import _mark_closed

        _mark_closed(state, closed)
        note_alerts(state, [], closed)
        state["alerts"].append({"id": f"flat:{state['day']}", "kind": "flat", "message": flat_message(float(state["net"]))})
        self._save_state(state)
        return self.status(now)

    def mark_cutoff(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(IST)).astimezone(IST)
        state = self._load_state(now.date())
        if state.get("cutoff_sent"):
            return
        expire_untriggered(state)
        state["alerts"].append({"id": f"cutoff:{state['day']}", "kind": "cutoff", "message": cutoff_message()})
        state["cutoff_sent"] = True
        self._save_state(state)

    def write_report(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(IST)).astimezone(IST)
        state = self._load_state(now.date())
        report = build_report(state)
        state["report"] = report
        state["reported"] = True
        self._save_state(state)
        _write(self.root / "reports" / f"{state['day']}.json", report)
        return report

    def replay(self, day: date, symbols: dict, sectors: dict, nifty: list, settings: ScannerSettings | None = None) -> dict:
        settings = settings or self.settings()
        try:
            state = walk_day(
                symbols,
                sectors,
                nifty,
                day=day,
                settings=settings,
                proven=proven_pairs(self.rankings()),
                source="replay",
            )
        except Exception as exc:
            failed = {
                "day": day.isoformat(),
                "source": "replay",
                "completed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "code_version": _code_version(),
            }
            _write(self.root / "replay" / f"{day.isoformat()}.json", failed)
            raise
        _write(self.root / "replay" / f"{day.isoformat()}.json", state)
        return state

    def run_rank(self, by_symbol: dict[str, list] | None = None) -> dict:
        settings = self.settings()
        if by_symbol is None:
            symbols, _sectors, _nifty, _unmapped = self._live_feed(datetime.now(IST))
            by_symbol = symbols
        payload = {
            "as_of": datetime.now(IST).date().isoformat(),
            "pairs": rank_pairs(by_symbol, settings),
        }
        _write(self.root / "rankings" / "latest.json", payload)
        return payload

    def tick(self, now: datetime | None = None) -> list[str]:
        """One scheduler pass. Returns the actions it ran."""
        now = (now or datetime.now(IST)).astimezone(IST)
        state = self._load_state(now.date())
        last = datetime.fromisoformat(state["last_scan"]) if state.get("last_scan") else None
        due = actions_due(
            now,
            last_scan=last,
            flattened=bool(state.get("flattened")),
            reported=bool(state.get("reported")),
            cutoff_sent=bool(state.get("cutoff_sent")),
        )
        for action in due:
            if action == "scan":
                self.scan(now)
            elif action == "cutoff":
                self.mark_cutoff(now)
            elif action == "flat":
                self.flatten_now(now)
            elif action == "report":
                self.write_report(now)
        return due

    # ---------------------------------------------------------------- reads
    def status(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(IST)).astimezone(IST)
        settings = self.settings()
        state = self._load_state(now.date())
        names = universe_names()
        label = settings.universe
        try:
            spec = load_universes()
            label = spec["universes"][settings.universe]["label"]
        except (KeyError, OSError):
            pass
        loss_used = max(0.0, -float(state.get("net") or 0))
        trades_used = len(state.get("trades") or []) + len(state.get("open") or [])
        return {
            "now": now.isoformat(),
            "trading_day": _trading(now.date()),
            "universe": settings.universe,
            "universe_label": label,
            "universes": list(names),
            "unmapped": state.get("unmapped") or [],
            "net": state.get("net") or 0,
            "trades_used": trades_used,
            "max_trades": settings.max_trades_per_day,
            "loss_used": loss_used,
            "daily_loss_limit": settings.daily_loss_limit,
            "minutes_to_cutoff": _minutes_until(now, 14, 30),
            "minutes_to_flat": _minutes_until(now, 15, 15),
            "auto_trade": settings.auto_trade,
            "alerts": state.get("alerts") or [],
            "open": state.get("open") or [],
        }

    def signals(self, day: date | None = None) -> list[dict]:
        day = day or datetime.now(IST).date()
        state = self._load_state(day)
        return list(state.get("signals") or [])

    def signal(self, signal_id: str, day: date | None = None) -> dict | None:
        for row in self.signals(day):
            if row["id"] == signal_id:
                return row
        return None

    def heatmap(self, now: datetime | None = None) -> list[dict]:
        now = (now or datetime.now(IST)).astimezone(IST)
        symbols, sectors, nifty, _unmapped = self._live_feed(now)
        from app.scanner.context import context_row, nifty_change_pct
        from app.scanner.bars import split_sessions
        from app.scanner.runner import _prev_close

        nifty_pct = nifty_change_pct(nifty, now.date(), _prev_close(nifty, now.date()) if nifty else None) if nifty else None
        rows = []
        for symbol, bars in symbols.items():
            sessions = split_sessions(bars)
            prior = [s for s in sessions if s and day_of(int(s[0]["time"])) < now.date()]
            today = [b for b in bars if day_of(int(b["time"])) == now.date() and int(b["time"]) + 300 <= int(now.timestamp())]
            if not today:
                continue
            rows.append(
                context_row(
                    symbol=symbol,
                    sector=sectors.get(symbol, ""),
                    today=today,
                    prior_sessions=prior,
                    prev_close=_prev_close(bars, now.date()),
                    nifty_pct=nifty_pct,
                )
            )
        rows.sort(key=lambda r: float(r["change_pct"] or 0), reverse=True)
        return rows

    def trades(self, day: date | None = None) -> list[dict]:
        day = day or datetime.now(IST).date()
        return list(self._load_state(day).get("trades") or [])

    def report(self, day: date | None = None) -> dict | None:
        day = day or datetime.now(IST).date()
        saved = _read(self.root / "reports" / f"{day.isoformat()}.json")
        if saved:
            return saved
        state = self._load_state(day)
        if state.get("report"):
            return state["report"]
        if state.get("signals") or state.get("trades"):
            return build_report(state)
        return None


def take_from(state: dict, settings: ScannerSettings) -> list[dict]:
    from app.scanner.book import take_signals

    return take_signals(state["signals"], settings, state)


def _trading(day: date) -> bool:
    from app.live.session_days import normal_trading_day

    return normal_trading_day(day)


def _minutes_until(now: datetime, hour: int, minute: int) -> int | None:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (target - now).total_seconds() / 60
    if delta < 0:
        return 0
    return int(delta)


def replay_gate(root: Path) -> tuple[bool, str]:
    """Live auto-take may turn on only after a finished replay from this code version."""
    folder = root / "replay"
    if not folder.is_dir():
        return False, "no completed replay for this code version"
    version = _code_version()
    saw_old = False
    saw_error = False
    saw_unfinished = False
    for path in sorted(folder.glob("*.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if body.get("error"):
            saw_error = True
            continue
        if body.get("code_version") != version:
            saw_old = True
            continue
        if not body.get("completed"):
            saw_unfinished = True
            continue
        return True, ""
    if saw_old:
        return False, "replay was made with an older scanner"
    if saw_error:
        return False, "replay ended with an error"
    if saw_unfinished:
        return False, "replay did not finish"
    return False, "no completed replay for this code version"


def _code_version() -> str:
    from app.scanner.version import SCANNER_CODE_VERSION

    return SCANNER_CODE_VERSION


def ranking_is_stale(path: Path, today: date) -> bool:
    saved = _read(path)
    if not saved or not saved.get("as_of"):
        return True
    as_of = date.fromisoformat(saved["as_of"])
    return as_of < _last_saturday(today)


def _last_saturday(day: date) -> date:
    return day - timedelta(days=(day.weekday() - 5) % 7)


async def scanner_loop(service: ScannerService) -> None:
    """Sleep until the next clock mark. The local clock only wakes the job."""
    import asyncio

    while True:
        now = datetime.now(IST)
        try:
            await asyncio.to_thread(service.tick, now)
        except Exception:
            import logging

            logging.getLogger("tradeboss.scanner").exception("scanner tick failed")
        wake = next_wake(datetime.now(IST))
        delay = max(1.0, (wake - datetime.now(IST)).total_seconds())
        await asyncio.sleep(min(delay, 60.0))


def scheduler_wanted() -> bool:
    """The clock loop stays off under pytest. Production does not import pytest."""
    import sys

    return "pytest" not in sys.modules and "PYTEST_CURRENTTEST" not in os.environ


def scanner_live_keys() -> list[str]:
    """Equity keys for the one shared feed. Empty when live subscribe is off or a name did not resolve."""
    from app.config import settings as app_settings
    from app.upstox.instruments import current_index

    cfg = load_settings(app_settings.data_dir / "scanner" / "settings.json")
    if not cfg.live_subscribe:
        return []
    try:
        resolved, _unmapped = resolve_universe(current_index(app_settings.instruments_dir), cfg.universe)
    except KeyError:
        return []
    return [stock.instrument_key for stock in resolved]


def kickoff_rank_if_stale(service: ScannerService) -> None:
    import threading

    path = service.root / "rankings" / "latest.json"
    if not ranking_is_stale(path, datetime.now(IST).date()):
        return
    threading.Thread(target=service.run_rank, name="scanner-rank", daemon=True).start()
