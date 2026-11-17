"""S5-QM-long edge diagnosis (research only — slot 5 unchanged).

    cd backend && uv run python -m scripts.s5_edge_diagnosis

STEP 1: factor breakdowns (hour, weekday/expiry, VIX, ADX tercile, 15m ST agree, gap, +5% progress).
STEP 2: ≤3 skip / time-stop changes on buckets that lose in BOTH windows; holdout for the best.
Writes data/validation/s5_edge_diagnosis_<date>.md (under data/ — not committed).
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.catalog import parse_config
from app.backtest.execute import execute_holdout, execute_run
from app.backtest.holdout import load_holdout
from app.config import settings
from app.data.service import get_candles
from app.data.store import CandleStore
from app.exits.rules import NO_TARGET
from app.indicators.basic import rma
from app.indicators.frame import candles_to_frame
from app.indicators.volatility import supertrend, true_range
from app.options.history import default_history_store
from app.options.model import HISTORY_START, _session_map
from app.strategies.daily_levels import minute_of_day

IST = timezone(timedelta(hours=5, minutes=30))
RESEARCH_START = date(2022, 1, 3)
VIX_SYMBOL = "NSE_INDEX_India_VIX"
SLOT5 = {"mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10"}
HOUR_BUCKETS = (
    ("09:15-10:00", 9 * 60 + 15, 10 * 60),
    ("10-11", 10 * 60, 11 * 60),
    ("11-12", 11 * 60, 12 * 60),
    ("12-13:30", 12 * 60, 13 * 60 + 30),
    ("13:30-15:10", 13 * 60 + 30, 15 * 60 + 10 + 1),
)


def _run(start: date, end: date, *, holdout: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "strategy": "dd_five_confirm",
        "params": SLOT5,
        "symbol": "NIFTY50",
        "timeframe": "3m",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": ["normal"],
        "mode": "options",
        "strike_offset": 0,
        "strike_premium": 250.0,
        "slippage_points": 0.2,
        "live_timing": True,
        "option_fill": "delta_adjusted",
        "exit_rule": {
            "kind": "premium",
            "stop": 0.10,
            "target": NO_TARGET,
            "trail_every": 0.15,
            "trail_by": 0.10,
        },
    }
    if holdout:
        cfg = parse_config(body, allow_holdout=True)
        cfg["kind"] = "holdout"
        return execute_holdout(cfg, lambda _e: None)
    return execute_run(parse_config(body), lambda _e: None, hash_data=False)


def _pf(nets: list[float]) -> float | None:
    wins = sum(v for v in nets if v > 0)
    losses = abs(sum(v for v in nets if v < 0))
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return wins / losses


def _max_dd(nets: list[float]) -> float:
    eq = peak = 0.0
    dd = 0.0
    for v in nets:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return abs(dd)


def _leave_top(nets: list[float], n: int) -> float:
    if len(nets) <= n:
        return 0.0
    return sum(sorted(nets, reverse=True)[n:])


def _stats(nets: list[float]) -> dict[str, Any]:
    wins = [v for v in nets if v > 0]
    return {
        "trades": len(nets),
        "win_pct": 100.0 * len(wins) / len(nets) if nets else 0.0,
        "net": sum(nets),
        "pf": _pf(nets),
        "max_dd": _max_dd(nets),
        "without_top10": _leave_top(nets, 10),
    }


def _f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        if x == float("inf"):
            return "∞"
        return f"{x:,.{nd}f}"
    return str(x)


def _adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Wilder ADX(length) from OHLC frame."""
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    atr_s = rma(tr, length)
    plus_di = 100.0 * rma(pd.Series(plus_dm, index=df.index), length) / atr_s
    minus_di = 100.0 * rma(pd.Series(minus_dm, index=df.index), length) / atr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return rma(dx, length)


def _hour_bucket(tod: int) -> str:
    for name, lo, hi in HOUR_BUCKETS:
        if lo <= tod < hi:
            return name
    return "other"


