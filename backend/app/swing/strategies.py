"""Five daily long-only rules. Parameters below are the defaults. Nothing here is tuned.

Every signal is known at the close of day D. The book buys the next session's open.
Nifty 500 must be above its 200-day average on day D. The name must pass the
point-in-time filters that day. Volume averages exclude day D, so the signal
day does not inflate its own average.

S1 Breakout: close above the prior 20-session high, volume above 1.5× that
average. Stop is the entry open minus 2×ATR(14). Out by the fifth close.
S2 Momentum: on the last session of the week, 120-session return skipping the
last 5 sessions. The top 5 are the candidates. Stop 2×ATR. Out at the close of
the last session of the entry week.
S3 Pullback: close above the 200-day and the 50-day average, RSI(2) under 10.
Out at the first close above the 5-day average, else the fifth close. Stop 2.5×ATR.
S4 Near the high: close within 3% of the 252-session high, and the 20-day
average turnover is above its value 20 sessions earlier. Stop 2×ATR.
S5 Gap-and-go: open at least 4% above the previous close, volume at least 3×
the average, close in the top 30% of the day's range. Stop is the gap day's low.
"""

from __future__ import annotations

from collections import deque
from datetime import date

import numpy as np
import pandas as pd

from app.swing.engine import _wilder, nifty_above
from app.swing.fixes import as_date

REGIME_SMA = 200
MAX_HOLD = 5
TOP_N = 5

DEFAULTS = {
    "S1": {"breakout_n": 20, "vol_mult": 1.5, "atr_mult": 2.0},
    "S2": {"mom_n": 120, "skip": 5, "top_n": TOP_N, "atr_mult": 2.0},
    "S3": {"rsi_max": 10.0, "sma_fast": 50, "sma_slow": 200, "exit_sma": 5, "atr_mult": 2.5},
    "S4": {"band": 0.03, "high_n": 252, "atr_mult": 2.0},
    "S5": {"gap": 0.04, "vol_mult": 3.0, "top_pct": 0.30},
}

NAMES = {
    "S1": "Breakout",
    "S2": "Momentum rank",
    "S3": "Pullback",
    "S4": "Near 52-week high",
    "S5": "Gap-and-go",
}


def group_bounds(lineage: np.ndarray) -> list[tuple[int, int]]:
    if len(lineage) == 0:
        return []
    cuts = np.flatnonzero(lineage[1:] != lineage[:-1]) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(lineage)]))
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _rolling_max(values: np.ndarray, n: int, *, include_today: bool) -> np.ndarray:
    out = np.full(len(values), np.nan)
    window: deque[int] = deque()
    for i, price in enumerate(values):
        left = i - n + (1 if include_today else 0)
        while window and window[0] < left:
            window.popleft()
        if include_today:
            while window and values[window[-1]] <= price:
                window.pop()
            window.append(i)
            if i >= n - 1:
                out[i] = values[window[0]]
        else:
            if i >= n and window:
                out[i] = values[window[0]]
            while window and values[window[-1]] <= price:
                window.pop()
            window.append(i)
    return out


def _rolling_mean(values: np.ndarray, n: int, *, include_today: bool) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if n < 1 or len(values) < n:
        return out
    total = np.cumsum(values)
    if include_today:
        for i in range(n - 1, len(values)):
            prev = total[i - n] if i >= n else 0.0
            out[i] = (total[i] - prev) / n
        return out
    for i in range(n, len(values)):
        prev = total[i - n - 1] if i - n - 1 >= 0 else 0.0
        out[i] = (total[i - 1] - prev) / n
    return out


