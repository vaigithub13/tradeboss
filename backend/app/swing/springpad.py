"""SpringPad 50/200 SMA crossover. Research only. No orders.

The signal is a close: SMA50 crossing above SMA200 is a long entry at the next
session's open, and the opposite cross is a short entry. The stop and the
target are measured from the slipped fill. A gap through either level fills at
the open. If the day's range touches both, the stop fills. An opposite cross
while the position is open fills at the next open. Variant A has no time
limit. Variant B sells at the close of the fifth session, counting the entry
session as day one.

The stock gate is the uncapped book: every cross is its own trade, sized to
₹10,000, and a fill that cannot clear ₹5,000 is skipped. The ₹50,000 and
₹2,00,000 books, five names at a time, are reported and are not extra
attempts. A portfolio slot may hold a position a few rupees under ₹5,000 when
the unspent part of the slot is within 20%, the same remainder Phase 4 keeps.
A wider gap is skipped. After a loss the book funds fewer names.

Index futures use the dated futures table. A window that starts before the
first row is not costed and is not filled with a later row. Sensex is only
run when its daily file is already stored.

Holdout 2026-07-01 through 2026-10-01 is refused. There is no flag that opens it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from app.swing.bhav_pit import LISTED_SESSIONS, PRICE_MIN, TURNOVER_MIN
from app.swing.engine import LIQUID_SIZES, slipped
from app.swing.fixes import as_date
from app.swing.momentum import funded_slots
from app.swing.research import (
    MIN_AVG_TRADE,
    MIN_NOTIONAL,
    MIN_OOS_TRADES,
    RANDOM_BEAT,
    overlaps_holdout,
    shift_param,
)

FAST = 50
SLOW = 200
STOP_PCT = Decimal("0.02")
TARGET_PCT = Decimal("0.04")
MAX_HOLD_B = 5
TRADE_NOTIONAL = Decimal("10000")
BOOK_SLOTS = 5
RANDOM_SEEDS = tuple(range(1, 51))
STOP_REASONS = ("stop", "stop_gap")
TARGET_REASONS = ("target", "target_gap")


@dataclass(frozen=True)
class SpringSpec:
    start: date
    end: date
    fast: int = FAST
    slow: int = SLOW
    stop_pct: Decimal = STOP_PCT
    target_pct: Decimal = TARGET_PCT
    max_hold: int | None = None
    notional: Decimal = TRADE_NOTIONAL
    min_notional: Decimal = MIN_NOTIONAL
    max_positions: int | None = None
    capital: Decimal | None = None
    brokerage: str = "upstox"
    kind: str = "stock"
    side: str = "long"
    slip_liquid: Decimal = Decimal("0.001")
    slip_other: Decimal = Decimal("0.0025")

    def __post_init__(self) -> None:
        if self.brokerage not in ("upstox", "zero"):
            raise ValueError("brokerage is upstox or zero")
        if self.kind not in ("stock", "index"):
            raise ValueError("kind is stock or index")
        if self.side not in ("long", "short"):
            raise ValueError("side is long or short")
        if self.start > self.end:
            raise ValueError("start is after end")
        if self.fast < 2 or self.slow <= self.fast:
            raise ValueError("the slow average has to be longer than the fast average")
        if overlaps_holdout(self.start, self.end):
            raise ValueError("holdout 2026-07-01 through 2026-10-01 is untouched")


@dataclass(frozen=True)
class Tape:
    lineage: str
    symbol: str
    dates: list[date]
    loc: dict[date, int]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    golden: np.ndarray
    death: np.ndarray
    eligible: np.ndarray
    size: tuple[str, ...]
    last: date

    def with_lengths(self, fast: int, slow: int) -> Tape:
        golden, death = crosses(sma(self.close, fast), sma(self.close, slow))
        return replace(self, golden=golden, death=death)


@dataclass(frozen=True)
class Signal:
    lineage: str
    symbol: str
    size: str
    side: str
    signal_date: date
    entry_date: date


def sma(values: np.ndarray, window: int) -> np.ndarray:
    """Simple mean. Index i uses closes i-window+1 through i and nothing after it."""
    out = np.full(len(values), np.nan)
    if window < 1 or len(values) < window:
        return out
    totals = np.cumsum(values, dtype=float)
    out[window - 1] = totals[window - 1] / window
    if len(values) > window:
        out[window:] = (totals[window:] - totals[:-window]) / window
    return out


def crosses(fast: np.ndarray, slow: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Golden when the fast average finishes the session above the slow one."""
    valid = np.isfinite(fast) & np.isfinite(slow)
    prev_fast = np.roll(fast, 1)
    prev_slow = np.roll(slow, 1)
    prev_ok = np.roll(valid, 1)
    prev_ok[0] = False
    golden = valid & prev_ok & (prev_fast <= prev_slow) & (fast > slow)
    death = valid & prev_ok & (prev_fast >= prev_slow) & (fast < slow)
    golden[0] = False
    death[0] = False
    return golden, death