def _build_context(start: date, end: date) -> dict[str, Any]:
    store = CandleStore(settings.candles_dir)
    # pad for indicators / prior close
    pad_start = start - timedelta(days=40)
    start_ts = int(datetime(pad_start.year, pad_start.month, pad_start.day, tzinfo=IST).timestamp())
    end_ts = int(datetime(end.year, end.month, end.day, 23, 59, tzinfo=IST).timestamp())
    nifty = get_candles(
        store, "NIFTY50", "1m", from_time=start_ts, to_time=end_ts, session_types=["normal"]
    ).candles
    vix = get_candles(
        store, VIX_SYMBOL, "1m", from_time=start_ts, to_time=end_ts, session_types=["normal"]
    ).candles
    bars_15 = get_candles(
        store, "NIFTY50", "15m", from_time=start_ts, to_time=end_ts, session_types=["normal"]
    ).candles

    sessions, prev_session = _session_map(nifty)

    # VIX as-of: last close at or before t
    vix_times = np.array([int(c["time"]) for c in vix], dtype=np.int64)
    vix_close = np.array([float(c["close"]) for c in vix], dtype=float)

    df15 = candles_to_frame(bars_15)
    st = supertrend(df15, 10, 3.0)
    adx = _adx(df15, 14)
    t15 = df15["time"].to_numpy(dtype=np.int64) if "time" in df15.columns else np.array([int(c["time"]) for c in bars_15], dtype=np.int64)
    if "time" not in df15.columns:
        df15 = df15.copy()
        df15["time"] = t15
    st_dir = st["direction"].to_numpy(dtype=float)
    adx_v = adx.to_numpy(dtype=float)

    tape = default_history_store()
    return {
        "sessions": sessions,
        "prev_session": prev_session,
        "vix_times": vix_times,
        "vix_close": vix_close,
        "t15": t15,
        "st_dir": st_dir,
        "adx": adx_v,
        "tape": tape,
    }


def _lookup_last(times: np.ndarray, values: np.ndarray, t: int) -> float | None:
    if times.size == 0:
        return None
    i = int(np.searchsorted(times, t, side="right") - 1)
    if i < 0:
        return None
    v = float(values[i])
    return None if np.isnan(v) else v


def _gap_label(sessions: dict[date, dict[str, float]], prev: dict[date, date], d: date, thr: float = 0.3) -> str:
    if d not in sessions or d not in prev:
        return "unknown"
    pc = sessions[prev[d]]["close"]
    if pc == 0:
        return "unknown"
    g = (sessions[d]["open"] - pc) / abs(pc) * 100.0
    if g > thr:
        return "gap_up"
    if g < -thr:
        return "gap_down"
    return "flat"