def _momentum(close: np.ndarray, lookback: int, skip: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    start = lookback + skip
    for i in range(start, len(close)):
        base = close[i - skip - lookback]
        if base > 0:
            out[i] = close[i - skip] / base - 1.0
    return out


def _rsi(close: np.ndarray, n: int = 2) -> np.ndarray:
    if len(close) <= n:
        return np.full(len(close), np.nan)
    change = np.diff(close)
    gains = np.clip(change, 0, None)
    losses = np.clip(-change, 0, None)
    avg_gain = _wilder(gains.tolist(), n)
    avg_loss = _wilder(losses.tolist(), n)
    out = np.full(len(close), np.nan)
    for i, (gain, loss) in enumerate(zip(avg_gain, avg_loss, strict=True)):
        if gain is None or loss is None:
            continue
        if loss == 0:
            out[i + 1] = 100.0 if gain > 0 else 50.0
        else:
            rs = gain / loss
            out[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _apply(values: np.ndarray, bounds, fn) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for start, end in bounds:
        out[start:end] = fn(values[start:end])
    return out


def prepare_tape(frame: pd.DataFrame, nifty_close: pd.Series) -> pd.DataFrame:
    """Causal columns used by every default rule. The frame is sorted by lineage, date."""
    work = frame.sort_values(["lineage", "date"]).reset_index(drop=True)
    work["date"] = work["date"].map(as_date)
    work["lineage"] = work["lineage"].astype(str)
    work["passes"] = work["passes"].fillna(False).astype(bool) if "passes" in work.columns else False
    work["size"] = work["size"].fillna("rest").astype(str) if "size" in work.columns else "rest"
    bounds = group_bounds(work["lineage"].to_numpy())
    close = work["px_close"].to_numpy(dtype=float)
    high = work["px_high"].to_numpy(dtype=float)
    low = work["px_low"].to_numpy(dtype=float)
    volume = work["volume"].to_numpy(dtype=float)
    turnover = work["turnover"].to_numpy(dtype=float) if "turnover" in work.columns else volume * close

    def per_group(column: np.ndarray, fn) -> np.ndarray:
        return _apply(column, bounds, fn)

    work["atr"] = per_group(np.arange(len(work)), lambda idx: _atr_slice(high, low, close, idx))
    work["rsi2"] = per_group(close, lambda s: _rsi(s, 2))
    work["sma4"] = per_group(close, lambda s: _rolling_mean(s, 4, include_today=True))
    work["sma5"] = per_group(close, lambda s: _rolling_mean(s, 5, include_today=True))
    work["sma6"] = per_group(close, lambda s: _rolling_mean(s, 6, include_today=True))
    work["sma50"] = per_group(close, lambda s: _rolling_mean(s, 50, include_today=True))
    work["sma200"] = per_group(close, lambda s: _rolling_mean(s, 200, include_today=True))
    work["prior20"] = per_group(high, lambda s: _rolling_max(s, 20, include_today=False))
    work["vol20"] = per_group(volume, lambda s: _rolling_mean(s, 20, include_today=False))
    work["turn20"] = per_group(turnover, lambda s: _rolling_mean(s, 20, include_today=True))
    work["turn20_lag"] = per_group(turnover, lambda s: _lag(s, _rolling_mean(s, 20, include_today=True), 20))
    work["high252"] = per_group(high, lambda s: _rolling_max(s, 252, include_today=True))
    work["mom"] = per_group(close, lambda s: _momentum(s, 120, 5))
    previous = per_group(close, lambda s: np.concatenate(([np.nan], s[:-1])) if len(s) else s)
    work["prev_close"] = previous
    span = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        pos = np.where(span > 0, (close - low) / span, np.nan)
    work["range_pos"] = pos
    regime = nifty_above(nifty_close, REGIME_SMA)
    work["regime"] = [bool(regime.get(day, False)) for day in work["date"]]
    work.attrs["bounds"] = bounds
    return work


def _atr_slice(high, low, close, idx: np.ndarray) -> np.ndarray:
    h = high[idx]
    lo = low[idx]
    cl = close[idx]
    tr = []
    for i in range(len(idx)):
        if i == 0:
            tr.append(h[i] - lo[i])
        else:
            tr.append(max(h[i] - lo[i], abs(h[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])))
    values = _wilder(tr, 14)
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def _lag(values: np.ndarray, rolled: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) > n:
        out[n:] = rolled[:-n]
    return out


def week_end_dates(calendar: list[date]) -> set[date]:
    """Last trading session of each ISO week."""
    ends = set()
    for i, day in enumerate(calendar):
        nxt = calendar[i + 1] if i + 1 < len(calendar) else None
        if nxt is None or nxt.isocalendar()[:2] != day.isocalendar()[:2]:
            ends.add(day)
    return ends


def _pack(work: pd.DataFrame, mask: np.ndarray, score: np.ndarray, atr_mult: float, exit_kind: str, stop: np.ndarray | None) -> pd.DataFrame:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return pd.DataFrame(columns=["lineage", "symbol", "date", "score", "size", "atr", "atr_mult", "stop_price", "exit_kind", "max_hold"])
    return pd.DataFrame(
        {
            "lineage": work["lineage"].to_numpy()[idx],
            "symbol": work["symbol"].to_numpy()[idx],
            "date": work["date"].to_numpy()[idx],
            "score": score[idx],
            "size": work["size"].to_numpy()[idx],
            "atr": work["atr"].to_numpy(dtype=float)[idx],
            "atr_mult": atr_mult,
            "stop_price": np.nan if stop is None else stop[idx],
            "exit_kind": exit_kind,
            "max_hold": MAX_HOLD,
        }
    )


def _alive(work: pd.DataFrame) -> np.ndarray:
    atr = work["atr"].to_numpy(dtype=float)
    return work["passes"].to_numpy(dtype=bool) & work["regime"].to_numpy(dtype=bool) & np.isfinite(atr) & (atr > 0)


def signals_s1(work: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    p = {**DEFAULTS["S1"], **(params or {})}
    prior = work["prior20"].to_numpy(dtype=float) if p["breakout_n"] == 20 else _column(work, "px_high", lambda s: _rolling_max(s, int(p["breakout_n"]), include_today=False))
    vol = work["vol20"].to_numpy(dtype=float)
    close = work["px_close"].to_numpy(dtype=float)
    volume = work["volume"].to_numpy(dtype=float)
    mask = _alive(work) & np.isfinite(prior) & (prior > 0) & (close > prior) & np.isfinite(vol) & (vol > 0) & (volume > p["vol_mult"] * vol)
    return _pack(work, mask, close / prior - 1.0, float(p["atr_mult"]), "time", None)


def signals_s2(work: pd.DataFrame, week_ends: set[date], params: dict | None = None) -> pd.DataFrame:
    p = {**DEFAULTS["S2"], **(params or {})}
    if p["mom_n"] == 120 and p["skip"] == 5:
        mom = work["mom"].to_numpy(dtype=float)
    else:
        mom = _column(work, "px_close", lambda s: _momentum(s, int(p["mom_n"]), int(p["skip"])))
    ends = work["date"].map(lambda day: day in week_ends).to_numpy(dtype=bool)
    mask = _alive(work) & ends & np.isfinite(mom)
    packed = _pack(work, mask, mom, float(p["atr_mult"]), "week", None)
    if packed.empty:
        return packed
    packed = packed.sort_values(["date", "score", "lineage"], ascending=[True, False, True])
    return packed.groupby("date", sort=False).head(int(p["top_n"])).reset_index(drop=True)


def signals_s3(work: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    p = {**DEFAULTS["S3"], **(params or {})}
    close = work["px_close"].to_numpy(dtype=float)
    fast = work["sma50"].to_numpy(dtype=float) if p["sma_fast"] == 50 else _column(work, "px_close", lambda s: _rolling_mean(s, int(p["sma_fast"]), include_today=True))
    slow = work["sma200"].to_numpy(dtype=float) if p["sma_slow"] == 200 else _column(work, "px_close", lambda s: _rolling_mean(s, int(p["sma_slow"]), include_today=True))
    rsi = work["rsi2"].to_numpy(dtype=float)
    mask = _alive(work) & np.isfinite(fast) & np.isfinite(slow) & (close > fast) & (close > slow) & np.isfinite(rsi) & (rsi < p["rsi_max"])
    packed = _pack(work, mask, -rsi, float(p["atr_mult"]), "sma", None)
    if not packed.empty:
        packed["exit_sma"] = int(p["exit_sma"])
    return packed


def signals_s4(work: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    p = {**DEFAULTS["S4"], **(params or {})}
    close = work["px_close"].to_numpy(dtype=float)
    high_n = work["high252"].to_numpy(dtype=float) if p["high_n"] == 252 else _column(work, "px_high", lambda s: _rolling_max(s, int(p["high_n"]), include_today=True))
    turn = work["turn20"].to_numpy(dtype=float)
    lag = work["turn20_lag"].to_numpy(dtype=float)
    mask = (
        _alive(work)
        & np.isfinite(high_n)
        & (high_n > 0)
        & (close >= high_n * (1.0 - p["band"]))
        & np.isfinite(turn)
        & np.isfinite(lag)
        & (turn > lag)
    )
    return _pack(work, mask, close / high_n, float(p["atr_mult"]), "time", None)


def signals_s5(work: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    p = {**DEFAULTS["S5"], **(params or {})}
    open_ = work["px_open"].to_numpy(dtype=float)
    prev = work["prev_close"].to_numpy(dtype=float)
    vol = work["vol20"].to_numpy(dtype=float)
    volume = work["volume"].to_numpy(dtype=float)
    pos = work["range_pos"].to_numpy(dtype=float)
    low = work["px_low"].to_numpy(dtype=float)
    threshold = 1.0 - float(p["top_pct"])
    mask = (
        _alive(work)
        & np.isfinite(prev)
        & (prev > 0)
        & (open_ >= prev * (1.0 + p["gap"]))
        & np.isfinite(vol)
        & (vol > 0)
        & (volume >= p["vol_mult"] * vol)
        & np.isfinite(pos)
        & (pos >= threshold)
    )
    score = open_ / prev - 1.0
    return _pack(work, mask, score, 0.0, "time", low)


def make_signals(kind: str, work: pd.DataFrame, week_ends: set[date], params: dict | None = None) -> pd.DataFrame:
    if kind == "S1":
        return signals_s1(work, params)
    if kind == "S2":
        return signals_s2(work, week_ends, params)
    if kind == "S3":
        return signals_s3(work, params)
    if kind == "S4":
        return signals_s4(work, params)
    if kind == "S5":
        return signals_s5(work, params)
    raise ValueError(f"unknown strategy {kind}")


def _column(work: pd.DataFrame, source: str, fn) -> np.ndarray:
    values = work[source].to_numpy(dtype=float)
    bounds = work.attrs.get("bounds") or group_bounds(work["lineage"].to_numpy())
    return _apply(values, bounds, fn)
