"""Slot 5 and slot 7 ticket facts: the trail, the five confirmations, price action, practice.

No broker calls. Slot 4's fixed −20% / +40% bracket is not used here.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import pandas as pd

from app.exits.rules import PREMIUM_TRAIL_10, trailed_stop
from app.indicators.basic import bollinger
from app.indicators.momentum import rsi
from app.indicators.volatility import supertrend
from app.strategies.dd_five_confirm import classic_pivots
from app.strategies.first_hour_box import compute_first_hour_box
from app.strategies.price_action_patterns import confirmed_swing

IST = timezone(timedelta(hours=5, minutes=30))
TRAIL_WINDOW_S = 120
PRACTICE_BARS = 6
PRACTICE_TAGS = ("trend pullback", "level break", "range fade", "other")
WATCH_SLOTS = ("5", "7")


def market_of(slot: str, symbol: str | None = None) -> str:
    """NIFTY for slot 5, SENSEX for slot 7. The contract name wins when it names the index."""
    text = (symbol or "").upper()
    if "SENSEX" in text or str(slot) == "7":
        return "SENSEX"
    return "NIFTY"


def initial_stop(fill: float, stop_frac: float = 0.10) -> float:
    """Slot 5's opening premium stop: fill × 0.90. There is no target."""
    return round(float(fill) * (1.0 - float(stop_frac)), 2)


def user_trail_stop(user_fill: float, best: float, rule: Any = PREMIUM_TRAIL_10) -> float:
    """The trailing stop recomputed from the fill the user actually got, at the same best premium."""
    return trailed_stop(float(user_fill), rule, float(best))


def format_dd_why(facts: dict[str, Any]) -> str:
    """One sentence per confirmation the DD long rule checks, with the value on the signal bar."""
    close = float(facts["close"])
    line = float(facts["supertrend"])
    rsi_v = float(facts["rsi"])
    rsi_need = float(facts.get("rsi_long", 70))
    upper = float(facts["bollinger_upper"])
    r1 = float(facts["r1"])
    parts = [
        f"close {close:,.2f} is {'above' if close > line else 'at or below'} Supertrend {line:,.2f} "
        f"({'up' if facts.get('supertrend_up') else 'down'})",
        f"RSI {rsi_v:.1f} {'>' if rsi_v > rsi_need else 'not above'} {rsi_need:.0f}",
        f"close {close:,.2f} {'>' if close > upper else 'not above'} upper Bollinger {upper:,.2f}",
        f"close {close:,.2f} {'>' if close > r1 else 'not above'} R1 {r1:,.2f}",
    ]
    if facts.get("use_wr", True):
        wr = float(facts["williams_r"])
        need = float(facts.get("wr_long", -30))
        parts.append(f"Williams %R {wr:.1f} {'>' if wr > need else 'not above'} {need:.0f}")
    return "DD long confirmations: " + "; ".join(parts) + ". Exit by the trailing stop, no target."


def dd_facts_at(bars: Sequence[dict[str, Any]], index: int, *, prev_day: tuple[float, float, float] | None,
                use_wr: bool = True) -> dict[str, Any] | None:
    """Indicator values at `index`, using the same functions as the strategy. None when a value is missing."""
    if index < 0 or index >= len(bars):
        return None
    frame = pd.DataFrame({
        "open": [float(b["open"]) for b in bars],
        "high": [float(b["high"]) for b in bars],
        "low": [float(b["low"]) for b in bars],
        "close": [float(b["close"]) for b in bars],
    })
    st = supertrend(frame, 10, 2.0)
    direction = float(st["direction"].iloc[index])
    line = float(st["supertrend"].iloc[index])
    rsi_v = float(rsi(frame["close"], 14).iloc[index])
    upper = float(bollinger(frame["close"], 20, 2.0)["upper"].iloc[index])
    if any(math.isnan(v) for v in (direction, line, rsi_v, upper)):
        return None
    if prev_day is None:
        return None
    _s1, _p, r1 = classic_pivots(*prev_day)
    wr = _williams(frame, index, 14) if use_wr else None
    if use_wr and (wr is None or math.isnan(wr)):
        return None
    return {
        "close": float(frame["close"].iloc[index]),
        "supertrend": line,
        "supertrend_up": direction < 0,
        "rsi": rsi_v,
        "rsi_long": 70.0,
        "bollinger_upper": upper,
        "r1": r1,
        "williams_r": wr,
        "wr_long": -30.0,
        "use_wr": use_wr,
    }


