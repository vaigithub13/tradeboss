"""Replay the last 20 full sessions and write a read-only outcome report.

Does not change settings, thresholds, or the live book. Each triggered signal is
walked on its own, as if it had been taken, even when several overlap.
Run from backend/: python -m scripts.scanner_replay_report
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

from app.config import settings as cfg
from app.data.service import get_candles
from app.live.session_days import normal_trading_day
from app.routes.candles import get_store
from app.scanner.bars import IST, day_of, minute_of, session_for
from app.scanner.history import walk
from app.scanner.service import ScannerService

FINISH = {"target": "target", "stop": "stop", "time": "15:15"}


def full_sessions(store, *, limit: int) -> list:
    now = datetime.now(IST)
    page = get_candles(store, "NIFTY50", "5m", from_time=int(now.timestamp()) - 120 * 86400, to_time=int(now.timestamp()))
    by_day: dict = {}
    for bar in page.candles:
        by_day.setdefault(day_of(int(bar["time"])), []).append(bar)
    full = []
    for day, bars in by_day.items():
        mins = {minute_of(int(b["time"])) for b in bars}
        if normal_trading_day(day) and (9 * 60 + 15) in mins and any(m >= 15 * 60 + 10 for m in mins):
            full.append(day)
    return sorted(full)[-limit:]


def _finish(row: dict, bars: list, day) -> dict | None:
    if row.get("status") not in ("triggered", "in_trade", "target", "stop", "time_exit"):
        return None
    if row.get("entry") is None or row.get("stop") is None or row.get("target") is None:
        return None
    after = [b for b in session_for(bars, day) if int(b["time"]) > int(row["entry_time"])]
    reason, _px, r = walk(
        after,
        side=row["side"],
        entry=float(row["entry"]),
        stop=float(row["stop"]),
        target=float(row["target"]),
        target_r=float(row.get("target_r") or 2),
    )
    return {
        "day": day.isoformat(),
        "symbol": row["symbol"],
        "setup": row["setup"],
        "verdict": row.get("verdict") or "none",
        "history_source": row.get("history_source") or "stock",
        "finish": FINISH.get(reason, reason),
        "r": float(r),
        "target_pct": row.get("target_pct"),
        "stop_pct": row.get("stop_pct"),
        "open_pct": row.get("open_pct"),
    }


def _avg(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _block(title: str, rows: list[dict]) -> list[str]:
    lines = [f"### {title}", ""]
    if not rows:
        lines.append("No triggered signals.")
        lines.append("")
        return lines
    lines.append("| Group | Signals | Target | Stop | 15:15 | Win rate | Avg R | Target-first % | Stop-first % | Open at 15:15 % |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row["key"])].append(row)
    for key in sorted(groups):
        chunk = groups[key]
        n = len(chunk)
        finishes = Counter(item["finish"] for item in chunk)
        wins = finishes.get("target", 0)
        rs = [float(item["r"]) for item in chunk]
        tp = [float(item["target_pct"]) for item in chunk if item.get("target_pct") is not None]
        sp = [float(item["stop_pct"]) for item in chunk if item.get("stop_pct") is not None]
        op = [float(item["open_pct"]) for item in chunk if item.get("open_pct") is not None]
        lines.append(
            "| {key} | {n} | {t} | {s} | {f} | {wr:.1%} | {ar:.2f} | {tp} | {sp} | {op} |".format(
                key=key,
                n=n,
                t=finishes.get("target", 0),
                s=finishes.get("stop", 0),
                f=finishes.get("15:15", 0),
                wr=wins / n,
                ar=_avg(rs) or 0,
                tp="—" if _avg(tp) is None else f"{_avg(tp):.1f}",
                sp="—" if _avg(sp) is None else f"{_avg(sp):.1f}",
                op="—" if _avg(op) is None else f"{_avg(op):.1f}",
            )
        )
    lines.append("")
    return lines


def main() -> None:
    now = datetime.now(IST)
    if now.time() < time(16, 10):
        raise SystemExit("refusing to run before 16:10 IST")
    store = get_store()
    days = full_sessions(store, limit=20)
    if not days:
        raise SystemExit("no full stored sessions")
    svc = ScannerService(cfg.data_dir / "scanner", store=store, instruments_dir=cfg.instruments_dir)
    sim = replace(svc.settings(), auto_trade=False)
    taken: list[dict] = []
    day_lines = ["| Day | GOOD | COIN FLIP | AVOID | Expired |", "|---|---:|---:|---:|---:|"]
    for day in days:
        when = datetime.combine(day, time(15, 35), tzinfo=IST)
        symbols, sectors, nifty, unmapped = svc._live_feed(when)
        state = svc.replay(day, symbols, sectors, nifty, sim)
        counts = Counter((row.get("verdict") or "none") for row in state["signals"] if row.get("status") != "expired")
        expired = sum(1 for row in state["signals"] if row.get("status") == "expired")
        day_lines.append(
            f"| {day.isoformat()} | {counts.get('GOOD', 0)} | {counts.get('COIN_FLIP', 0)} | {counts.get('AVOID', 0)} | {expired} |"
        )
        print(day_lines[-1], flush=True)
        for row in state["signals"]:
            outcome = _finish(row, symbols.get(row["symbol"], []), day)
            if outcome is not None:
                taken.append(outcome)
        if unmapped:
            print(f"  unmapped {len(unmapped)}", flush=True)
    tagged = []
    for row in taken:
        tagged.append({**row, "key": row["verdict"]})
    by_setup = [{**row, "key": row["setup"]} for row in taken]
    by_source = [{**row, "key": row["history_source"]} for row in taken]
    by_verdict_setup = [{**row, "key": f"{row['verdict']} · {row['setup']}"} for row in taken]
    by_verdict_source = [{**row, "key": f"{row['verdict']} · {row['history_source']}"} for row in taken]
    lines = [
        f"# Scanner replay, last {len(days)} sessions",
        "",
        "Thresholds unchanged. Auto-take stayed off. Each triggered signal is walked on its own bar path,",
        "including names that would have overlapped. A win is a target exit. Average R includes stop and 15:15 exits.",
        "Time Machine columns are the average of that signal's path shares.",
        "",
        "## Verdicts per day",
        "",
        *day_lines,
        "",
        *_block("By verdict", tagged),
        *_block("By setup", by_setup),
        *_block("By history source", by_source),
        *_block("Verdict × setup", by_verdict_setup),
        *_block("Verdict × stock or pooled history", by_verdict_source),
    ]
    out = Path(cfg.data_dir) / "scanner" / "reports" / f"last20_{datetime.now(IST).date().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
