"""Recorded GEX snapshots (data/gex/<YYYY-MM-DD>.jsonl, one line per 5-minute snapshot) and the
end-of-day study that asks whether the GEX regime changes how the day moves and how trades do.

No look-ahead: a trade or a day is labelled only from a snapshot taken at or before that moment.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from app.options.time import IST

#: the day's regime is read from the first snapshot at or after this time (OI settles after the open)
ANCHOR = time(9, 45)
LONG = "long_gamma"
SHORT = "short_gamma"
REGIMES = (LONG, SHORT)


# ------------------------------------------------------------------ storage
def snapshot_row(result: dict[str, Any]) -> dict[str, Any]:
    """The compact line stored for one /api/gex result."""
    return {
        "t": int(result["as_of"]),
        "expiry": result["expiry"],
        "spot": result.get("spot"),
        "total_net": result.get("total_net"),
        "total_call": result.get("total_call"),
        "total_put": result.get("total_put"),
        "gamma_flip": result.get("gamma_flip"),
        "call_wall": result.get("call_wall"),
        "put_wall": result.get("put_wall"),
        "strikes": [[s["strike"], round(s["call"], 3), round(s["put"], 3)] for s in result.get("strikes", [])],
    }


def local_day(t: int) -> date:
    return datetime.fromtimestamp(int(t), IST).date()


def append_snapshot(gex_dir: Path, row: dict[str, Any]) -> Path:
    gex_dir.mkdir(parents=True, exist_ok=True)
    path = gex_dir / f"{local_day(row['t']).isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path


def recorded_days(gex_dir: Path) -> list[date]:
    out = []
    for p in gex_dir.glob("*.jsonl") if gex_dir.is_dir() else []:
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(out)


def load_day(gex_dir: Path, day: date) -> list[dict[str, Any]]:
    path = gex_dir / f"{day.isoformat()}.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written last line after a crash
        if isinstance(row, dict) and isinstance(row.get("t"), int) and row.get("spot") is not None:
            rows.append(row)
    rows.sort(key=lambda r: r["t"])
    # one snapshot per 5-minute slot (a second backend writing the same day must not double-count)
    seen: set[int] = set()
    out = []
    for r in rows:
        slot = r["t"] // 300
        if slot not in seen:
            seen.add(slot)
            out.append(r)
    return out


# ------------------------------------------------------------------ labels
def snapshot_at(snaps: list[dict[str, Any]], t: int) -> dict[str, Any] | None:
    """Latest snapshot taken at or before `t` (snaps ascending)."""
    best = None
    for s in snaps:
        if s["t"] <= t:
            best = s
        else:
            break
    return best


def regime(snap: dict[str, Any], price: float) -> str:
    """Long gamma when price is at or above the flip; with no flip, the sign of total net GEX."""
    flip = snap.get("gamma_flip")
    if flip is not None:
        return LONG if price >= float(flip) else SHORT
    return LONG if float(snap.get("total_net") or 0.0) >= 0 else SHORT


def day_stats(snaps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Regime at the anchor snapshot, then how far spot travelled for the rest of the day."""
    anchor = next((s for s in snaps if datetime.fromtimestamp(s["t"], IST).time() >= ANCHOR), None)
    if anchor is None:
        return None
    after = [s for s in snaps if s["t"] >= anchor["t"]]
    spots = [float(s["spot"]) for s in after]
    rng = max(spots) - min(spots)
    move = spots[-1] - spots[0]
    return {
        "day": local_day(anchor["t"]).isoformat(),
        "regime": regime(anchor, float(anchor["spot"])),
        "net_at_anchor": anchor.get("total_net"),
        "flip_at_anchor": anchor.get("gamma_flip"),
        "spot_at_anchor": anchor["spot"],
        "range_after": rng,
        "move_after": move,
        "trend_day": rng > 0 and abs(move) >= 0.5 * rng,
        "snapshots": len(snaps),
    }


# ------------------------------------------------------------------ trades
def _trade_pnl(t: dict[str, Any]) -> tuple[float, bool]:
    """(option net ₹ if known else index points, is_option)."""
    opt = t.get("option") or {}
    if opt.get("net_pnl") is not None:
        return float(opt["net_pnl"]), True
    sign = 1 if t.get("direction") == "LONG" else -1
    return (float(t["exit_price"]) - float(t["entry_price"])) * sign, False