def _williams(frame: pd.DataFrame, index: int, length: int) -> float:
    if index + 1 < length:
        return float("nan")
    window = frame.iloc[index + 1 - length: index + 1]
    hh = float(window["high"].max())
    ll = float(window["low"].min())
    if not hh > ll:
        return float("nan")
    return (hh - float(frame["close"].iloc[index])) / (hh - ll) * -100.0


def _day(epoch: int):
    return datetime.fromtimestamp(int(epoch), IST).date()


def price_action(bars: Sequence[dict[str, Any]], *, signal_index: int | None = None) -> dict[str, Any]:
    """Facts for the last 50 bars up to the signal. No opinion."""
    if not bars:
        return {"text": "No candles."}
    end = len(bars) - 1 if signal_index is None else signal_index
    end = max(0, min(end, len(bars) - 1))
    window = list(bars[max(0, end - 49): end + 1])
    highs = [float(b["high"]) for b in window]
    lows = [float(b["low"]) for b in window]
    swings: list[dict[str, Any]] = []
    for i in range(len(window)):
        ph, pl = confirmed_swing(highs, lows, i, 3, 3)
        if ph is not None:
            swings.append({"kind": "high", "price": ph, "time": int(window[i - 3]["time"])})
        if pl is not None:
            swings.append({"kind": "low", "price": pl, "time": int(window[i - 3]["time"])})
    structure = _structure(swings)
    last = window[-1]
    close = float(last["close"])
    pdh, pdl, cpr = _previous_day(bars, int(last["time"]))
    fhb = _fhb(window, int(last["time"]))
    level = _nearest_100(window)
    anatomy = _anatomy(window[-1], window[-2] if len(window) > 1 else None)
    text = (
        f"Structure {structure}. "
        f"PDH { _fmt(pdh) }, PDL { _fmt(pdl) }, distance { _fmt(None if pdh is None else close - pdh) } / { _fmt(None if pdl is None else close - pdl) }. "
        f"CPR {cpr}. First hour box {fhb}. Nearest 100: {level}. Candle: {anatomy}."
    )
    return {
        "structure": structure,
        "swings": swings[-6:],
        "pdh": pdh,
        "pdl": pdl,
        "close": close,
        "distance_pdh": None if pdh is None else round(close - pdh, 2),
        "distance_pdl": None if pdl is None else round(close - pdl, 2),
        "cpr": cpr,
        "first_hour": fhb,
        "nearest_100": level,
        "candle": anatomy,
        "text": " ".join(text.split()),
    }


def after_trade(bars: Sequence[dict[str, Any]], *, entry_time: int, exit_time: int) -> dict[str, Any]:
    """What the index did from the signal bar through the exit. The trail itself is a premium, not an index price."""
    window = [b for b in bars if entry_time <= int(b["time"]) <= exit_time]
    if not window:
        return {"text": "No candles between the entry and the exit."}
    entry_close = float(window[0]["close"])
    mfe = round(max(float(b["high"]) for b in window) - entry_close, 2)
    highs = [float(b["high"]) for b in window]
    lows = [float(b["low"]) for b in window]
    last_low = None
    last_low_at = None
    for i in range(len(window)):
        _ph, pl = confirmed_swing(highs, lows, i, 3, 3)
        if pl is not None:
            last_low = pl
            last_low_at = int(window[i - 3]["time"])
    exit_close = float(window[-1]["close"])
    retest = _retest(window, entry_close)
    vs = "no confirmed swing low in the trade"
    if last_low is not None:
        side = "above" if exit_close > last_low else "below" if exit_close < last_low else "at"
        vs = f"exit close {exit_close:,.2f} is {side} the last swing low {last_low:,.2f}"
    text = (
        f"Max favourable move {mfe:,.2f} points above the signal close. "
        f"{'A level was retested.' if retest else 'No level retest.'} "
        f"The premium trail is not an index price; {vs}."
    )
    return {
        "mfe": mfe,
        "retest": retest,
        "last_swing_low": last_low,
        "last_swing_low_at": last_low_at,
        "text": text,
    }