def _minutes_to_progress(tape: Any, trade: dict[str, Any], pct: float = 0.05) -> int | None:
    opt = trade.get("option") or {}
    c = opt.get("contract") or {}
    fill = opt.get("entry_fill")
    if fill is None or not c:
        return None
    try:
        exp = date.fromisoformat(str(c["expiry"])) if not isinstance(c["expiry"], date) else c["expiry"]
        strike = float(c["strike"])
        kind = str(c["kind"])
    except (KeyError, TypeError, ValueError):
        return None
    entry = int(trade["entry_time"])
    exit_t = int(opt.get("exit_time") or trade["exit_time"])
    target = round(float(fill) * (1.0 + pct), 2)
    t0 = entry - entry % 60
    for t_min in range(t0, min(exit_t, t0 + 60 * 60) + 1, 60):  # up to 60m look
        ohlc = tape.bar_at(exp, strike, kind, t_min)
        if ohlc is None:
            continue
        _o, h, _l, _c = ohlc
        if h >= target:
            return max(0, (t_min - t0) // 60)
    return None


def _force_exit_pnl(tape: Any, trade: dict[str, Any], after_min: int) -> float | None:
    """Approximate net ₹ if flattened at `after_min` using that minute's close (no charge recompute: scale)."""
    opt = trade.get("option") or {}
    c = opt.get("contract") or {}
    fill = opt.get("entry_fill")
    exit_fill = opt.get("exit_fill")
    net = opt.get("net_pnl")
    if fill is None or exit_fill is None or net is None or not c:
        return None
    try:
        exp = date.fromisoformat(str(c["expiry"])) if not isinstance(c["expiry"], date) else c["expiry"]
        strike = float(c["strike"])
        kind = str(c["kind"])
        units = float(opt.get("units") or 65)
    except (KeyError, TypeError, ValueError):
        return None
    entry = int(trade["entry_time"])
    t_exit = (entry - entry % 60) + after_min * 60
    ohlc = tape.bar_at(exp, strike, kind, t_exit)
    if ohlc is None:
        # try nearby minutes
        for dt in range(0, 5):
            ohlc = tape.bar_at(exp, strike, kind, t_exit + 60 * dt)
            if ohlc is not None:
                break
    if ohlc is None:
        return None
    px = float(ohlc[3])  # close
    # gross change vs original: replace exit_fill with px; keep charges ratio roughly via net/gross
    gross_old = (float(exit_fill) - float(fill)) * units
    gross_new = (px - float(fill)) * units
    charges = float(opt.get("charges_total") or 0.0)
    if charges == 0 and opt.get("gross_pnl") is not None:
        charges = float(opt["gross_pnl"]) - float(net)
    return gross_new - charges


def _annotate(trades: list[dict[str, Any]], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    vix_at_entries: list[float] = []
    adx_at_entries: list[float] = []
    for t in trades:
        opt = t.get("option") or {}
        if opt.get("net_pnl") is None:
            continue
        entry = int(t["entry_time"])
        when = datetime.fromtimestamp(entry, IST)
        tod = minute_of_day(entry)
        d = when.date()
        vix = _lookup_last(ctx["vix_times"], ctx["vix_close"], entry)
        adxv = _lookup_last(ctx["t15"], ctx["adx"], entry)
        st_d = _lookup_last(ctx["t15"], ctx["st_dir"], entry)
        # TV: -1 uptrend → agrees with long
        st_agree = "agree" if st_d is not None and st_d < 0 else ("disagree" if st_d is not None else "unknown")
        flags = set(opt.get("flags") or [])
        expiry = "expiry_day" if "expiry_day" in flags else "other_day"
        # also treat contract expiry == entry day
        c = opt.get("contract") or {}
        try:
            exp = date.fromisoformat(str(c.get("expiry")))
            if exp == d:
                expiry = "expiry_day"
        except (TypeError, ValueError):
            pass
        # Progress needs a real option tape; modelled-only fills (before HISTORY_START) → no_tape.
        sources = {opt.get("entry_source"), opt.get("exit_source")}
        has_tape = "real" in sources
        mins = _minutes_to_progress(ctx["tape"], t, 0.05) if has_tape else None
        if vix is not None:
            vix_at_entries.append(vix)
        if adxv is not None:
            adx_at_entries.append(adxv)
        if not has_tape:
            progress = "no_tape"
        elif mins is not None and mins <= 10:
            progress = "hit≤10"
        elif mins is not None and mins <= 20:
            progress = "hit≤20"
        elif mins is not None and mins <= 30:
            progress = "hit≤30"
        else:
            progress = "no_hit_30"
        rows.append({
            "trade": t,
            "when": when,
            "net": float(opt["net_pnl"]),
            "hour": _hour_bucket(tod),
            "weekday": when.strftime("%a"),
            "expiry": expiry,
            "vix": vix,
            "adx": adxv,
            "st_agree": st_agree,
            "gap": _gap_label(ctx["sessions"], ctx["prev_session"], d, 0.3),
            "mins_to_5": mins,
            "has_tape": has_tape,
            "progress": progress,
        })
    # terciles from this window's entries
    def tercile(vals: list[float], x: float | None, labels: tuple[str, str, str]) -> str:
        if x is None or len(vals) < 3:
            return "unknown"
        a, b = np.quantile(vals, [1 / 3, 2 / 3])
        if x <= a:
            return labels[0]
        if x <= b:
            return labels[1]
        return labels[2]

    for r in rows:
        r["vix_tercile"] = tercile(vix_at_entries, r["vix"], ("VIX_low", "VIX_mid", "VIX_high"))
        r["adx_tercile"] = tercile(adx_at_entries, r["adx"], ("ADX_low", "ADX_mid", "ADX_high"))
    return rows


def _table(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        groups[str(r[key])].append(r["net"])
    out = []
    for k in sorted(groups, key=lambda x: (x == "unknown", x)):
        s = _stats(groups[k])
        s["bucket"] = k
        out.append(s)
    return out


def _md_factor(title: str, tables: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines = [f"### {title}", ""]
    for wname, rows in tables.items():
        lines += [f"**{wname}**", "", "| bucket | trades | win % | net ₹ | PF |", "|---|---:|---:|---:|---:|"]
        for r in rows:
            lines.append(
                f"| {r['bucket']} | {r['trades']} | {_f(r['win_pct'], 1)} | {_f(r['net'])} | {_f(r['pf'], 2)} |"
            )
        lines.append("")
    return lines


def _losing_both(
    research: list[dict[str, Any]], real: list[dict[str, Any]]
) -> list[str]:
    r_map = {r["bucket"]: r for r in research}
    x_map = {r["bucket"]: r for r in real}
    bad = []
    for k in set(r_map) & set(x_map):
        if k in ("unknown", "other"):
            continue
        if r_map[k]["net"] < 0 and x_map[k]["net"] < 0 and r_map[k]["trades"] >= 15 and x_map[k]["trades"] >= 10:
            bad.append(k)
    return sorted(bad)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)

    holdout = load_holdout()
    research_end = holdout[0] - timedelta(days=1)
    lines: list[str] = [
        "# S5-QM-long edge diagnosis (slot 5 unchanged)",
        "",
        "dd_five_confirm 3m long_only use_wr, strike_premium 250, premium_trail_10, live timing, "
        "slippage 0.2, delta-adjusted. Holdout excluded from research/real-prem.",
        "",
        f"Research {RESEARCH_START} .. {research_end}; real-premium {HISTORY_START} .. {research_end}.",
        "Day-type: market-view history too sparse → **ADX(14) on 15m terciles**.",
        "Premium progress / time-stop use real option tape only (no_tape before 2024-10-03).",
        "",
    ]

    windows = [("research", RESEARCH_START, research_end), ("real-prem", HISTORY_START, research_end)]
    if args.smoke:
        windows = [("research", RESEARCH_START, min(RESEARCH_START + timedelta(days=60), research_end))]

    print("=== building shared context (Nifty/VIX/15m) ===", flush=True)
    ctx_start = windows[0][1]
    ctx_end = max(w[2] for w in windows)
    ctx = _build_context(ctx_start, ctx_end)
    print(
        f"  1m sessions={len(ctx['sessions'])} vix={len(ctx['vix_times'])} 15m={len(ctx['t15'])}",
        flush=True,
    )

    annotated: dict[str, list[dict[str, Any]]] = {}
    baselines: dict[str, dict[str, Any]] = {}

    for wname, start, end in windows:
        print(f"=== baseline {wname} {start}..{end} ===", flush=True)
        out = _run(start, end)
        trades = out["result"]["trades"]
        print(f"  trades={len(trades)} annotating ...", flush=True)
        rows = _annotate(trades, ctx)
        annotated[wname] = rows
        baselines[wname] = _stats([r["net"] for r in rows])
        print(
            f"  annotated={len(rows)} net={baselines[wname]['net']:.0f} pf={baselines[wname]['pf']}",
            flush=True,
        )

    lines += ["## Baseline", "", "| window | trades | win % | net ₹ | PF | max DD | −top10 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for wname, _, _ in windows:
        b = baselines[wname]
        lines.append(
            f"| {wname} | {b['trades']} | {_f(b['win_pct'], 1)} | {_f(b['net'])} | {_f(b['pf'], 2)} | "
            f"{_f(b['max_dd'])} | {_f(b['without_top10'])} |"
        )
    lines.append("")

    # STEP 1 tables
    lines += ["## STEP 1 — Factor breakdowns", ""]
    factors = [
        ("a) Entry hour", "hour"),
        ("b) Weekday", "weekday"),
        ("b2) Expiry day vs other", "expiry"),
        ("c) India VIX tercile at entry", "vix_tercile"),
        ("d) ADX(14) 15m tercile (day-type proxy)", "adx_tercile"),
        ("e) 15m Supertrend(10,3) vs long", "st_agree"),
        ("f) Opening gap ±0.3%", "gap"),
        ("g) Minutes to first +5% premium", "progress"),
    ]
    factor_tables: dict[str, dict[str, list[dict[str, Any]]]] = {}
    losing: dict[str, list[str]] = {}
    for title, key in factors:
        tables = {w: _table(annotated[w], key) for w in annotated}
        factor_tables[key] = tables
        lines += _md_factor(title, tables)
        if "research" in tables and "real-prem" in tables:
            losing[key] = _losing_both(tables["research"], tables["real-prem"])
            if losing[key]:
                lines.append(f"Losing in **both** windows: {', '.join(losing[key])}.")
                lines.append("")

    # STEP 2 — pick ≤3 changes from losing buckets with a reason
    lines += ["## STEP 2 — Candidate changes (≤3)", ""]
    candidates: list[dict[str, Any]] = []

    # Prefer skips that are large and losing: hour / vix / adx / gap / st_disagree / progress
    def add_skip(name: str, reason: str, pred) -> None:  # noqa: ANN001
        if len(candidates) >= 3:
            return
        candidates.append({"name": name, "reason": reason, "kind": "skip", "pred": pred})

    # Hour losers
    for h in losing.get("hour", []):
        add_skip(f"skip_hour_{h}", f"hour {h} loses in both windows", lambda r, hh=h: r["hour"] != hh)
    # VIX high often hurts long premium buys
    if "VIX_high" in losing.get("vix_tercile", []):
        add_skip("skip_vix_high", "VIX_high loses in both windows", lambda r: r["vix_tercile"] != "VIX_high")
    if "ADX_low" in losing.get("adx_tercile", []):
        add_skip("skip_adx_low", "ADX_low (range-like) loses in both", lambda r: r["adx_tercile"] != "ADX_low")
    if "disagree" in losing.get("st_agree", []):
        add_skip("skip_st_disagree", "15m ST disagree loses in both", lambda r: r["st_agree"] != "disagree")
    if "gap_down" in losing.get("gap", []):
        add_skip("skip_gap_down", "gap_down loses in both", lambda r: r["gap"] != "gap_down")
    if "no_hit_30" in losing.get("progress", []) and len(candidates) < 3:
        # time stop instead of skip
        candidates.append({
            "name": "timestop_5pct_20m",
            "reason": "no +5% by 30m loses in both → exit if not ≥+5% by 20m",
            "kind": "timestop",
            "x": 0.05,
            "n": 20,
        })

    # If still empty, force top structural skips from net worst hour
    if not candidates and "research" in annotated and "real-prem" in annotated:
        # worst hour by real-prem net among those also negative in research
        hour_r = {r["bucket"]: r for r in factor_tables["hour"]["research"]}
        hour_x = {r["bucket"]: r for r in factor_tables["hour"]["real-prem"]}
        ranked = sorted(
            (k for k in hour_r if k in hour_x and hour_r[k]["net"] < 0 and hour_x[k]["net"] < 0),
            key=lambda k: hour_x[k]["net"],
        )
        for k in ranked[:2]:
            add_skip(f"skip_hour_{k}", f"hour {k} negative both (fallback)", lambda r, hh=k: r["hour"] != hh)
        candidates.append({
            "name": "timestop_5pct_20m",
            "reason": "progress filter: cut dead trades at 20m if <+5%",
            "kind": "timestop",
            "x": 0.05,
            "n": 20,
        })

    candidates = candidates[:3]
    if not candidates:
        lines.append("No qualifying losing buckets with enough trades; no changes tested.")
        lines.append("")
    else:
        for c in candidates:
            lines.append(f"- **{c['name']}**: {c['reason']} ({c['kind']})")
        lines.append("")

    accepted: list[dict[str, Any]] = []

    def apply_skip(rows: list[dict[str, Any]], pred) -> list[float]:  # noqa: ANN001
        return [r["net"] for r in rows if pred(r)]

    def apply_timestop(rows: list[dict[str, Any]], x: float, n: int, tape: Any) -> list[float]:
        out = []
        for r in rows:
            if not r.get("has_tape"):
                out.append(r["net"])  # cannot apply premium time-stop without tape
                continue
            if abs(x - 0.05) < 1e-9:
                hit = r["mins_to_5"]
            else:
                hit = _minutes_to_progress(tape, r["trade"], x)
            if hit is not None and hit <= n:
                out.append(r["net"])  # keep original trail path
                continue
            alt = _force_exit_pnl(tape, r["trade"], n)
            out.append(alt if alt is not None else r["net"])
        return out

    # Need tape for timestop from real-prem context (rebuild light)
    tape = default_history_store()

    lines += ["### Change results", ""]
    for c in candidates:
        print(f"=== change {c['name']} ===", flush=True)
        lines += [f"#### {c['name']}", "", f"Reason: {c['reason']}", ""]
        lines += [
            "| window | trades | kept % | win % | net ₹ | PF | max DD | −top10 | vs base PF | vs base DD |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        ok_both = True
        kept_ok = True
        window_stats: dict[str, dict[str, Any]] = {}
        for wname in annotated:
            base = baselines[wname]
            rows = annotated[wname]
            if c["kind"] == "skip":
                nets = apply_skip(rows, c["pred"])
            else:
                nets = apply_timestop(rows, float(c["x"]), int(c["n"]), tape)
            s = _stats(nets)
            kept = 100.0 * s["trades"] / base["trades"] if base["trades"] else 0.0
            if kept < 70:
                kept_ok = False
            pf_better = s["pf"] is not None and base["pf"] is not None and s["pf"] > base["pf"]
            dd_better = s["max_dd"] < base["max_dd"]
            if not (pf_better and dd_better):
                ok_both = False
            window_stats[wname] = {**s, "kept": kept}
            lines.append(
                f"| {wname} | {s['trades']} | {_f(kept, 1)} | {_f(s['win_pct'], 1)} | {_f(s['net'])} | "
                f"{_f(s['pf'], 2)} | {_f(s['max_dd'])} | {_f(s['without_top10'])} | "
                f"{'↑' if pf_better else '↓'} | {'↑' if dd_better else '↓'} |"
            )
            print(
                f"  {wname}: n={s['trades']} kept={kept:.0f}% net={s['net']:.0f} pf={s['pf']} dd={s['max_dd']:.0f}",
                flush=True,
            )

        # ±20% sensitivity
        sens_cliff = False
        if c["kind"] == "timestop" and "real-prem" in annotated:
            lines += ["", "Sensitivity ±20% on X and N (real-prem):", "",
                      "| X | N | trades | net ₹ | PF | max DD |", "|---:|---:|---:|---:|---:|---:|"]
            base_s = None
            axis = []
            for x in (0.04, 0.05, 0.06):
                for n in (16, 20, 24):
                    nets = apply_timestop(annotated["real-prem"], x, n, tape)
                    s = _stats(nets)
                    lines.append(
                        f"| {x:.0%} | {n} | {s['trades']} | {_f(s['net'])} | {_f(s['pf'], 2)} | {_f(s['max_dd'])} |"
                    )
                    if abs(x - 0.05) < 1e-9 and n == 20:
                        base_s = s
                    if (abs(x - 0.05) < 1e-9 and n in (16, 24)) or (n == 20 and abs(x - 0.05) > 1e-9):
                        axis.append(s)
            if base_s and base_s["net"]:
                worst = 0.0
                for s in axis:
                    worst = max(worst, abs((s["net"] - base_s["net"]) / abs(base_s["net"]) * 100))
                sens_cliff = worst > 40
                lines.append(f"Axis max move vs (5%,20m): {_f(worst, 1)}% → {'**cliff**' if sens_cliff else 'ok'}.")
        elif c["kind"] == "skip" and "real-prem" in annotated:
            # trivial sensitivity: N/A for pure skip; mark ok
            lines.append("")
            lines.append("Skip filter: no continuous param; sensitivity N/A (no cliff).")

        accept = ok_both and kept_ok and not sens_cliff and "research" in annotated and "real-prem" in annotated
        lines.append("")
        lines.append(
            f"**Verdict:** {'ACCEPT' if accept else 'reject'} "
            f"(PF↑ and DD↑ both windows={ok_both}, kept≥70%={kept_ok}, no cliff={not sens_cliff})."
        )
        lines.append("")
        if accept:
            accepted.append({**c, "stats": window_stats})

    # Holdout for single best accepted
    lines += ["## Holdout", ""]
    if not accepted:
        lines.append("No accepted change — holdout not run.")
        lines.append("")
    else:
        # best by real-prem PF improvement
        def score(c: dict[str, Any]) -> float:
            b = baselines["real-prem"]["pf"] or 0
            s = c["stats"]["real-prem"]["pf"] or 0
            return s - b

        best = max(accepted, key=score)
        lines.append(f"Best accepted: **{best['name']}**. Running holdout {holdout[0]}..{holdout[1]} with baseline S5, then applying the same change post-hoc.")
        print(f"=== holdout for {best['name']} ===", flush=True)
        hout = _run(holdout[0], holdout[1], holdout=True)
        hctx = _build_context(holdout[0], holdout[1])
        hrows = _annotate(hout["result"]["trades"], hctx)
        base_h = _stats([r["net"] for r in hrows])
        if best["kind"] == "skip":
            nets_h = apply_skip(hrows, best["pred"])
        else:
            nets_h = apply_timestop(hrows, float(best["x"]), int(best["n"]), tape)
        s_h = _stats(nets_h)
        lines += [
            "",
            "| run | trades | win % | net ₹ | PF | max DD | −top10 |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| holdout baseline | {base_h['trades']} | {_f(base_h['win_pct'], 1)} | {_f(base_h['net'])} | {_f(base_h['pf'], 2)} | {_f(base_h['max_dd'])} | {_f(base_h['without_top10'])} |",
            f"| holdout + {best['name']} | {s_h['trades']} | {_f(s_h['win_pct'], 1)} | {_f(s_h['net'])} | {_f(s_h['pf'], 2)} | {_f(s_h['max_dd'])} | {_f(s_h['without_top10'])} |",
            "",
        ]
        print(f"  holdout base net={base_h['net']:.0f} → {s_h['net']:.0f}", flush=True)

    lines += [
        "## Summary verdict",
        "",
        "Slot 5 config was not modified. Research-only post-hoc skips / tape-based time-stop.",
        "",
    ]
    if accepted:
        lines.append("Accepted: " + ", ".join(c["name"] for c in accepted) + ".")
    else:
        lines.append("**No change accepted.** Keep S5-QM-long as-is.")
    lines.append("")

    text = "\n".join(lines)
    print("\n" + text)
    out_dir = settings.data_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"s5_edge_diagnosis_{date.today().isoformat()}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    main()