def tag_trades(
    trades: Iterable[dict[str, Any]], snaps_by_day: dict[date, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Trades on recorded days, each with the regime of the last snapshot at or before entry."""
    out = []
    for t in trades:
        entry = int(t["entry_time"])
        snaps = snaps_by_day.get(local_day(entry))
        snap = snapshot_at(snaps, entry) if snaps else None
        if snap is None:
            continue
        pnl, is_option = _trade_pnl(t)
        out.append({
            "entry_time": entry,
            "direction": t.get("direction"),
            "regime": regime(snap, float(t["entry_price"])),
            "pnl": pnl,
            "is_option": is_option,
        })
    return out


def summarise_tagged(tagged: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in REGIMES:
        rows = [r for r in tagged if r["regime"] == name]
        pnl = [r["pnl"] for r in rows]
        out[name] = {
            "trades": len(rows),
            "win_rate": (sum(1 for p in pnl if p > 0) / len(pnl)) if pnl else None,
            "net": sum(pnl) if pnl else 0.0,
            "per_trade": (sum(pnl) / len(pnl)) if pnl else None,
        }
    return out


def summarise_days(stats: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in stats:
        by[s["regime"]].append(s)
    out = {}
    for name in REGIMES:
        rows = by.get(name, [])
        out[name] = {
            "days": len(rows),
            "avg_range": (sum(r["range_after"] for r in rows) / len(rows)) if rows else None,
            "avg_abs_move": (sum(abs(r["move_after"]) for r in rows) / len(rows)) if rows else None,
            "trend_days": sum(1 for r in rows if r["trend_day"]),
        }
    return out


# ------------------------------------------------------------------ report text
MIN_DAYS_TO_JUDGE = 20


def _f(x: Any, nd: int = 1) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return str(x)


def render_report(
    day_stats_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    written_for: date,
) -> str:
    n = len(day_stats_rows)
    lines = [f"# GEX study, {written_for.isoformat()} ({n} recorded day{'s' if n != 1 else ''})", ""]
    if n < MIN_DAYS_TO_JUDGE:
        lines += [f"Only {n} day(s) so far: too few to judge. Read this as a log until there are "
                  f"{MIN_DAYS_TO_JUDGE}+ days.", ""]
    lines += ["Regime = spot vs the gamma flip at the first snapshot from 09:45 (trades: at entry).", "",
              "## Does the regime change how the day moves?", "",
              "| regime | days | avg range after 09:45 (pts) | avg abs move (pts) | trend days |", "|---|---|---|---|---|"]
    days = summarise_days(day_stats_rows)
    for name in REGIMES:
        d = days[name]
        lines.append(f"| {name} | {d['days']} | {_f(d['avg_range'])} | {_f(d['avg_abs_move'])} | {d['trend_days']} |")
    lines += ["", "## Candidates split by regime at entry (options mode, after costs; ₹)", "",
              "| id | strategy | long γ trades | long γ win % | long γ ₹/trade | short γ trades | short γ win % "
              "| short γ ₹/trade | error |", "|---|---|---|---|---|---|---|---|---|"]
    for c in candidates:
        s = c.get("by_regime") or {}
        lg, sh = s.get(LONG, {}), s.get(SHORT, {})

        def win(x: dict[str, Any]) -> str:
            return "-" if x.get("win_rate") is None else f"{x['win_rate'] * 100:.0f}"

        lines.append(
            f"| {c['id']} | {c['name']} | {lg.get('trades', 0)} | {win(lg)} | {_f(lg.get('per_trade'), 0)} | "
            f"{sh.get('trades', 0)} | {win(sh)} | {_f(sh.get('per_trade'), 0)} | {c.get('error') or ''} |"
        )
    lines += ["", "## Days", "", "| day | regime | net GEX at anchor (Cr) | flip | spot | range after | move after |",
              "|---|---|---|---|---|---|---|"]
    for r in day_stats_rows:
        lines.append(f"| {r['day']} | {r['regime']} | {_f(r['net_at_anchor'], 0)} | {_f(r['flip_at_anchor'], 0)} | "
                     f"{_f(r['spot_at_anchor'], 0)} | {_f(r['range_after'])} | {_f(r['move_after'])} |")
    return "\n".join(lines) + "\n"