def score_practice(signal_close: float, upcoming: Sequence[dict[str, Any]], *, direction: str,
                   level: float | None = None) -> dict[str, Any]:
    """The next 6 candles. Direction is right when the 6th close is on that side of the signal close."""
    window = list(upcoming)[:PRACTICE_BARS]
    if len(window) < PRACTICE_BARS:
        return {"ready": False, "direction_hit": None, "level_hit": None}
    end = float(window[-1]["close"])
    up = direction in ("up", "long", "BUY")
    direction_hit = end > float(signal_close) if up else end < float(signal_close)
    level_hit = None
    if level is not None:
        level_hit = any(float(b["low"]) <= float(level) <= float(b["high"]) for b in window)
    return {"ready": True, "direction_hit": direction_hit, "level_hit": level_hit, "end_close": end}


def tag_accuracy(reads: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Running direction accuracy for each setup tag that has a scored read."""
    rows = []
    for tag in PRACTICE_TAGS:
        done = [r for r in reads if r.get("tag") == tag and r.get("direction_hit") is not None]
        correct = sum(1 for r in done if r["direction_hit"])
        rows.append({
            "tag": tag,
            "reads": len(done),
            "correct": correct,
            "accuracy": None if not done else round(correct / len(done), 4),
        })
    return rows


def trails_on_time(record: dict[str, Any], *, window_s: int = TRAIL_WINDOW_S) -> bool:
    """Each stop move the assist saw was confirmed within `window_s`. A book with no trail has nothing to miss."""
    if not record.get("trailing"):
        return True
    seen = list(record.get("trail_seen") or [])
    logged = list(record.get("trails") or [])
    for item in seen:
        match = next((row for row in logged if row.get("paper_stop") == item.get("paper_stop")), None)
        if match is None or match.get("updated_at") is None:
            return False
        delay = int(match["updated_at"]) - int(item["moved_at"])
        if delay < 0 or delay > window_s:
            return False
    return True


def pending_trail(record: dict[str, Any], *, paper_stop: float, best: float, moved_at: int,
                  rule: Any = PREMIUM_TRAIL_10) -> dict[str, Any] | None:
    """A move the user has not confirmed. The rupee stop is from their fill, not the paper fill."""
    if record.get("kind") != "taken" or record.get("exit_time") is not None:
        return None
    fill = record.get("fill_price")
    if fill is None:
        return None
    initial = float(record.get("initial_stop") or initial_stop(float(fill)))
    if float(paper_stop) <= initial + 1e-9:
        return None
    seen = record.get("trail_seen") or []
    if any(row.get("paper_stop") == paper_stop for row in seen):
        logged = record.get("trails") or []
        if any(row.get("paper_stop") == paper_stop and row.get("updated_at") is not None for row in logged):
            return None
    real_stop = user_trail_stop(float(fill), float(best), rule)
    return {
        "key": record["key"],
        "slot": record.get("slot"),
        "market": market_of(str(record.get("slot")), record.get("contract")),
        "contract": record.get("contract"),
        "paper_stop": float(paper_stop),
        "real_stop": real_stop,
        "moved_at": int(moved_at),
        "text": f"Move SL to ₹{real_stop:,.2f}",
    }


def _structure(swings: list[dict[str, Any]]) -> str:
    highs = [s["price"] for s in swings if s["kind"] == "high"]
    lows = [s["price"] for s in swings if s["kind"] == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "not enough swings"
    hh, hl = highs[-1] > highs[-2], lows[-1] > lows[-2]
    lh, ll = highs[-1] < highs[-2], lows[-1] < lows[-2]
    if hh and hl:
        return "HH/HL"
    if lh and ll:
        return "LH/LL"
    return "mixed"


def _previous_day(bars: Sequence[dict[str, Any]], now: int):
    today = _day(now)
    days: dict[Any, list[dict[str, Any]]] = {}
    for bar in bars:
        days.setdefault(_day(int(bar["time"])), []).append(bar)
    prior = [d for d in sorted(days) if d < today]
    if not prior:
        return None, None, "no previous session"
    prev = days[prior[-1]]
    high = max(float(b["high"]) for b in prev)
    low = min(float(b["low"]) for b in prev)
    close = float(prev[-1]["close"])
    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = 2 * pivot - bc
    top, bottom = max(tc, bc), min(tc, bc)
    cpr = f"BC {bottom:,.2f} P {pivot:,.2f} TC {top:,.2f}"
    return round(high, 2), round(low, 2), cpr


def _fhb(window: list[dict[str, Any]], as_of: int) -> str:
    result = compute_first_hour_box(window, as_of=as_of, skip_odd_sessions=False)
    if not result.boxes:
        return "not formed"
    box = result.boxes[-1]
    close = float(window[-1]["close"])
    if close > box.high:
        place = "above"
    elif close < box.low:
        place = "below"
    else:
        place = "inside"
    return f"high {box.high:,.2f} low {box.low:,.2f}, close {place}"


def _nearest_100(window: list[dict[str, Any]]) -> str:
    last = window[-1]
    close = float(last["close"])
    nearest = round(close / 100.0) * 100.0
    prev = float(window[-2]["close"]) if len(window) > 1 else close
    crossed = (prev - nearest) * (close - nearest) < 0
    tagged = float(last["low"]) <= nearest <= float(last["high"])
    if crossed:
        kind = "break"
    elif tagged and not crossed:
        kind = "retest"
    else:
        kind = "mid-range"
    return f"{nearest:,.0f} ({kind})"


def _anatomy(bar: dict[str, Any], prev: dict[str, Any] | None) -> str:
    high, low = float(bar["high"]), float(bar["low"])
    open_, close = float(bar["open"]), float(bar["close"])
    span = high - low
    if span <= 0:
        return "no range"
    body = abs(close - open_) / span * 100.0
    upper = (high - max(open_, close)) / span * 100.0
    lower = (min(open_, close) - low) / span * 100.0
    location = (close - low) / span * 100.0
    relation = "no previous candle"
    if prev is not None:
        ph, pl = float(prev["high"]), float(prev["low"])
        po, pc = float(prev["open"]), float(prev["close"])
        inside = high <= ph and low >= pl
        outside = high >= ph and low <= pl
        engulf = outside and (close - open_) * (pc - po) < 0 and abs(close - open_) >= abs(pc - po)
        if inside:
            relation = "inside"
        elif engulf:
            relation = "engulfing"
        elif outside:
            relation = "outside"
        else:
            relation = "neither inside nor outside"
    return (
        f"body {body:.0f}%, upper wick {upper:.0f}%, lower wick {lower:.0f}%, "
        f"close {location:.0f}% up the range, {relation} vs previous"
    )


def _retest(window: list[dict[str, Any]], entry_close: float) -> bool:
    """A later bar trades back to the signal close after price had left it."""
    left = False
    for bar in window[1:]:
        if float(bar["high"]) > entry_close or float(bar["low"]) < entry_close:
            left = True
        if left and float(bar["low"]) <= entry_close <= float(bar["high"]):
            return True
    return False


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"
