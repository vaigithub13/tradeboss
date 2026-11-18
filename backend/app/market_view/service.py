"""Gather the inputs, build the view, save it to data/market-view/<day>.json, and score it after the close."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.market_view import sources
from app.market_view.view import build_view, score_outcome
from app.options.time import IST

NIFTY = "NIFTY50"
SENSEX = "SENSEX"
VIX = "NSE_INDEX_India_VIX"
DAILY_LOOKBACK_DAYS = 60


def view_dir(data_dir: Path) -> Path:
    return data_dir / "market-view"


def sensex_view_dir(data_dir: Path) -> Path:
    return data_dir / "market-view" / "sensex"


def load_view(data_dir: Path, day: date) -> dict[str, Any] | None:
    path = view_dir(data_dir) / f"{day.isoformat()}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def load_sensex_view(data_dir: Path, day: date) -> dict[str, Any] | None:
    path = sensex_view_dir(data_dir) / f"{day.isoformat()}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def save_view(data_dir: Path, view: dict[str, Any]) -> Path:
    d = view_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{view['day']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(view, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def save_sensex_view(data_dir: Path, view: dict[str, Any]) -> Path:
    d = sensex_view_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{view['day']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(view, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def _day_start(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())


def _daily(store: Any, symbol: str, before: date) -> list[dict[str, Any]]:
    from app.data.service import get_candles

    start = _day_start(before - timedelta(days=DAILY_LOOKBACK_DAYS))
    try:
        return list(get_candles(store, symbol, "1D", from_time=start, to_time=_day_start(before) - 1,
                                session_types=("normal",)).candles)
    except Exception:
        return []


def latest_gex(gex_dir: Path) -> dict[str, Any] | None:
    from app.options.gex_history import load_day, recorded_days

    for d in reversed(recorded_days(gex_dir)):
        rows = load_day(gex_dir, d)
        if rows:
            return rows[-1]
    return None


def build_for(
    day: date,
    *,
    data_dir: Path,
    store: Any,
    is_trading_day: Callable[[date], bool],
    http_factory: Callable[[], Any] = sources.make_http,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    daily = _daily(store, NIFTY, day)
    vix_daily = _daily(store, VIX, day)
    vix_last = float(vix_daily[-1]["close"]) if vix_daily else None
    vix_prev = float(vix_daily[-2]["close"]) if len(vix_daily) > 1 else None
    with http_factory() as http:
        quotes = {k: sources.fetch_quote(http, sym, name) for k, (sym, name) in sources.GLOBAL_SYMBOLS.items()}
        brent = sources.fetch_quote(http, *sources.BRENT)
        fii_cash = sources.fetch_fii_dii(http)
        fii_oi = sources.fetch_participant_oi(http, day, is_trading_day)
    view = build_view(
        day=day.isoformat(), built_at=int(clock()), quotes=quotes, brent=brent, fii_cash=fii_cash,
        fii_oi=fii_oi, vix_last=vix_last, vix_prev=vix_prev, daily=daily,
        gex=latest_gex(data_dir / "gex"),
    )
    save_view(data_dir, view)
    return view


def score_day(day: date, *, data_dir: Path, store: Any) -> dict[str, Any] | None:
    """Add the outcome to the saved view of `day` (from that day's 5m candles)."""
    from app.data.service import get_candles

    view = load_view(data_dir, day)
    if view is None:
        return None
    start = _day_start(day)
    try:
        candles = list(get_candles(store, NIFTY, "5m", from_time=start, to_time=start + 86399,
                                   session_types=("normal",)).candles)
    except Exception:
        candles = []
    outcome = score_outcome(view, candles)
    if outcome is None:
        return None
    view["outcome"] = outcome
    save_view(data_dir, view)
    return outcome


def history(data_dir: Path, limit: int = 60) -> dict[str, Any]:
    d = view_dir(data_dir)
    days = sorted((p for p in d.glob("*.json")), reverse=True)[:limit] if d.is_dir() else []
    rows = []
    for p in days:
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        o = v.get("outcome") or {}
        rows.append({"day": v.get("day"), "bias": v.get("bias"), "score": v.get("score"),
                     "day_type": v.get("day_type"), "bias_hit": o.get("bias_hit"),
                     "range_held": o.get("range_held"), "day_type_hit": o.get("day_type_hit"),
                     "move": o.get("move")})

    def rate(key: str) -> dict[str, Any]:
        vals = [r[key] for r in rows if r[key] is not None]
        return {"n": len(vals), "rate": (sum(1 for v in vals if v) / len(vals)) if vals else None}

    return {"days": rows, "bias_hit": rate("bias_hit"), "range_held": rate("range_held"),
            "day_type_hit": rate("day_type_hit")}


def _atm_straddle_move(chain: list[dict[str, Any]], spot: float) -> dict[str, Any] | None:
    """ATM CE+PE last prices → expected one-day move (points) from the straddle."""
    from app.options.chain_view import atm_strike

    strikes: list[float] = []
    by_strike: dict[float, dict[str, Any]] = {}
    for row in chain:
        try:
            k = float(row.get("strike_price") or row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if k <= 0:
            continue
        strikes.append(k)
        by_strike[k] = row
    if not strikes or spot <= 0:
        return None
    atm = atm_strike(spot, sorted(set(strikes)))
    row = by_strike.get(atm)
    if not row:
        return None

    def _ltp(side: str) -> float | None:
        block = row.get(side) or row.get(side.replace("_options", "")) or {}
        if not isinstance(block, dict):
            return None
        md = block.get("market_data") if isinstance(block.get("market_data"), dict) else block
        raw = md.get("ltp") if isinstance(md, dict) else None
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    ce = _ltp("call_options")
    pe = _ltp("put_options")
    if ce is None or pe is None:
        return None
    premium = ce + pe
    return {
        "atm": atm,
        "call_ltp": round(ce, 2),
        "put_ltp": round(pe, 2),
        "straddle": round(premium, 2),
        "expected_move": round(premium, 2),
        "range": [round(spot - premium, 2), round(spot + premium, 2)],
    }


def build_sensex_for(
    day: date,
    *,
    data_dir: Path,
    store: Any,
    clock: Callable[[], float] = time.time,
    chain_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Sensex market view: prior-day CPR/pivots + expected move from the ATM straddle."""
    from app.market_view.view import day_type, levels_of, score_structure, structure
    from app.upstox.instruments import SENSEX_INDEX_KEY

    daily = _daily(store, SENSEX, day)
    st = structure(daily)
    factors = [score_structure(st)]
    total = sum(f["score"] for f in factors if f["score"] is not None)
    bias = {2: "Bullish", 1: "Mild bullish", 0: "Neutral", -1: "Mild bearish", -2: "Bearish"}.get(
        max(-2, min(2, total if abs(total) < 2 else (2 if total > 0 else -2))),
        "Neutral",
    )
    spot = float(st["prev_close"]) if st else 0.0
    straddle: dict[str, Any] | None = None
    try:
        rows = chain_fetcher() if chain_fetcher is not None else _fetch_sensex_chain()
        if rows and spot > 0:
            straddle = _atm_straddle_move(rows, spot)
    except Exception:
        straddle = None
    rng = tuple(straddle["range"]) if straddle and straddle.get("range") else None
    dtype, dreason = day_type(st, None)
    view = {
        "day": day.isoformat(),
        "built_at": int(clock()),
        "symbol": SENSEX,
        "instrument_key": SENSEX_INDEX_KEY,
        "factors": factors,
        "score": total,
        "bias": bias,
        "expected_range": list(rng) if rng else None,
        "expected_move": straddle,
        "day_type": dtype,
        "day_type_reason": dreason,
        "levels": levels_of(st, rng),
        "plan": (
            f"Sensex: watch CPR {st['tc']:,.0f}/{st['bc']:,.0f}; "
            f"ATM straddle move ~{straddle['expected_move']:.0f} pts."
            if st and straddle
            else (
                f"Sensex: watch CPR {st['tc']:,.0f}/{st['bc']:,.0f}."
                if st
                else "Sensex market view: not enough daily candles yet."
            )
        ),
        "structure": st,
    }
    save_sensex_view(data_dir, view)
    return view


def _fetch_sensex_chain() -> list[dict[str, Any]]:
    from app.config import settings
    from app.upstox.deps import make_client
    from app.upstox.instruments import SENSEX_INDEX_KEY

    client = make_client()
    if client is None:
        return []
    key = settings.sensex_index_key or SENSEX_INDEX_KEY
    expiries = client.option_contracts(key)
    if not expiries:
        return []
    return client.option_chain(key, expiries[0])
