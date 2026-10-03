"""Daily spread report. Every percentile is taken from one snapshot per contract per second."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.live.model import ist_ms_of_day
from app.options.strikes import atm_strike

_OPEN = 9 * 60 + 15
_CLOSE = 15 * 60 + 30
_RANGE_END = 9 * 60 + 30
_FIELDS = ("spread", "buy_1", "sell_1", "buy_2", "sell_2")


def median_p90(values: list[float]) -> tuple[float | None, float | None]:
    """Median averages the two middle values when the count is even.
    The 90th percentile is nearest rank: ceil(0.9 × n), 1-based."""
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return None, None
    if n % 2:
        med = xs[n // 2]
    else:
        med = (xs[n // 2 - 1] + xs[n // 2]) / 2
    rank = math.ceil(0.9 * n)
    return float(med), float(xs[rank - 1])


def second_snapshots(rows: list[dict]) -> list[dict]:
    """The last update of each contract in each clock second. A second with 50
    updates counts once, the same as a second with one."""
    last: dict[tuple[str, int], dict] = {}
    order: list[tuple[str, int]] = []
    for row in rows:
        key = (str(row["instrument_key"]), int(row["ts_ms"]) // 1000)
        if key not in last:
            order.append(key)
        last[key] = row
    return [last[k] for k in order]


def fast_minutes(bars: list[dict]) -> set[int]:
    """Bar-open times whose high−low is in the top 10% of `bars`. Ties at the cutoff all count."""
    if not bars:
        return set()
    ranges = sorted((float(b["high"]) - float(b["low"]) for b in bars), reverse=True)
    cutoff = ranges[math.ceil(0.1 * len(bars)) - 1]
    return {int(b["time"]) for b in bars if float(b["high"]) - float(b["low"]) >= cutoff - 1e-9}


def orb_window(bars: list[dict]) -> tuple[int, int] | None:
    """[start_ms, end_ms) of the 5 minutes after the first 1m bar that breaks the 09:15–09:30 range.
    Both sides on that bar still open one window. No break, or no range bars, is None."""
    ranged = [b for b in bars if _OPEN <= _minute_s(int(b["time"])) < _RANGE_END]
    if not ranged:
        return None
    hi = max(float(b["high"]) for b in ranged)
    lo = min(float(b["low"]) for b in ranged)
    later = sorted((b for b in bars if _minute_s(int(b["time"])) >= _RANGE_END), key=lambda b: int(b["time"]))
    for bar in later:
        if float(bar["high"]) > hi or float(bar["low"]) < lo:
            start = int(bar["time"]) * 1000
            return start, start + 5 * 60 * 1000
    return None


def report_day(rows: list[dict], nifty_bars: list[dict], *, day: date, step: int, calendar) -> dict[str, Any]:
    snaps = [s for s in second_snapshots(rows) if _time_bin(int(s["ts_ms"])) is not None]
    session = [b for b in nifty_bars if _OPEN <= _minute_s(int(b["time"])) < _CLOSE]
    fast = fast_minutes(session)
    window = orb_window(session)
    groups: dict[str, dict[str, list[dict]]] = {"time": {}, "dte": {}, "moneyness": {}}
    fast_rows: list[dict] = []
    orb_rows: list[dict] = []
    for snap in snaps:
        ts = int(snap["ts_ms"])
        bin_name = _time_bin(ts)
        if bin_name is None:
            continue
        groups["time"].setdefault(bin_name, []).append(snap)
        expiry = date.fromisoformat(str(snap["expiry"]))
        groups["dte"].setdefault(_dte_bucket(_trading_dte(day, expiry, calendar.is_trading_day)), []).append(snap)
        groups["moneyness"].setdefault(
            _moneyness(str(snap["kind"]), float(snap["strike"]), float(snap["nifty_ltp"]), step), []
        ).append(snap)
        if (ts // 1000) // 60 * 60 in fast:
            fast_rows.append(snap)
        if window is not None and window[0] <= ts < window[1]:
            orb_rows.append(snap)
    return {
        "day": day.isoformat(),
        "snapshots": len(snaps),
        "time": {name: _cell(items) for name, items in sorted(groups["time"].items())},
        "dte": {name: _cell(items) for name, items in sorted(groups["dte"].items())},
        "moneyness": {name: _cell(items) for name, items in sorted(groups["moneyness"].items())},
        "fast": _cell(fast_rows),
        "orb": _cell(orb_rows),
    }


def write_report_files(directory: Path, day: date, nifty_bars: list[dict]) -> dict[str, Any]:
    from app.backtest.expiry import load_default_calendar
    from app.live.spreads.writer import read_spreads
    from app.options.strikes import load_default_step_table

    frame = read_spreads(directory, day)
    rows = _frame_rows(frame)
    step = load_default_step_table().step("NIFTY", day)
    report = report_day(rows, nifty_bars, day=day, step=step, calendar=load_default_calendar())
    out = directory / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{day.isoformat()}.json").write_text(json.dumps(report, indent=2))
    (out / f"{day.isoformat()}.md").write_text(_markdown(report))
    return report


def _frame_rows(frame) -> list[dict]:
    rows = []
    for rec in frame.to_dict(orient="records"):
        row = {}
        for key, value in rec.items():
            if value is None or (isinstance(value, float) and math.isnan(value)):
                row[key] = None
            else:
                row[key] = value
        rows.append(row)
    return rows


def _cell(rows: list[dict]) -> dict[str, Any]:
    def stat(field: str, *, nonnegative: bool = False) -> dict[str, Any]:
        vals: list[float] = []
        uncovered = 0
        for row in rows:
            value = row.get(field)
            if value is None or (isinstance(value, float) and math.isnan(value)):
                uncovered += 1
                continue
            number = float(value)
            if nonnegative and number < 0:
                continue
            vals.append(number)
        med, p90 = median_p90(vals)
        return {"median": med, "p90": p90, "n": len(vals), "uncovered": uncovered, "thin": len(vals) < 30}

    covers = sum(1 for row in rows if row.get("level1_covers_1_lot") is True)
    cell = {field: stat(field, nonnegative=(field == "spread")) for field in _FIELDS}
    cell["level1_covers_1_lot"] = {"true": covers, "n": len(rows)}
    return cell


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Spread report {report['day']}",
        "",
        f"Snapshots (one per contract per second, 09:15–15:30): {report['snapshots']}.",
        "Spread and fill costs are premium points. Fill cost is the average price of 1 or 2 lots",
        "walked through the five levels, minus the mid. A null fill means the five levels could not cover it.",
        "",
    ]
    for title, key in (
        ("Time of day", "time"), ("DTE", "dte"), ("Moneyness", "moneyness"),
    ):
        lines.append(f"## {title}")
        lines.append("")
        lines.extend(_table(report[key]))
        lines.append("")
    lines.append("## Fast minutes")
    lines.append("")
    lines.extend(_table({"": report["fast"]}))
    lines.append("")
    lines.append("## Five minutes after the 09:15–09:30 break")
    lines.append("")
    lines.extend(_table({"": report["orb"]}))
    lines.append("")
    return "\n".join(lines)


def _table(groups: dict[str, Any]) -> list[str]:
    header = "| bucket | spread median | spread p90 | n | buy 1 lot | sell 1 lot | uncovered buy 1 | level-1 covers 1 lot |"
    rule = "|---|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, rule]
    for name, cell in groups.items():
        spread = cell["spread"]
        lines.append(
            f"| {name} | {_n(spread['median'])} | {_n(spread['p90'])} | {spread['n']} | "
            f"{_n(cell['buy_1']['median'])} | {_n(cell['sell_1']['median'])} | {cell['buy_1']['uncovered']} | "
            f"{cell['level1_covers_1_lot']['true']}/{cell['level1_covers_1_lot']['n']} |"
        )
    return lines


def _n(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _minute_s(ts_s: int) -> int:
    return ((ts_s + 19_800) % 86_400) // 60


def _time_bin(ts_ms: int) -> str | None:
    minute = ist_ms_of_day(ts_ms) // 60_000
    if minute < _OPEN or minute >= _CLOSE:
        return None
    start = _OPEN + ((minute - _OPEN) // 15) * 15
    hour, mins = divmod(start, 60)
    return f"{hour:02d}:{mins:02d}"


def _trading_dte(day: date, expiry: date, is_trading_day) -> int:
    n = 0
    cursor = day
    while cursor < expiry:
        cursor += timedelta(days=1)
        if is_trading_day(cursor):
            n += 1
    return n


def _dte_bucket(dte: int) -> str:
    if dte <= 0:
        return "0"
    if dte == 1:
        return "1"
    if dte == 2:
        return "2"
    if dte <= 4:
        return "3-4"
    return "5+"


def _moneyness(kind: str, strike: float, spot: float, step: int) -> str:
    steps = int(round((strike - atm_strike(spot, step)) / step))
    if steps == 0:
        return "ATM"
    otm = steps > 0 if kind == "CE" else steps < 0
    dist = abs(steps)
    if dist == 1:
        return "OTM1" if otm else "ITM1"
    if dist == 2:
        return "OTM2" if otm else "ITM2"
    return "outside"