def _text(value) -> str:
    if value is None:
        return "other"
    text = str(value).strip()
    if text in ("", "nan", "None", "NaN"):
        return "other"
    return text


def build_tapes(frame: pd.DataFrame, fast: int, slow: int, *, require_filter: bool = True) -> dict[str, Tape]:
    """One tape per lineage. SMA and the cross use the research close."""
    if frame.empty:
        return {}
    work = frame.copy()
    work["date"] = work["date"].map(as_date)
    work = work.sort_values(["lineage", "date"])
    tapes: dict[str, Tape] = {}
    for lineage, part in work.groupby(work["lineage"].astype(str), sort=False):
        part = part.drop_duplicates("date")
        dates = [as_date(day) for day in part["date"]]
        close = part["px_close"].to_numpy(dtype=float)
        golden, death = crosses(sma(close, fast), sma(close, slow))
        if require_filter:
            sessions = part["sessions"].to_numpy(dtype=float)
            turnover = part["turnover_20d"].to_numpy(dtype=float)
            raw = part["close"].to_numpy(dtype=float)
            eligible = (sessions >= LISTED_SESSIONS) & (turnover >= TURNOVER_MIN) & (raw >= PRICE_MIN)
        else:
            eligible = np.ones(len(dates), dtype=bool)
        sizes = tuple(_text(value) for value in part["size"]) if "size" in part.columns else tuple("other" for _ in dates)
        symbol = _text(part["symbol"].iloc[-1]) if "symbol" in part.columns else str(lineage)
        tapes[str(lineage)] = Tape(
            lineage=str(lineage),
            symbol=symbol,
            dates=dates,
            loc={day: i for i, day in enumerate(dates)},
            open=part["px_open"].to_numpy(dtype=float),
            high=part["px_high"].to_numpy(dtype=float),
            low=part["px_low"].to_numpy(dtype=float),
            close=close,
            golden=golden,
            death=death,
            eligible=np.asarray(eligible, dtype=bool),
            size=sizes,
            last=dates[-1],
        )
    return tapes


def collect_signals(
    tapes: dict[str, Tape],
    market: list[date],
    start: date,
    end: date,
    blocked: set[tuple[str, date]],
    side: str,
    *,
    require_eligible: bool,
) -> list[Signal]:
    """A cross on day D is an entry on the next session, when that session is in the window."""
    if side not in ("long", "short"):
        raise ValueError("side is long or short")
    index = {day: i for i, day in enumerate(market)}
    found: list[Signal] = []
    for tape in tapes.values():
        marks = tape.golden if side == "long" else tape.death
        for i in np.flatnonzero(marks):
            if require_eligible and not bool(tape.eligible[i]):
                continue
            day = tape.dates[int(i)]
            pos = index.get(day)
            if pos is None or pos + 1 >= len(market):
                continue
            entry = market[pos + 1]
            if entry < start or entry > end or entry not in tape.loc:
                continue
            if (tape.lineage, entry) in blocked:
                continue
            found.append(
                Signal(
                    tape.lineage,
                    tape.symbol,
                    tape.size[int(i)],
                    side,
                    day,
                    entry,
                )
            )
    found.sort(key=lambda item: (item.entry_date, item.lineage))
    return found


