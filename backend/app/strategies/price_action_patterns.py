"""Pure price-action pattern detectors (closed bars only; no look-ahead).

Each candidate is known ONLY on its formation bar. Confirmation / invalidation are
checked later by the strategy (within `confirm_bars`, default 3) — never backdated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

Side = Literal["long", "short"]


@dataclass(frozen=True)
class OHLC:
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class PatternCandidate:
    """Setup formed at `end_index`. Strategy confirms on a later closed bar."""

    kind: str
    side: Side
    end_index: int
    confirm_level: float  # long: close > level; short: close < level
    invalid_level: float  # long: close < level; short: close > level
    pattern_high: float
    pattern_low: float
    #: Plus / long-legged: only the immediate next bar may confirm a breakout
    next_bar_only: bool = False


def body(b: OHLC) -> float:
    return abs(b.close - b.open)


def bar_range(b: OHLC) -> float:
    return b.high - b.low


def is_green(b: OHLC) -> bool:
    return b.close > b.open


def is_red(b: OHLC) -> bool:
    return b.close < b.open


def upper_wick(b: OHLC) -> float:
    return b.high - max(b.open, b.close)


def lower_wick(b: OHLC) -> float:
    return min(b.open, b.close) - b.low


def body_top(b: OHLC) -> float:
    return max(b.open, b.close)


def body_bot(b: OHLC) -> float:
    return min(b.open, b.close)


def avg_body(bars: Sequence[OHLC], end: int, n: int = 10) -> float | None:
    """Mean body of the last `n` bars ending at `end` (inclusive). Needs n bars."""
    if end + 1 < n or n < 1:
        return None
    vals = [body(bars[i]) for i in range(end - n + 1, end + 1)]
    return sum(vals) / n


def atr_wilder(bars: Sequence[OHLC], end: int, length: int = 14) -> float | None:
    """Wilder ATR at `end` (inclusive). Needs `length` true ranges ending at end."""
    if end < length or length < 1:
        return None
    trs: list[float] = []
    for i in range(end - length + 1, end + 1):
        b = bars[i]
        prev_c = bars[i - 1].close if i > 0 else b.close
        trs.append(max(b.high - b.low, abs(b.high - prev_c), abs(b.low - prev_c)))
    atr = trs[0]
    for tr in trs[1:]:
        atr = (atr * (length - 1) + tr) / length
    return atr


def ema_series(closes: Sequence[float], length: int = 20) -> list[float | None]:
    """EMA over the whole series; early values None until seeded."""
    out: list[float | None] = [None] * len(closes)
    if length < 1 or len(closes) < length:
        return out
    seed = sum(closes[:length]) / length
    out[length - 1] = seed
    k = 2.0 / (length + 1)
    prev = seed
    for i in range(length, len(closes)):
        prev = closes[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def is_downtrend(closes: Sequence[float], ema20: Sequence[float | None], atr: float, i: int) -> bool:
    """close < EMA20, EMA20 falling, close < close[6] − 0.5×ATR."""
    if i < 6 or atr <= 0:
        return False
    e, e_prev = ema20[i], ema20[i - 1]
    if e is None or e_prev is None:
        return False
    return closes[i] < e and e < e_prev and closes[i] < closes[i - 6] - 0.5 * atr


def is_uptrend(closes: Sequence[float], ema20: Sequence[float | None], atr: float, i: int) -> bool:
    if i < 6 or atr <= 0:
        return False
    e, e_prev = ema20[i], ema20[i - 1]
    if e is None or e_prev is None:
        return False
    return closes[i] > e and e > e_prev and closes[i] > closes[i - 6] + 0.5 * atr


def _body_inside(inner: OHLC, outer: OHLC) -> bool:
    return body_top(inner) <= body_top(outer) and body_bot(inner) >= body_bot(outer)


# ---- pattern detectors (known at end_index = last pattern bar) ----------------


def detect_tweezer_bottom(bars: Sequence[OHLC], i: int, atr: float) -> PatternCandidate | None:
    if i < 1 or atr <= 0:
        return None
    c1, c2 = bars[i - 1], bars[i]
    if not (is_red(c1) and is_green(c2)):
        return None
    if abs(c1.low - c2.low) > 0.1 * atr:
        return None
    lo = min(c1.low, c2.low)
    return PatternCandidate(
        kind="tweezer_bottom",
        side="long",
        end_index=i,
        confirm_level=c2.high,
        invalid_level=lo,
        pattern_high=max(c1.high, c2.high),
        pattern_low=lo,
    )


def detect_tweezer_top(bars: Sequence[OHLC], i: int, atr: float) -> PatternCandidate | None:
    if i < 1 or atr <= 0:
        return None
    c1, c2 = bars[i - 1], bars[i]
    if not (is_green(c1) and is_red(c2)):
        return None
    if abs(c1.high - c2.high) > 0.1 * atr:
        return None
    hi = max(c1.high, c2.high)
    return PatternCandidate(
        kind="tweezer_top",
        side="short",
        end_index=i,
        confirm_level=c2.low,
        invalid_level=hi,
        pattern_high=hi,
        pattern_low=min(c1.low, c2.low),
    )


def detect_harami_bull(bars: Sequence[OHLC], i: int, avg_b: float) -> PatternCandidate | None:
    if i < 1 or avg_b <= 0:
        return None
    c1, c2 = bars[i - 1], bars[i]
    b1, b2 = body(c1), body(c2)
    if b1 < 1.2 * avg_b:
        return None
    if not (is_red(c1) and is_green(c2)):
        return None
    if not _body_inside(c2, c1) or b2 > 0.5 * b1:
        return None
    lo = min(c1.low, c2.low)
    return PatternCandidate(
        kind="harami_bull",
        side="long",
        end_index=i,
        confirm_level=c1.high,
        invalid_level=lo,
        pattern_high=max(c1.high, c2.high),
        pattern_low=lo,
    )


def detect_harami_bear(bars: Sequence[OHLC], i: int, avg_b: float) -> PatternCandidate | None:
    if i < 1 or avg_b <= 0:
        return None
    c1, c2 = bars[i - 1], bars[i]
    b1, b2 = body(c1), body(c2)
    if b1 < 1.2 * avg_b:
        return None
    if not (is_green(c1) and is_red(c2)):
        return None
    if not _body_inside(c2, c1) or b2 > 0.5 * b1:
        return None
    hi = max(c1.high, c2.high)
    return PatternCandidate(
        kind="harami_bear",
        side="short",
        end_index=i,
        confirm_level=c1.low,
        invalid_level=hi,
        pattern_high=hi,
        pattern_low=min(c1.low, c2.low),
    )


def detect_morning_star(bars: Sequence[OHLC], i: int, avg_b: float) -> PatternCandidate | None:
    if i < 2 or avg_b <= 0:
        return None
    c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
    b1, b2 = body(c1), body(c2)
    if b1 < 1.2 * avg_b or not is_red(c1):
        return None
    if b2 > 0.3 * b1:
        return None
    mid = (c1.open + c1.close) / 2.0
    if c3.close <= mid:
        return None
    lo = min(c1.low, c2.low, c3.low)
    return PatternCandidate(
        kind="morning_star",
        side="long",
        end_index=i,
        confirm_level=c3.high,
        invalid_level=lo,
        pattern_high=max(c1.high, c2.high, c3.high),
        pattern_low=lo,
    )


def detect_evening_star(bars: Sequence[OHLC], i: int, avg_b: float) -> PatternCandidate | None:
    if i < 2 or avg_b <= 0:
        return None
    c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
    b1, b2 = body(c1), body(c2)
    if b1 < 1.2 * avg_b or not is_green(c1):
        return None
    if b2 > 0.3 * b1:
        return None
    mid = (c1.open + c1.close) / 2.0
    if c3.close >= mid:
        return None
    hi = max(c1.high, c2.high, c3.high)
    return PatternCandidate(
        kind="evening_star",
        side="short",
        end_index=i,
        confirm_level=c3.low,
        invalid_level=hi,
        pattern_high=hi,
        pattern_low=min(c1.low, c2.low, c3.low),
    )


def _is_doji(b: OHLC) -> bool:
    r = bar_range(b)
    return r > 0 and body(b) <= 0.1 * r


def detect_dragonfly(bars: Sequence[OHLC], i: int, *, downtrend: bool) -> PatternCandidate | None:
    if not downtrend:
        return None
    b = bars[i]
    r = bar_range(b)
    if r <= 0 or not _is_doji(b):
        return None
    if upper_wick(b) > 0.1 * r or lower_wick(b) < 0.6 * r:
        return None
    return PatternCandidate(
        kind="dragonfly",
        side="long",
        end_index=i,
        confirm_level=b.high,
        invalid_level=b.low,
        pattern_high=b.high,
        pattern_low=b.low,
    )


def detect_gravestone(bars: Sequence[OHLC], i: int, *, uptrend: bool) -> PatternCandidate | None:
    if not uptrend:
        return None
    b = bars[i]
    r = bar_range(b)
    if r <= 0 or not _is_doji(b):
        return None
    if lower_wick(b) > 0.1 * r or upper_wick(b) < 0.6 * r:
        return None
    return PatternCandidate(
        kind="gravestone",
        side="short",
        end_index=i,
        confirm_level=b.low,
        invalid_level=b.high,
        pattern_high=b.high,
        pattern_low=b.low,
    )


def detect_plus_doji(bars: Sequence[OHLC], i: int) -> list[PatternCandidate]:
    """Indecision: both sides offered as next-bar breakout only."""
    b = bars[i]
    r = bar_range(b)
    if r <= 0 or not _is_doji(b):
        return []
    uw, lw = upper_wick(b), lower_wick(b)
    if uw < 0.3 * r or lw < 0.3 * r:
        return []
    ratio = uw / lw if lw > 0 else 0.0
    if not (0.67 <= ratio <= 1.5):
        return []
    return [
        PatternCandidate(
            kind="plus_doji",
            side="long",
            end_index=i,
            confirm_level=b.high,
            invalid_level=b.low,
            pattern_high=b.high,
            pattern_low=b.low,
            next_bar_only=True,
        ),
        PatternCandidate(
            kind="plus_doji",
            side="short",
            end_index=i,
            confirm_level=b.low,
            invalid_level=b.high,
            pattern_high=b.high,
            pattern_low=b.low,
            next_bar_only=True,
        ),
    ]


def detect_long_legged(bars: Sequence[OHLC], i: int, atr: float) -> list[PatternCandidate]:
    b = bars[i]
    r = bar_range(b)
    if r <= 0 or atr <= 0 or not _is_doji(b):
        return []
    if r < 1.5 * atr:
        return []
    uw, lw = upper_wick(b), lower_wick(b)
    if uw < 0.35 * r or lw < 0.35 * r:
        return []
    return [
        PatternCandidate(
            kind="long_legged",
            side="long",
            end_index=i,
            confirm_level=b.high,
            invalid_level=b.low,
            pattern_high=b.high,
            pattern_low=b.low,
            next_bar_only=True,
        ),
        PatternCandidate(
            kind="long_legged",
            side="short",
            end_index=i,
            confirm_level=b.low,
            invalid_level=b.high,
            pattern_high=b.high,
            pattern_low=b.low,
            next_bar_only=True,
        ),
    ]


PATTERN_KINDS = (
    "tweezer_bottom",
    "tweezer_top",
    "harami_bull",
    "harami_bear",
    "morning_star",
    "evening_star",
    "dragonfly",
    "gravestone",
    "plus_doji",
    "long_legged",
)


def detect_at(
    bars: Sequence[OHLC],
    i: int,
    *,
    atr: float,
    avg_b: float,
    closes: Sequence[float],
    ema20: Sequence[float | None],
    kinds: Sequence[str] | None = None,
) -> list[PatternCandidate]:
    """All matching candidates known at bar `i` (closed)."""
    want = set(kinds) if kinds is not None else set(PATTERN_KINDS)
    out: list[PatternCandidate] = []
    down = is_downtrend(closes, ema20, atr, i)
    up = is_uptrend(closes, ema20, atr, i)

    if "tweezer_bottom" in want:
        c = detect_tweezer_bottom(bars, i, atr)
        if c:
            out.append(c)
    if "tweezer_top" in want:
        c = detect_tweezer_top(bars, i, atr)
        if c:
            out.append(c)
    if "harami_bull" in want:
        c = detect_harami_bull(bars, i, avg_b)
        if c:
            out.append(c)
    if "harami_bear" in want:
        c = detect_harami_bear(bars, i, avg_b)
        if c:
            out.append(c)
    if "morning_star" in want:
        c = detect_morning_star(bars, i, avg_b)
        if c:
            out.append(c)
    if "evening_star" in want:
        c = detect_evening_star(bars, i, avg_b)
        if c:
            out.append(c)
    if "dragonfly" in want:
        c = detect_dragonfly(bars, i, downtrend=down)
        if c:
            out.append(c)
    if "gravestone" in want:
        c = detect_gravestone(bars, i, uptrend=up)
        if c:
            out.append(c)
    if "plus_doji" in want:
        out.extend(detect_plus_doji(bars, i))
    if "long_legged" in want:
        out.extend(detect_long_legged(bars, i, atr))
    return out


def confirmed(
    cand: PatternCandidate,
    bar: OHLC,
    *,
    bars_since: int,
    confirm_bars: int = 3,
) -> bool:
    """True when this closed bar confirms the candidate (no look-ahead)."""
    if bars_since < 1:
        return False
    if cand.next_bar_only and bars_since != 1:
        return False
    if bars_since > confirm_bars:
        return False
    if cand.side == "long":
        return bar.close > cand.confirm_level
    return bar.close < cand.confirm_level


def invalidated(cand: PatternCandidate, bar: OHLC) -> bool:
    if cand.side == "long":
        return bar.close < cand.invalid_level
    return bar.close > cand.invalid_level


def classic_pivots_full(high: float, low: float, close: float) -> dict[str, float]:
    """PP, R1, R2, S1, S2 classic floor pivots."""
    pp = (high + low + close) / 3.0
    r1 = 2.0 * pp - low
    s1 = 2.0 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    return {"PP": pp, "R1": r1, "S1": s1, "R2": r2, "S2": s2}


def fib_zone(swing_low: float, swing_high: float) -> tuple[float, float]:
    """38.2–61.8 retracement band between a confirmed swing low and high (unordered)."""
    lo, hi = (swing_low, swing_high) if swing_low <= swing_high else (swing_high, swing_low)
    span = hi - lo
    a = lo + 0.382 * span
    b = lo + 0.618 * span
    return (min(a, b), max(a, b))


def confirmed_swing(
    highs: Sequence[float],
    lows: Sequence[float],
    i: int,
    left: int = 5,
    right: int = 5,
) -> tuple[float | None, float | None]:
    """Pivot high/low confirmed at `i` (pivot bar = i - right)."""
    n = i + 1
    if n < left + right + 1:
        return None, None
    pivot = i - right
    ph = highs[pivot]
    pl = lows[pivot]
    is_h = all(highs[j] < ph for j in range(pivot - left, pivot + right + 1) if j != pivot)
    is_l = all(lows[j] > pl for j in range(pivot - left, pivot + right + 1) if j != pivot)
    return (ph if is_h else None, pl if is_l else None)
