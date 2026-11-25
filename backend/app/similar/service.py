"""Build the feature bank and answer one query. Pure aside from the candle read and the cache directory."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.data.service import get_candles
from app.data.store import CandleStore
from app.similar.cache import (
    bank_path,
    days_reliability_path,
    load_bank,
    load_json,
    reliability_path,
    save_bank,
    save_json,
)
from app.similar.days import build_day_rows, match_days, walk_days
from app.similar.features import (
    ALLOWED_TIMEFRAMES,
    CONTEXT_NOTE,
    DEFAULT_WINDOW,
    HORIZONS,
    INDEX_SYMBOLS,
    TIME_CAP_S,
    VIX_SYMBOL,
    bar_seconds,
    candle_features,
    date_of,
    ist_day,
    similarity,
    stack_windows,
    wilder_atr,
    window_ends,
)
from app.similar.match import (
    base_returns,
    euclidean,
    excursion,
    forward_slice,
    pick_matches,
    rerank_dtw,
    summary,
)
from app.similar.walkforward import run_walkforward

log = logging.getLogger("tradeboss.similar")
IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_RULE = "Intraday windows that cross the overnight gap are excluded. The gap is not a feature."


def _arrays(candles: list[dict]) -> dict[str, np.ndarray]:
    return {
        "times": np.array([c["time"] for c in candles], dtype=np.int64),
        "open": np.array([c["open"] for c in candles], dtype=np.float64),
        "high": np.array([c["high"] for c in candles], dtype=np.float64),
        "low": np.array([c["low"] for c in candles], dtype=np.float64),
        "close": np.array([c["close"] for c in candles], dtype=np.float64),
    }


def load_normal(store: CandleStore, symbol: str, timeframe: str, as_of: int | None = None) -> dict[str, np.ndarray]:
    """Normal sessions only. A replay ``as_of`` drops every bar that starts after it."""
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {ALLOWED_TIMEFRAMES}")
    result = get_candles(store, symbol, timeframe, session_types=("normal",), cursor=as_of)
    return _arrays(result.candles)


def build_bank_arrays(bars: dict[str, np.ndarray], window: int, timeframe: str) -> dict[str, np.ndarray]:
    features = candle_features(bars["open"], bars["high"], bars["low"], bars["close"])
    ends = window_ends(bars["times"], window, timeframe)
    return {
        "times": bars["times"],
        "open": bars["open"],
        "high": bars["high"],
        "low": bars["low"],
        "close": bars["close"],
        "ends": ends,
        "vectors": stack_windows(features, ends, window),
        "atr": wilder_atr(bars["high"], bars["low"], bars["close"]),
    }


def ensure_bank(store: CandleStore, cache_dir: Path, symbol: str, timeframe: str, window: int) -> dict[str, np.ndarray]:
    path = bank_path(cache_dir, symbol, timeframe, window)
    cached = load_bank(path)
    if cached is not None and "vectors" in cached:
        return cached
    log.info("building similar-pattern bank %s %s window %s", symbol, timeframe, window)
    bars = load_normal(store, symbol, timeframe, as_of=None)
    payload = build_bank_arrays(bars, window, timeframe)
    save_bank(path, payload)
    return payload


def _prefix(bank: dict[str, np.ndarray], as_of: int | None) -> dict[str, np.ndarray]:
    if as_of is None:
        return bank
    keep = int(np.searchsorted(bank["times"], int(as_of), side="right"))
    ends = bank["ends"]
    ok = ends < keep
    return {
        "times": bank["times"][:keep],
        "open": bank["open"][:keep],
        "high": bank["high"][:keep],
        "low": bank["low"][:keep],
        "close": bank["close"][:keep],
        "atr": bank["atr"][:keep],
        "ends": ends[ok],
        "vectors": bank["vectors"][ok],
    }


def answer_candles(
    store: CandleStore,
    cache_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    window: int = DEFAULT_WINDOW,
    as_of: int | None = None,
    method: str = "euclidean",
) -> dict:
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {ALLOWED_TIMEFRAMES}")
    if window < 5 or window > 200:
        raise ValueError("window must be between 5 and 200")
    if method not in ("euclidean", "dtw"):
        raise ValueError("method must be euclidean or dtw")
    # Replay: rebuild is not required. The cached bank is sliced at as_of, and a
    # bar that starts after as_of never enters the slice. When the cache is cold
    # and a cursor is set, load only through the cursor so the build itself cannot
    # see later bars.
    path = bank_path(cache_dir, symbol, timeframe, window)
    if load_bank(path) is None and as_of is not None:
        bars = load_normal(store, symbol, timeframe, as_of=as_of)
        bank = build_bank_arrays(bars, window, timeframe)
    else:
        bank = _prefix(ensure_bank(store, cache_dir, symbol, timeframe, window), as_of)
    intraday = timeframe != "1D"
    empty = {
        "symbol": symbol,
        "timeframe": timeframe,
        "window": window,
        "as_of": as_of,
        "method": method,
        "note": CONTEXT_NOTE,
        "window_rule": WINDOW_RULE,
        "query": None,
        "matches": [],
        "summary": {},
        "reliability": load_json(reliability_path(cache_dir, symbol, timeframe, window)),
    }
    if len(bank["ends"]) == 0:
        session_bars = {"5m": 75, "15m": 25, "1h": 6}.get(timeframe)
        if intraday and session_bars is not None and window >= session_bars:
            empty["empty_reason"] = (
                f"A {window}-bar {timeframe} window does not fit in one cash session "
                f"({session_bars} bars) without crossing the overnight gap."
            )
        return empty
    query_pos = len(bank["ends"]) - 1
    q_end = int(bank["ends"][query_pos])
    q_start_i = q_end - window + 1
    query_start = int(bank["times"][q_start_i])
    distances = euclidean(bank["vectors"][query_pos], bank["vectors"])
    shortlist = pick_matches(distances, bank["times"][bank["ends"]], query_start=query_start, k=200 if method == "dtw" else 10)
    if method == "dtw" and shortlist:
        features = candle_features(bank["open"], bank["high"], bank["low"], bank["close"])
        shortlist = rerank_dtw(features, bank["ends"], q_end, window, shortlist)[:10]
    dim = int(bank["vectors"].shape[1])
    per_horizon: dict[int, list[float]] = {h: [] for h in HORIZONS}
    matches = []
    for pos in shortlist:
        end = int(bank["ends"][pos])
        start_i = end - window + 1
        if method == "dtw":
            features = candle_features(bank["open"], bank["high"], bank["low"], bank["close"])
            from app.similar.match import dtw_band, dtw_distance

            dist = dtw_distance(
                features[q_start_i : q_end + 1],
                features[start_i : end + 1],
                dtw_band(window),
            )
        else:
            dist = float(distances[pos])
        forwards = {}
        for horizon in HORIZONS:
            span = forward_slice(bank["times"], end, horizon, as_of=as_of, intraday=intraday)
            if span is None:
                forwards[str(horizon)] = None
                continue
            _, last = span
            stats = excursion(bank["high"], bank["low"], bank["close"], bank["atr"], end, last)
            forwards[str(horizon)] = {key: round(value, 4) for key, value in stats.items()}
            per_horizon[horizon].append(stats["return_pct"])
        matches.append({
            "start": int(bank["times"][start_i]),
            "end": int(bank["times"][end]),
            "forward_end": int(bank["times"][min(end + 20, len(bank["times"]) - 1)]),
            "similarity": round(similarity(dist, dim), 1),
            "forward": forwards,
        })
    summary_block = {}
    for horizon in HORIZONS:
        matched = summary(np.asarray(per_horizon[horizon], dtype=np.float64))
        base = summary(base_returns(
            bank["close"], bank["times"], bank["ends"], horizon, as_of=as_of, intraday=intraday,
        ))
        summary_block[str(horizon)] = {"matches": matched, "base": base}
    # The query's own forward is not a match. Clip a forward_end that would pass as_of.
    forward_end = int(bank["times"][min(q_end + 20, len(bank["times"]) - 1)])
    empty.update({
        "query": {"start": query_start, "end": int(bank["times"][q_end]), "forward_end": forward_end},
        "matches": matches,
        "summary": summary_block,
    })
    return empty


def compute_reliability(
    store: CandleStore,
    cache_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    window: int = DEFAULT_WINDOW,
    time_cap_s: float = TIME_CAP_S,
) -> dict:
    bank = ensure_bank(store, cache_dir, symbol, timeframe, window)
    report = run_walkforward(
        bank["times"],
        bank["close"],
        bank["vectors"],
        bank["ends"],
        timeframe,
        time_cap_s=time_cap_s,
    )
    report["symbol"] = symbol
    report["timeframe"] = timeframe
    report["window"] = window
    report["window_rule"] = WINDOW_RULE
    report["holdout_excluded"] = "2026-07-01..2026-10-01"
    save_json(reliability_path(cache_dir, symbol, timeframe, window), report)
    return report


def _clock_minute(at: str, as_of: int | None) -> int:
    if at == "09:30":
        return 9 * 60 + 30
    if at == "10:15":
        return 10 * 60 + 15
    if at == "now":
        if as_of is None:
            return 10 * 60 + 15
        local = datetime.fromtimestamp(int(as_of), tz=timezone.utc).astimezone(IST)
        return local.hour * 60 + local.minute
    raise ValueError("at must be 09:30, 10:15, or now")


def _vix_bars(store: CandleStore, cache_dir: Path, as_of: int | None) -> dict[str, np.ndarray]:
    path = cache_dir / f"{VIX_SYMBOL}_5m_v1.npz"
    cached = load_bank(path)
    if cached is None:
        try:
            loaded = load_normal(store, VIX_SYMBOL, "5m", as_of=None)
        except Exception:
            loaded = {"times": np.empty(0, dtype=np.int64), "close": np.empty(0, dtype=np.float64)}
        save_bank(path, {"times": loaded["times"], "close": loaded["close"]})
        cached = loaded
    if as_of is None or len(cached["times"]) == 0:
        return {"times": cached["times"], "close": cached["close"]}
    keep = int(np.searchsorted(cached["times"], int(as_of), side="right"))
    return {"times": cached["times"][:keep], "close": cached["close"][:keep]}


def answer_days(
    store: CandleStore,
    cache_dir: Path,
    *,
    symbol: str,
    at: str = "10:15",
    as_of: int | None = None,
) -> dict:
    if symbol not in INDEX_SYMBOLS:
        raise ValueError("similar days are only for NIFTY50, SENSEX, and BANKNIFTY")
    clock = _clock_minute(at, as_of)
    # The 5m bank is the cached feature source. Slicing at as_of drops later bars.
    bars = _prefix(ensure_bank(store, cache_dir, symbol, "5m", DEFAULT_WINDOW), as_of)
    vix = _vix_bars(store, cache_dir, as_of)
    rows = build_day_rows(
        bars["times"], bars["open"], bars["high"], bars["low"], bars["close"],
        clock_minute=clock,
        vix_times=vix["times"],
        vix_close=vix["close"],
    )
    if as_of is not None:
        rows = [row for row in rows if row["known_through"] <= as_of]
    if as_of is None:
        query_day = rows[-1]["day"] if rows else None
    else:
        cursor_day = int(ist_day(np.array([as_of], dtype=np.int64))[0])
        known = [row for row in rows if row["day"] <= cursor_day]
        query_day = known[-1]["day"] if known else None
    if query_day is None:
        found = {"matches": [], "outcomes": {}, "columns": []}
    else:
        found = match_days(rows, query_day=query_day)
    return {
        "symbol": symbol,
        "at": at,
        "clock": f"{clock // 60:02d}:{clock % 60:02d}",
        "as_of": as_of,
        "date": None if query_day is None else date_of(query_day).isoformat(),
        "note": CONTEXT_NOTE,
        "window_rule": "Features stop at the chosen clock. Outcomes are how the earlier session ended.",
        "matches": found["matches"],
        "outcomes": found["outcomes"],
        "reliability": load_json(days_reliability_path(cache_dir, symbol)),
    }


def compute_days_reliability(store: CandleStore, cache_dir: Path, *, symbol: str, time_cap_s: float = TIME_CAP_S) -> dict:
    if symbol not in INDEX_SYMBOLS:
        raise ValueError("similar days are only for NIFTY50, SENSEX, and BANKNIFTY")
    bars = load_normal(store, symbol, "5m", as_of=None)
    try:
        vix = load_normal(store, VIX_SYMBOL, "5m", as_of=None)
    except Exception:
        vix = {"times": np.empty(0, dtype=np.int64), "close": np.empty(0, dtype=np.float64)}
    rows = build_day_rows(
        bars["times"], bars["open"], bars["high"], bars["low"], bars["close"],
        clock_minute=10 * 60 + 15,
        vix_times=vix["times"],
        vix_close=vix["close"],
    )
    report = walk_days(rows, time_cap_s=time_cap_s)
    report["symbol"] = symbol
    report["at"] = "10:15"
    report["note"] = CONTEXT_NOTE
    save_json(days_reliability_path(cache_dir, symbol), report)
    return report