def entry_pool(
    tapes: dict[str, Tape],
    market: list[date],
    start: date,
    end: date,
    blocked: set[tuple[str, date]],
    spec: SpringSpec,
) -> list[tuple[str, date]]:
    """Eligible decision days whose next session can be bought under the same size floor."""
    index = {day: i for i, day in enumerate(market)}
    pool: list[tuple[str, date]] = []
    for tape in tapes.values():
        for i, day in enumerate(tape.dates):
            if not bool(tape.eligible[i]):
                continue
            pos = index.get(day)
            if pos is None or pos + 1 >= len(market):
                continue
            entry = market[pos + 1]
            if entry < start or entry > end or entry not in tape.loc:
                continue
            if (tape.lineage, entry) in blocked:
                continue
            if _qty_for(tape, tape.loc[entry], spec, tape.size[i]) < 1:
                continue
            pool.append((tape.lineage, entry))
    return pool


def slip_for(size: str, spec: SpringSpec) -> Decimal:
    if spec.kind == "index":
        return Decimal("0")
    if size in LIQUID_SIZES:
        return spec.slip_liquid
    return spec.slip_other


def entry_fill(side: str, raw: Decimal, slip: Decimal) -> Decimal:
    return slipped("BUY" if side == "long" else "SELL", raw, slip)


def exit_fill(side: str, raw: Decimal, slip: Decimal) -> Decimal:
    return slipped("SELL" if side == "long" else "BUY", raw, slip)


def bracket(fill: Decimal, side: str, stop_pct: Decimal, target_pct: Decimal) -> tuple[Decimal, Decimal]:
    if side == "long":
        return fill * (Decimal(1) - stop_pct), fill * (Decimal(1) + target_pct)
    return fill * (Decimal(1) + stop_pct), fill * (Decimal(1) - target_pct)


def _lower_lock(open_: float, high: float, low: float, close: float, prev: float | None) -> bool:
    if prev is None or prev != prev or not (close < prev):
        return False
    return max(open_, high, low, close) - min(open_, high, low, close) <= 1e-6


def overnight_exit(
    side: str,
    open_: float,
    stop: float,
    target: float,
    is_event: bool,
    crossed: bool,
    locked: bool,
) -> tuple[float, str] | None:
    """The open is checked before the day's range. A gap fills at the open."""
    if locked:
        return None
    if side == "long":
        if open_ <= stop:
            return open_, "stop_gap"
        if open_ >= target:
            return open_, "target_gap"
    else:
        if open_ >= stop:
            return open_, "stop_gap"
        if open_ <= target:
            return open_, "target_gap"
    if is_event:
        return open_, "event_open"
    if crossed:
        return open_, "cross"
    return None


def range_exit(
    side: str,
    low: float,
    high: float,
    stop: float,
    target: float,
    locked: bool,
) -> tuple[float, str] | None:
    """Both levels in the same range fill the stop."""
    if locked:
        return None
    if side == "long":
        if low <= stop and high >= target:
            return stop, "stop"
        if low <= stop:
            return stop, "stop"
        if high >= target:
            return target, "target"
    else:
        if high >= stop and low <= target:
            return stop, "stop"
        if high >= stop:
            return stop, "stop"
        if low <= target:
            return target, "target"
    return None


def close_exit(
    close: float,
    held: int,
    max_hold: int | None,
    next_event: bool,
    is_end: bool,
    locked: bool,
) -> tuple[float, str] | None:
    """Event, then the time stop, then the sample boundary. The sample still flattens a lock."""
    if next_event and not locked:
        return close, "event"
    if max_hold is not None and held >= max_hold and not locked:
        return close, "time"
    if is_end:
        return close, "sample_end"
    return None


