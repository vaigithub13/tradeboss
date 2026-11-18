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


def _leg_quote(row: dict[str, Any], side: str) -> tuple[float | None, str | None]:
    """Prefer bid/ask mid; fall back to LTP. Returns (price, 'mid'|'ltp')."""
    block = row.get(side) or {}
    if not isinstance(block, dict):
        return None, None
    md = block.get("market_data") if isinstance(block.get("market_data"), dict) else block
    if not isinstance(md, dict):
        return None, None

    def _f(key: str) -> float | None:
        raw = md.get(key)
        try:
            v = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
        return v if v is not None and v > 0 else None

    bid, ask, ltp = _f("bid_price"), _f("ask_price"), _f("ltp")
    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 2), "mid"
    if ltp is not None:
        return round(ltp, 2), "ltp"
    return None, None


def _atm_straddle_move(
    chain: list[dict[str, Any]],
    spot: float,
    *,
    step: int = 100,
    expiry: date | None = None,
) -> dict[str, Any] | None:
    """ATM CE+PE mid (else LTP) → expected one-day move from the straddle.

    ATM is the listed strike nearest `spot` (tie → lower), snapped with the BSE step when a
    matching listed strike exists. Never uses CPR / prior-day close.
    """
    from app.options.chain_view import atm_strike as nearest_listed
    from app.options.strikes import atm_strike as snap_step

    by_strike: dict[float, dict[str, Any]] = {}
    for row in chain:
        try:
            k = float(row.get("strike_price") or row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if k <= 0:
            continue
        by_strike[k] = row
    strikes = sorted(by_strike)
    if not strikes or spot <= 0:
        return None
    snapped = snap_step(spot, step)
    atm = snapped if snapped in by_strike else nearest_listed(spot, strikes)
    row = by_strike.get(atm)
    if not row:
        return None

    ce, ce_src = _leg_quote(row, "call_options")
    pe, pe_src = _leg_quote(row, "put_options")
    if ce is None or pe is None:
        return None
    premium = round(ce + pe, 2)
    return {
        "atm": atm,
        "spot": round(spot, 2),
        "expiry": expiry.isoformat() if expiry else None,
        "step": step,
        "call_price": ce,
        "put_price": pe,
        "call_source": ce_src,
        "put_source": pe_src,
        # Compat aliases for the first Sensex panel build
        "call_ltp": ce,
        "put_ltp": pe,
        "straddle": premium,
        "expected_move": premium,
        "range": [round(spot - premium, 2), round(spot + premium, 2)],
    }


def _latest_index_price(store: Any, symbol: str, day: date) -> float | None:
    """Latest Sensex price: today's last 1m close (live overlay during hours, session close after)."""
    from app.data.service import get_candle_page, get_candles

    start = _day_start(day)
    try:
        candles = list(
            get_candles(
                store, symbol, "1m", from_time=start, to_time=None,
                session_types=("normal", "weekend_full"),
            ).candles
        )
    except Exception:
        candles = []
    if candles:
        return float(candles[-1]["close"])
    try:
        page = get_candle_page(store, symbol, "1m", limit=1, session_types=["normal", "weekend_full"])
        if page.candles:
            return float(page.candles[-1]["close"])
    except Exception:
        return None
    return None


def _sensex_step(on: date) -> int:
    from app.options.strikes import StrikeStepUnknown, load_default_step_table

    try:
        return load_default_step_table().step("SENSEX", on)
    except StrikeStepUnknown:
        return 100


def build_sensex_for(
    day: date,
    *,
    data_dir: Path,
    store: Any,
    clock: Callable[[], float] = time.time,
    chain_fetcher: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Sensex market view: prior-day CPR/pivots + ATM straddle from the latest index price."""
    from app.market_view.view import day_type, levels_of, score_structure, structure
    from app.options.chain_view import chain_spot_from_rows
    from app.upstox.instruments import SENSEX_INDEX_KEY

    daily = _daily(store, SENSEX, day)
    st = structure(daily)
    factors = [score_structure(st)]
    total = sum(f["score"] for f in factors if f["score"] is not None)
    bias = {2: "Bullish", 1: "Mild bullish", 0: "Neutral", -1: "Mild bearish", -2: "Bearish"}.get(
        max(-2, min(2, total if abs(total) < 2 else (2 if total > 0 else -2))),
        "Neutral",
    )
    straddle: dict[str, Any] | None = None
    expiry: date | None = None
    try:
        fetched = chain_fetcher() if chain_fetcher is not None else _fetch_sensex_chain()
        if isinstance(fetched, tuple) and len(fetched) == 2:
            expiry, rows = fetched[0], fetched[1]
        else:
            rows = list(fetched or [])
        # Spot: live underlying from the chain when present, else latest 1m close — never CPR prev_close.
        spot = chain_spot_from_rows(rows) if rows else None
        if spot is None:
            spot = _latest_index_price(store, SENSEX, day)
        if rows and spot is not None and spot > 0:
            straddle = _atm_straddle_move(rows, float(spot), step=_sensex_step(day), expiry=expiry)
    except Exception:
        straddle = None
    rng = tuple(straddle["range"]) if straddle and straddle.get("range") else None
    dtype, dreason = day_type(st, None)
    exp_txt = f" exp {straddle['expiry']}" if straddle and straddle.get("expiry") else ""
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
            f"ATM {straddle['atm']:,.0f}{exp_txt} straddle ~{straddle['expected_move']:.0f} pts "
            f"(spot {straddle['spot']:,.0f})."
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


def _fetch_sensex_chain() -> tuple[date | None, list[dict[str, Any]]]:
    """Nearest weekly Sensex option chain (expiry ASC from Upstox)."""
    from app.config import settings
    from app.upstox.deps import make_client
    from app.upstox.instruments import SENSEX_INDEX_KEY

    client = make_client()
    if client is None:
        return None, []
    key = settings.sensex_index_key or SENSEX_INDEX_KEY
    expiries = client.option_contracts(key)
    if not expiries:
        return None, []
    exp = expiries[0]
    return exp, client.option_chain(key, exp)
