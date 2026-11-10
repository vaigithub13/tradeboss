"""Start the paper slots on the running session backend, the same way the Paper panel's Start buttons do.

    cd backend && .venv/bin/python -m scripts.paper_autostart [--now]

Run in the background by scripts/launchd/session-start.sh. Waits until `start_at_ist` (default 09:16, so the
first 5m bar exists), then POSTs /api/paper/start for each slot in scripts/launchd/paper_slots.json that is not
already running. Slots that fail are retried every 30 s until `give_up_at_ist`. A slot already running (409)
counts as done. Places no orders: paper only. Talks to 127.0.0.1:8000.

Skips Muhurat and other listed special sessions (`normal_trading_day` only). The backend may still start on those
days to record the feed; paper slots stay off unless started by hand. Each POST sets `start_source: "auto"`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.live.session_days import normal_trading_day

IST = timezone(timedelta(hours=5, minutes=30))
BASE = "http://127.0.0.1:8000"
CONFIG = Path(__file__).resolve().parent / "launchd" / "paper_slots.json"
RETRY_S = 30


def log(msg: str) -> None:
    print(f"{datetime.now(IST):%Y-%m-%d %H:%M:%S} IST paper-autostart: {msg}", flush=True)


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for s in cfg.get("slots", []):
        if not {"slot", "strategy"} <= set(s):
            raise ValueError(f"slot entry needs slot and strategy: {s}")
    return cfg


def at_today(hhmm: str, now: datetime) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return now.astimezone(IST).replace(hour=h, minute=m, second=0, microsecond=0)


def running_slots(status: dict[str, Any]) -> set[str]:
    return {str(s.get("slot")) for s in status.get("slots", []) if s.get("state") == "running"}


def to_start(cfg: dict[str, Any], running: set[str], done: set[str]) -> list[dict[str, Any]]:
    return [s for s in cfg.get("slots", []) if str(s["slot"]) not in running and str(s["slot"]) not in done]


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"null")
        except ValueError:
            detail = None
        return e.code, detail


def run(
    cfg: dict[str, Any],
    *,
    request: Callable[[str, str, dict[str, Any] | None], tuple[int, Any]] = _request,
    now: Callable[[], datetime] = lambda: datetime.now(IST),
    sleep: Callable[[float], None] = time.sleep,
    start_now: bool = False,
) -> dict[str, str]:
    """{slot: "started" | "already running" | "failed: ..."}."""
    if not cfg.get("enabled", True):
        log("disabled in paper_slots.json")
        return {}
    today = now().astimezone(IST).date()
    if not normal_trading_day(today):
        log(f"skip: {today.isoformat()} is not a normal trading day (Muhurat / holiday / weekend / special)")
        return {}
    start_at = at_today(cfg.get("start_at_ist", "09:16"), now())
    give_up = at_today(cfg.get("give_up_at_ist", "09:45"), now())
    if not start_now and now() < start_at:
        wait = (start_at - now()).total_seconds()
        log(f"waiting {wait / 60:.0f} min until {start_at:%H:%M} IST")
        sleep(wait)
    results: dict[str, str] = {}
    done: set[str] = set()
    while True:
        try:
            code, status = request("GET", "/api/paper/status", None)
        except (urllib.error.URLError, OSError) as e:
            code, status = 0, None
            log(f"backend not reachable: {type(e).__name__}")
        running = running_slots(status) if code == 200 and isinstance(status, dict) else set()
        for s in cfg.get("slots", []):
            if str(s["slot"]) in running and str(s["slot"]) not in done:
                results[str(s["slot"])] = "already running"
                done.add(str(s["slot"]))
        if code == 200:
            for s in to_start(cfg, running, done):
                body = {
                    "slot": str(s["slot"]),
                    "strategy": s["strategy"],
                    "params": s.get("params", {}),
                    "start_source": "auto",
                }
                try:
                    c, out = request("POST", "/api/paper/start", body)
                except (urllib.error.URLError, OSError) as e:
                    c, out = 0, {"detail": type(e).__name__}
                slot = str(s["slot"])
                if c == 200:
                    results[slot] = "started"
                    done.add(slot)
                    log(f"slot {slot}: started {s['strategy']} {s.get('params', {})}")
                elif c == 409 and "already" in str((out or {}).get("detail", "")).lower():
                    results[slot] = "already running"
                    done.add(slot)
                elif c == 400:
                    results[slot] = f"failed: {(out or {}).get('detail')}"
                    done.add(slot)  # a bad setting will not fix itself
                    log(f"slot {slot}: refused ({(out or {}).get('detail')}) - check paper_slots.json")
                else:
                    results[slot] = f"failed: HTTP {c} {(out or {}).get('detail') if isinstance(out, dict) else ''}"
                    log(f"slot {slot}: {results[slot]} (will retry)")
        if len(done) >= len(cfg.get("slots", [])):
            break
        if now() >= give_up:
            log(f"giving up at {give_up:%H:%M} IST: start the rest from the Paper panel")
            break
        sleep(RETRY_S)
    log("result: " + ", ".join(f"slot {k} {v}" for k, v in sorted(results.items())))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the paper slots on the session backend")
    parser.add_argument("--now", action="store_true", help="do not wait for start_at_ist")
    args = parser.parse_args(argv)
    results = run(load_config(), start_now=args.now)
    return 0 if all(v in ("started", "already running") for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