def _qty_for(tape: Tape, entry_i: int, spec: SpringSpec, size: str) -> int:
    raw = Decimal(str(float(tape.open[entry_i])))
    if raw <= 0:
        return 0
    fill = entry_fill(spec.side, raw, slip_for(size, spec))
    if spec.kind == "index":
        return 1
    qty = int(spec.notional // fill)
    if qty < 1 or fill * qty < spec.min_notional:
        return 0
    return qty


def _crossed(tape: Tape, bar_i: int, side: str) -> bool:
    if bar_i <= 0:
        return False
    marks = tape.death if side == "long" else tape.golden
    return bool(marks[bar_i - 1])


def _finish(
    signal: Signal,
    qty: int,
    entry_raw: float,
    entry_fill_px: float,
    stop: float,
    target: float,
    exit_day: date,
    exit_raw: float,
    reason: str,
    hold: int,
    slip: Decimal,
) -> dict:
    filled = float(exit_fill(signal.side, Decimal(str(exit_raw)), slip))
    return {
        "lineage": signal.lineage,
        "symbol": signal.symbol,
        "size": signal.size,
        "side": signal.side,
        "signal_date": signal.signal_date,
        "entry_date": signal.entry_date,
        "exit_date": exit_day,
        "entry_raw": entry_raw,
        "entry_fill": entry_fill_px,
        "exit_raw": exit_raw,
        "exit_fill": filled,
        "qty": qty,
        "stop": stop,
        "target": target,
        "reason": reason,
        "hold": hold,
    }


def walk_one(
    signal: Signal,
    tape: Tape,
    market: list[date],
    loc: dict[date, int],
    end_day: date,
    spec: SpringSpec,
    flags: set[tuple[str, date]],
) -> dict | None:
    entry_i = tape.loc.get(signal.entry_date)
    start_pos = loc.get(signal.entry_date)
    end_pos = loc.get(end_day)
    if entry_i is None or start_pos is None or end_pos is None or start_pos > end_pos:
        return None
    size = signal.size
    slip = slip_for(size, spec)
    raw = float(tape.open[entry_i])
    if not raw > 0:
        return None
    filled = entry_fill(signal.side, Decimal(str(raw)), slip)
    if spec.kind == "index":
        qty = 1
    else:
        qty = int(spec.notional // filled)
        if qty < 1 or filled * qty < spec.min_notional:
            return None
    stop_d, target_d = bracket(filled, signal.side, spec.stop_pct, spec.target_pct)
    stop = float(stop_d)
    target = float(target_d)
    entry_px = float(filled)
    absent = 0
    last_close = float(tape.close[entry_i])
    for step in range(end_pos - start_pos + 1):
        day = market[start_pos + step]
        held = step + 1
        bar_i = tape.loc.get(day)
        if bar_i is None:
            absent += 1
            ended = tape.last < day
            if spec.kind == "index" and not ended and day != end_day:
                continue
            if ended or absent >= 5 or day == end_day:
                reason = "delisted" if ended else ("no_bar" if absent >= 5 else "sample_end")
                if spec.kind == "index" and reason == "no_bar":
                    continue
                return _finish(signal, qty, raw, entry_px, stop, target, day, last_close, reason, held, slip)
            continue
        absent = 0
        open_ = float(tape.open[bar_i])
        high = float(tape.high[bar_i])
        low = float(tape.low[bar_i])
        close = float(tape.close[bar_i])
        last_close = close
        prev = float(tape.close[bar_i - 1]) if bar_i > 0 else None
        locked = signal.side == "long" and spec.kind != "index" and _lower_lock(open_, high, low, close, prev)
        if step > 0:
            decision = overnight_exit(
                signal.side,
                open_,
                stop,
                target,
                (signal.lineage, day) in flags,
                _crossed(tape, bar_i, signal.side),
                locked,
            )
            if decision is not None:
                price, reason = decision
                return _finish(signal, qty, raw, entry_px, stop, target, day, price, reason, held, slip)
        decision = range_exit(signal.side, low, high, stop, target, locked)
        if decision is not None:
            price, reason = decision
            return _finish(signal, qty, raw, entry_px, stop, target, day, price, reason, held, slip)
        nxt = market[start_pos + step + 1] if start_pos + step + 1 < len(market) else None
        decision = close_exit(
            close,
            held,
            spec.max_hold,
            nxt is not None and (signal.lineage, nxt) in flags,
            day == end_day,
            locked,
        )
        if decision is not None:
            price, reason = decision
            return _finish(signal, qty, raw, entry_px, stop, target, day, price, reason, held, slip)
    return _finish(signal, qty, raw, entry_px, stop, target, end_day, last_close, "sample_end", end_pos - start_pos + 1, slip)


def apply_costs(trade: dict, charge) -> dict:
    """`charge(side, price, qty, on)` returns the rupee fee for one leg."""
    out = dict(trade)
    qty = int(trade["qty"])
    entry_side = "BUY" if trade["side"] == "long" else "SELL"
    exit_side = "SELL" if trade["side"] == "long" else "BUY"
    entry_fee = charge(entry_side, trade["entry_fill"], qty, trade["entry_date"])
    exit_fee = charge(exit_side, trade["exit_fill"], qty, trade["exit_date"])
    fees = float(entry_fee + exit_fee)
    if trade["side"] == "long":
        slip_rupees = (trade["entry_fill"] - trade["entry_raw"]) * qty + (trade["exit_raw"] - trade["exit_fill"]) * qty
        pnl = (trade["exit_fill"] - trade["entry_fill"]) * qty - fees
    else:
        slip_rupees = (trade["entry_raw"] - trade["entry_fill"]) * qty + (trade["exit_fill"] - trade["exit_raw"]) * qty
        pnl = (trade["entry_fill"] - trade["exit_fill"]) * qty - fees
    notional = trade["entry_fill"] * qty
    out["pnl"] = pnl
    out["notional"] = notional
    out["cost"] = fees + slip_rupees
    out["ret"] = pnl / notional if notional else 0.0
    out["fees"] = fees
    return out


def run_uncapped(
    signals: list[Signal],
    tapes: dict[str, Tape],
    market: list[date],
    spec: SpringSpec,
    flags: set[tuple[str, date]],
    charge=None,
) -> list[dict]:
    loc = {day: i for i, day in enumerate(market)}
    end_day = _end_day(market, spec.end)
    if end_day is None:
        return []
    trades = []
    for signal in signals:
        tape = tapes.get(signal.lineage)
        if tape is None:
            continue
        trade = walk_one(signal, tape, market, loc, end_day, spec, flags)
        if trade is None:
            continue
        trades.append(trade if charge is None else apply_costs(trade, charge))
    return trades


def _end_day(market: list[date], end: date) -> date | None:
    found = None
    for day in market:
        if day <= end:
            found = day
        else:
            break
    return found


def _fee_factory(table, flat: Decimal | None):
    def charge(side: str, price: float, qty: int, on: date) -> Decimal:
        return table.leg(side, price, qty, on, flat_override=flat).total

    return charge


def _futures_factory(table):
    def charge(side: str, price: float, qty: int, on: date) -> Decimal:
        return table.leg_cost(side, float(price), int(qty), on).total

    return charge


def run_book(
    signals: list[Signal],
    tapes: dict[str, Tape],
    market: list[date],
    spec: SpringSpec,
    flags: set[tuple[str, date]],
    charge,
) -> dict:
    """Shared cash, at most `max_positions` names. Lineage order decides a crowded open."""
    if spec.capital is None or spec.max_positions is None:
        raise ValueError("a portfolio book needs capital and a position cap")
    loc = {day: i for i, day in enumerate(market)}
    end_day = _end_day(market, spec.end)
    window = [day for day in market if spec.start <= day <= spec.end]
    pending: dict[date, list[Signal]] = defaultdict(list)
    for signal in signals:
        if signal.entry_date in loc and spec.start <= signal.entry_date <= spec.end:
            pending[signal.entry_date].append(signal)
    for day in pending:
        pending[day].sort(key=lambda item: item.lineage)
    cash = spec.capital
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity: list[float] = [float(spec.capital)]
    last_close: dict[str, float] = {}
    if end_day is None:
        return {"trades": trades, "equity": equity}

    def mark() -> Decimal:
        total = cash
        for lineage, pos in positions.items():
            price = last_close.get(lineage)
            if price is not None:
                total += Decimal(str(price)) * pos["qty"]
        return total

    def close_pos(pos: dict, day: date, price: float, reason: str, held: int) -> None:
        nonlocal cash
        slip = pos["slip"]
        raw = Decimal(str(price))
        fill = exit_fill(pos["side"], raw, slip)
        proceeds = fill * pos["qty"]
        fees = charge("SELL" if pos["side"] == "long" else "BUY", float(fill), pos["qty"], day)
        affordable = cash + proceeds
        if fees > affordable:
            fees = affordable
        cash += proceeds - fees
        if cash < 0:
            cash = Decimal(0)
        trade = _finish(
            pos["signal"],
            pos["qty"],
            pos["entry_raw"],
            pos["entry_fill"],
            pos["stop"],
            pos["target"],
            day,
            price,
            reason,
            held,
            slip,
        )
        entry_fees = pos["entry_fees"]
        exit_fees = float(fees)
        slip_rupees = (pos["entry_fill"] - pos["entry_raw"]) * pos["qty"] + (price - float(fill)) * pos["qty"]
        pnl = (float(fill) - pos["entry_fill"]) * pos["qty"] - entry_fees - exit_fees
        notional = pos["entry_fill"] * pos["qty"]
        trade["exit_fill"] = float(fill)
        trade["pnl"] = pnl
        trade["cost"] = entry_fees + exit_fees + slip_rupees
        trade["fees"] = entry_fees + exit_fees
        trade["notional"] = notional
        trade["ret"] = pnl / notional if notional else 0.0
        trades.append(trade)
        del positions[pos["lineage"]]

    for today in window:
        today_i = loc[today]
        exited: set[str] = set()
        for lineage, pos in list(positions.items()):
            tape = tapes[lineage]
            bar_i = tape.loc.get(today)
            held = today_i - pos["entry_index"] + 1
            if bar_i is None:
                pos["absent"] += 1
                ended = tape.last < today
                if pos["last_close"] is not None and (ended or pos["absent"] >= 5 or today == end_day):
                    reason = "delisted" if ended else ("no_bar" if pos["absent"] >= 5 else "sample_end")
                    close_pos(pos, today, pos["last_close"], reason, held)
                    exited.add(lineage)
                continue
            pos["absent"] = 0
            open_ = float(tape.open[bar_i])
            high = float(tape.high[bar_i])
            low = float(tape.low[bar_i])
            close = float(tape.close[bar_i])
            prev = float(tape.close[bar_i - 1]) if bar_i > 0 else None
            locked = _lower_lock(open_, high, low, close, prev)
            pos["last_close"] = close
            last_close[lineage] = close
            if today == pos["entry_date"]:
                continue
            decision = overnight_exit(
                "long",
                open_,
                pos["stop"],
                pos["target"],
                (lineage, today) in flags,
                _crossed(tape, bar_i, "long"),
                locked,
            )
            if decision is not None:
                price, reason = decision
                close_pos(pos, today, price, reason, held)
                exited.add(lineage)
        slots = funded_slots(mark(), int(spec.max_positions), spec.min_notional)
        free = slots - len(positions)
        slot = (mark() / Decimal(slots)) if slots else Decimal("0")
        seen: set[str] = set()
        for signal in pending.get(today, []):
            if free <= 0:
                break
            if signal.lineage in positions or signal.lineage in seen:
                continue
            seen.add(signal.lineage)
            tape = tapes.get(signal.lineage)
            if tape is None:
                continue
            bar_i = tape.loc.get(today)
            if bar_i is None:
                continue
            raw = float(tape.open[bar_i])
            if not raw > 0:
                continue
            slip = slip_for(signal.size, spec)
            fill = entry_fill("long", Decimal(str(raw)), slip)
            def _bill(qty: int, fill=fill, today=today) -> Decimal:
                return charge("BUY", float(fill), qty, today)

            qty = _shares(cash, fill, slot, _bill, spec.min_notional)
            if qty < 1:
                continue
            fees = charge("BUY", float(fill), qty, today)
            cash -= fill * qty + fees
            if cash < 0:
                cash = Decimal(0)
            stop_d, target_d = bracket(fill, "long", spec.stop_pct, spec.target_pct)
            positions[signal.lineage] = {
                "lineage": signal.lineage,
                "signal": signal,
                "side": "long",
                "qty": qty,
                "entry_date": today,
                "entry_index": today_i,
                "entry_raw": raw,
                "entry_fill": float(fill),
                "entry_fees": float(fees),
                "stop": float(stop_d),
                "target": float(target_d),
                "slip": slip,
                "absent": 0,
                "last_close": float(tape.close[bar_i]),
            }
            last_close[signal.lineage] = float(tape.close[bar_i])
            free -= 1
        for lineage, pos in list(positions.items()):
            if lineage in exited:
                continue
            tape = tapes[lineage]
            bar_i = tape.loc.get(today)
            if bar_i is None:
                continue
            high = float(tape.high[bar_i])
            low = float(tape.low[bar_i])
            close = float(tape.close[bar_i])
            prev = float(tape.close[bar_i - 1]) if bar_i > 0 else None
            locked = _lower_lock(float(tape.open[bar_i]), high, low, close, prev)
            held = today_i - pos["entry_index"] + 1
            decision = range_exit("long", low, high, pos["stop"], pos["target"], locked)
            if decision is None:
                nxt = market[today_i + 1] if today_i + 1 < len(market) else None
                decision = close_exit(
                    close,
                    held,
                    spec.max_hold,
                    nxt is not None and (lineage, nxt) in flags,
                    today == end_day,
                    locked,
                )
            if decision is not None:
                price, reason = decision
                close_pos(pos, today, price, reason, held)
                exited.add(lineage)
            else:
                pos["last_close"] = close
                last_close[lineage] = close
        equity.append(float(mark()))
    return {"trades": trades, "equity": equity}


def _shares(cash: Decimal, fill: Decimal, slot: Decimal, fee_for, minimum: Decimal) -> int:
    """Integer shares inside the slot. A remainder within 20% of a funded slot is kept."""
    if slot < minimum or fill <= 0 or cash <= 0:
        return 0
    qty = int(slot // fill)
    while qty > 0 and fill * Decimal(qty) + fee_for(qty) > cash:
        qty -= 1
    if qty < 1:
        return 0
    notional = fill * Decimal(qty)
    if notional >= minimum or slot - notional <= slot * Decimal("0.20"):
        return qty
    return 0


def _core(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "win_rate": 0.0,
            "avg": 0.0,
            "cost": 0.0,
            "pf": None,
            "pf_infinite": False,
            "hold": 0.0,
            "pnl": 0.0,
            "exits": {},
            "groups": {"stop": 0, "target": 0, "cross": 0, "time": 0, "other": 0},
        }
    pnls = [float(item["pnl"]) for item in trades]
    rets = [float(item["ret"]) for item in trades]
    costs = [float(item["cost"]) for item in trades]
    notionals = [float(item["notional"]) for item in trades]
    wins = [item for item in pnls if item > 0]
    losses = [item for item in pnls if item < 0]
    exits = dict(Counter(str(item["reason"]) for item in trades))
    groups = {
        "stop": sum(exits.get(name, 0) for name in STOP_REASONS),
        "target": sum(exits.get(name, 0) for name in TARGET_REASONS),
        "cross": exits.get("cross", 0),
        "time": exits.get("time", 0),
    }
    groups["other"] = len(trades) - sum(groups.values())
    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades),
        "avg": float(np.mean(rets)),
        "cost": (sum(costs) / sum(notionals)) if sum(notionals) else 0.0,
        "pf": None if not losses else sum(wins) / abs(sum(losses)),
        "pf_infinite": not losses and bool(wins),
        "hold": float(np.mean([int(item["hold"]) for item in trades])),
        "pnl": float(sum(pnls)),
        "exits": exits,
        "groups": groups,
    }


def summarize(trades: list[dict]) -> dict:
    stats = _core(trades)
    stats["by_size"] = _buckets(trades, "size")
    stats["by_year"] = _buckets(trades, "year")
    return stats


def _buckets(trades: list[dict], key: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in trades:
        if key == "year":
            label = str(item["exit_date"].year)
        else:
            label = str(item.get(key) or "other")
        grouped[label].append(item)
    rows = []
    for label in sorted(grouped):
        part = _core(grouped[label])
        rows.append(
            {
                "key": label,
                "trades": part["trades"],
                "win_rate": part["win_rate"],
                "avg": part["avg"],
                "pnl": part["pnl"],
                "hold": part["hold"],
            }
        )
    return rows


def breakeven_win_rate(stop: float, target: float, cost: float) -> float | None:
    """Win rate that offsets a `stop` loss and a `target` win after a round-trip cost fraction.

    w* = (stop + cost) / (stop + target). The cost cancels in the denominator.
    """
    if stop <= 0 or target <= 0 or cost >= target:
        return None
    return (stop + cost) / (stop + target)


def round_trip_cost(charge, on: date, notional: Decimal, slip: Decimal) -> float:
    """Fees plus slippage, as a fraction of the entry notional, qty 1 at that notional."""
    price = float(notional)
    buy = float(charge("BUY", price, 1, on))
    sell = float(charge("SELL", price, 1, on))
    return (buy + sell) / price + 2.0 * float(slip)


def length_variants(fast: int, slow: int) -> list[tuple[str, int, int]]:
    """The SMA pair moves together: 50/200 becomes 40/160 and 60/240."""
    return [
        ("sma -20%", int(shift_param(fast, -0.2)), int(shift_param(slow, -0.2))),
        ("sma +20%", int(shift_param(fast, 0.2)), int(shift_param(slow, 0.2))),
    ]


def percent_variants(spec: SpringSpec) -> list[tuple[str, SpringSpec]]:
    """Stop and target, one at a time, each ±20%."""
    found = []
    for name, direction in (("stop -20%", -0.2), ("stop +20%", 0.2)):
        updated = Decimal(str(shift_param(float(spec.stop_pct), direction))).quantize(Decimal("0.000001"))
        found.append((name, replace(spec, stop_pct=updated)))
    for name, direction in (("target -20%", -0.2), ("target +20%", 0.2)):
        updated = Decimal(str(shift_param(float(spec.target_pct), direction))).quantize(Decimal("0.000001"))
        found.append((name, replace(spec, target_pct=updated)))
    return found


def cliff_hits(base_pnl: float, base_avg: float, variants: list[tuple[str, float, float]]) -> list[str]:
    """A profitable book cliffs when a neighbour loses money or keeps under half the average trade."""
    hits = []
    for label, pnl, avg in variants:
        if base_pnl > 0 and pnl <= 0:
            hits.append(label)
        elif base_avg > 0 and avg < 0.5 * base_avg:
            hits.append(label)
    return hits


def judge(dev: dict, oos: dict, seed_avgs: list[float], cliffs: list[str], *, dev_costed: bool = True) -> dict:
    """Every condition is required. The 0.5% average applies to DEV and to OOS."""
    reasons: list[str] = []
    if not dev_costed:
        reasons.append("DEV is not costed")
    else:
        if not (dev["pnl"] > 0 and dev["avg"] > 0):
            reasons.append("DEV is not positive after costs")
        if dev["avg"] < MIN_AVG_TRADE:
            reasons.append("DEV average trade is under 0.5%")
    if not (oos["pnl"] > 0 and oos["avg"] > 0):
        reasons.append("OOS is not positive after costs")
    if oos["avg"] < MIN_AVG_TRADE:
        reasons.append("OOS average trade is under 0.5%")
    if oos["trades"] < MIN_OOS_TRADES:
        reasons.append(f"OOS has {oos['trades']} trades")
    beaten = sum(1 for item in seed_avgs if oos["avg"] > item)
    share = beaten / len(seed_avgs) if seed_avgs else 0.0
    if not seed_avgs or share < RANDOM_BEAT:
        reasons.append(f"beats {beaten}/{len(seed_avgs) if seed_avgs else 0} random seeds")
    if cliffs:
        reasons.append("cliff: " + ", ".join(cliffs))
    return {"pass": not reasons, "reasons": reasons, "beaten": beaten, "seeds": len(seed_avgs), "random_share": share}


def _cost_rows(table):
    inner = getattr(table, "table", None)
    if inner is not None:
        return inner.rows
    return table.rows


def futures_gap(table, start: date, end: date) -> str | None:
    """The first dated futures row is not copied backward."""
    rows = _cost_rows(table)
    if not rows:
        return "the futures cost table has no rows"
    first = rows[0].effective_from
    if start < first:
        return f"futures cost table starts {first.isoformat()}; {start.isoformat()}..{end.isoformat()} is not costed"
    return None


def path_stats(equity: list[float], start: date, end: date, capital: float) -> dict:
    if not equity:
        return {"cagr": 0.0, "max_dd": 0.0, "end_equity": capital}
    path = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(path)
    draw = np.where(peak > 0, (peak - path) / peak, 0.0)
    years = max((end - start).days / 365.25, 1 / 365.25)
    last = float(path[-1])
    cagr = -1.0 if last <= 0 else (last / capital) ** (1.0 / years) - 1.0
    return {"cagr": cagr, "max_dd": float(np.max(draw)), "end_equity": last}


def random_signals(
    pool: list[tuple[str, date]],
    tapes: dict[str, Tape],
    count: int,
    seed: int,
    side: str = "long",
) -> list[Signal]:
    """Random entries. The decision day is the prior bar of that name, not a cross."""
    if count < 1 or not pool:
        return []
    rng = np.random.default_rng(seed)
    picked = rng.choice(len(pool), size=count, replace=len(pool) < count)
    found = []
    for slot in picked:
        lineage, entry = pool[int(slot)]
        tape = tapes[lineage]
        pos = tape.loc.get(entry)
        size = tape.size[pos] if pos is not None else "other"
        decision = tape.dates[pos - 1] if pos is not None and pos > 0 else entry
        found.append(Signal(lineage, tape.symbol, size, side, decision, entry))
    return found
