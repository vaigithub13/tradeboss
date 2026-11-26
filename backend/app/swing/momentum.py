"""Monthly momentum research book. No orders.

Four books were fixed before any run: M1 is the return from t−252 to t−21,
M2 is the average of the two volatility-scaled z-scores, and each is run with
and without the Nifty 500 regime filter. The gate is the ₹50,000, 10-name,
Upstox book. The ₹2,00,000 book and the ₹0 brokerage book are reported and
are not extra attempts.

Holdout 2026-07-01 through 2026-09-30 is refused unless the caller has already
decided the book passed and sets `holdout=True`. Forward starts 2026-10-01.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from app.swing.delivery import DeliveryTable
from app.swing.engine import LIQUID_SIZES, slipped
from app.swing.fixes import as_date
from app.swing.research import cliff_labels, param_grid, shift_param
from app.swing.strategies import group_bounds
from app.swing.tax import GRANDFATHER_BEFORE, TaxLedger, TaxTable, grandfather_basis, is_long_term

HOLDOUT_START = date(2026, 7, 1)
HOLDOUT_END = date(2026, 9, 30)
DEV_START = date(2016, 1, 1)
DEV_END = date(2021, 12, 31)
OOS_START = date(2022, 1, 1)
OOS_END = date(2026, 6, 30)
FORWARD_START = date(2026, 10, 1)
TURNOVER_FLOOR = 100_000_000.0  # ₹10 crore
PRIMARY_CAPITAL = Decimal("50000")
PRIMARY_N = 10
PRIMARY_BROKERAGE = "upstox"
EW_CAPITAL = Decimal("10000000")
MIN_NOTIONAL = Decimal("5000")
RANDOM_SEEDS = tuple(range(1, 51))
RANDOM_BEAT = 0.95
NIFTY_DD_MULT = 1.5
SKIP = 21
M1_FAR = 252
M2_SHORT = 126
M2_LONG = 252
REGIME_SMA = 200

DEFAULTS = {
    "M1": {"far": M1_FAR, "skip": SKIP},
    "M2": {"short": M2_SHORT, "long": M2_LONG, "skip": SKIP},
}
NAMES = {
    "M1": "12-1",
    "M1R": "12-1, regime",
    "M2": "NSE-style",
    "M2R": "NSE-style, regime",
}


def overlaps_holdout(start: date, end: date) -> bool:
    return start <= HOLDOUT_END and HOLDOUT_START <= end


def research_windows(latest: date) -> dict[str, tuple[date, date]]:
    """DEV, OOS, and forward. The holdout is not a window."""
    if latest < FORWARD_START:
        raise ValueError("latest session is before the forward window")
    return {
        "DEV": (DEV_START, DEV_END),
        "OOS": (OOS_START, OOS_END),
        "FORWARD": (FORWARD_START, latest),
    }


@dataclass(frozen=True)
class MonthSpec:
    capital: Decimal
    n: int
    start: date
    end: date
    brokerage: str = PRIMARY_BROKERAGE
    regime: bool = False
    mode: str = "buffer"
    fractional: bool = False
    min_notional: Decimal = MIN_NOTIONAL
    slip_liquid: Decimal = Decimal("0.001")
    slip_other: Decimal = Decimal("0.0025")
    holdout: bool = False

    def __post_init__(self) -> None:
        if self.brokerage not in ("upstox", "zero"):
            raise ValueError("brokerage is upstox or zero")
        if self.mode not in ("buffer", "equal"):
            raise ValueError("mode is buffer or equal")
        if self.n < 1 or self.capital <= 0:
            raise ValueError("capital and N must be positive")
        if self.start > self.end:
            raise ValueError("start is after end")
        if overlaps_holdout(self.start, self.end) and not self.holdout:
            raise ValueError("holdout 2026-07-01 through 2026-09-30 is untouched")


def month_end_dates(calendar: list[date]) -> list[date]:
    """Last trading session of each calendar month."""
    ends = []
    for i, day in enumerate(calendar):
        nxt = calendar[i + 1] if i + 1 < len(calendar) else None
        if nxt is None or (nxt.year, nxt.month) != (day.year, day.month):
            ends.append(day)
    return ends


def liquid_mask(frame: pd.DataFrame) -> pd.Series:
    """Phase 1b pass plus a 20-day average turnover of at least ₹10 crore."""
    passes = frame["passes"].fillna(False).astype(bool) if "passes" in frame.columns else False
    turn = frame["turnover_20d"] if "turnover_20d" in frame.columns else pd.Series(np.nan, index=frame.index)
    return passes & turn.notna() & (turn.astype(float) >= TURNOVER_FLOOR)


def score_panel(frame: pd.DataFrame, kind: str, month_ends: list[date], params: dict | None = None) -> pd.DataFrame:
    """One row per liquid name on a month-end. The score is known at that close."""
    base = "M1" if kind.startswith("M1") else "M2"
    chosen = {**DEFAULTS[base], **(params or {})}
    work = frame.sort_values(["lineage", "date"]).reset_index(drop=True)
    bounds = group_bounds(work["lineage"].astype(str).to_numpy())
    close = work["px_close"].to_numpy(dtype=float)
    ends = set(month_ends)
    is_end = np.fromiter((day in ends for day in work["date"]), dtype=bool, count=len(work))
    liquid = liquid_mask(work).to_numpy()
    columns = ["date", "lineage", "symbol", "size", "score"]
    if base == "M1":
        values = _by_lineage(close, bounds, lambda series: _m1_array(series, int(chosen["far"]), int(chosen["skip"])))
        mask = is_end & liquid & np.isfinite(values)
        out = work.loc[mask, ["date", "lineage", "symbol", "size"]].copy()
        out["score"] = values[mask]
        return out.reset_index(drop=True)
    short = _by_lineage(close, bounds, lambda series: _ratio_array(series, int(chosen["short"]), int(chosen["skip"])))
    long = _by_lineage(close, bounds, lambda series: _ratio_array(series, int(chosen["long"]), int(chosen["skip"])))
    mask = is_end & liquid & np.isfinite(short) & np.isfinite(long)
    part = work.loc[mask, ["date", "lineage", "symbol", "size"]].copy()
    part["r6"] = short[mask]
    part["r12"] = long[mask]
    pieces = []
    for _day, group in part.groupby("date", sort=False):
        group = group.copy()
        group["score"] = 0.5 * (_z(group["r6"]) + _z(group["r12"]))
        pieces.append(group[columns])
    if not pieces:
        return pd.DataFrame(columns=columns)
    return pd.concat(pieces, ignore_index=True)


def _by_lineage(values: np.ndarray, bounds, fn) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for start, end in bounds:
        out[start:end] = fn(values[start:end])
    return out


def _m1_array(close: np.ndarray, far: int, skip: int) -> np.ndarray:
    """close[t−skip] / close[t−far] − 1. The last `skip` sessions are outside the return."""
    out = np.full(len(close), np.nan)
    if far <= skip or len(close) <= far:
        return out
    idx = np.arange(far, len(close))
    base = close[idx - far]
    last = close[idx - skip]
    good = base > 0
    out[idx[good]] = last[good] / base[good] - 1.0
    return out


def _ratio_array(close: np.ndarray, lookback: int, skip: int) -> np.ndarray:
    """Return over `lookback` sessions ending `skip` ago, divided by the volatility of that window."""
    out = np.full(len(close), np.nan)
    if lookback < 2 or len(close) <= skip + lookback:
        return out
    daily = np.zeros(len(close))
    prev = close[:-1]
    change = np.divide(np.diff(close), prev, out=np.zeros(len(close) - 1), where=prev > 0)
    daily[1:] = change
    c1 = np.cumsum(daily)
    c2 = np.cumsum(daily * daily)
    idx = np.arange(skip + lookback, len(close))
    end = idx - skip
    left = end - lookback
    total = c1[end] - c1[left]
    squares = c2[end] - c2[left]
    var = (squares - total * total / lookback) / (lookback - 1)
    sigma = np.sqrt(np.maximum(var, 0.0))
    base = close[idx - skip - lookback]
    last = close[idx - skip]
    good = (base > 0) & (sigma > 0) & np.isfinite(sigma)
    out[idx[good]] = (last[good] / base[good] - 1.0) / sigma[good]
    return out


def _z(values: pd.Series) -> pd.Series:
    sd = float(values.std(ddof=1))
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=values.index)
    return (values - float(values.mean())) / sd


def prepare(bars: pd.DataFrame) -> tuple[dict, dict[str, date]]:
    """One lookup for every book. Rebuilding it per run is most of the cost."""
    return _lookup(bars), _last_bar(bars)


def eligible_panel(frame: pd.DataFrame, month_ends: list[date]) -> pd.DataFrame:
    """Every liquid name on a month-end. The score is unused; the equal-weight book holds all of them."""
    work = frame.sort_values(["lineage", "date"]).reset_index(drop=True)
    ends = set(month_ends)
    is_end = np.fromiter((day in ends for day in work["date"]), dtype=bool, count=len(work))
    mask = is_end & liquid_mask(work).to_numpy()
    out = work.loc[mask, ["date", "lineage", "symbol", "size"]].copy()
    out["score"] = 0.0
    return out.reset_index(drop=True)


def random_panel(scores: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Same names, random ranks. One seed is one book."""
    out = scores.copy()
    out["score"] = np.random.default_rng(seed).random(len(out))
    return out


def fair_market_values(bars: pd.DataFrame) -> dict[str, Decimal]:
    """Research close on the last session on or before 31 January 2018."""
    if bars.empty or "px_close" not in bars.columns:
        return {}
    work = bars.copy()
    work["date"] = work["date"].map(as_date)
    work = work[work["date"] <= date(2018, 1, 31)].sort_values(["lineage", "date"])
    if work.empty:
        return {}
    last = work.groupby(work["lineage"].astype(str), sort=False)["px_close"].last()
    return {str(lineage): Decimal(str(price)) for lineage, price in last.items() if price == price and float(price) > 0}


def run_month(
    bars: pd.DataFrame,
    scores: pd.DataFrame,
    costs: DeliveryTable,
    taxes: TaxTable,
    spec: MonthSpec,
    calendar: list[date],
    regime: dict[date, bool] | None = None,
    flags: set[tuple[str, date]] | None = None,
    blocked: set[tuple[str, date]] | None = None,
    fmv: dict[str, Decimal] | None = None,
    lookup: dict | None = None,
    last_known: dict[str, date] | None = None,
) -> dict:
    """Rank at the month-end close, trade at the next open. No stops."""
    regime = regime or {}
    if flags is None:
        flags = set()
    if blocked is None:
        blocked = set()
    fmv = fmv or {}
    if lookup is None:
        lookup = _lookup(bars)
    nxt = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    last_bar = last_known if last_known is not None else _last_bar(bars)
    by_signal: dict[date, pd.DataFrame] = {}
    if scores is not None and not scores.empty:
        for day, group in scores.groupby("date", sort=False):
            by_signal[as_date(day)] = group
    entries = {}
    for end in month_end_dates(calendar):
        opened = nxt.get(end)
        if opened is not None and spec.start <= opened <= spec.end:
            entries[opened] = end
    window = [day for day in calendar if spec.start <= day <= spec.end]
    cash = spec.capital
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    daily: list[dict] = []
    ledger = TaxLedger(taxes)
    last_close: dict[str, Decimal] = {}
    cost_rupees = Decimal("0")
    turns: list[float] = []
    size_days: dict[str, int] = defaultdict(int)

    def slip_of(size: str) -> Decimal:
        if str(size) in LIQUID_SIZES:
            return spec.slip_liquid
        return spec.slip_other

    def charge(side: str, fill: Decimal, qty: Decimal, day: date) -> Decimal:
        if qty <= 0:
            return Decimal("0")
        override = Decimal("0") if spec.brokerage == "zero" else None
        if qty == int(qty):
            return costs.leg(side, fill, int(qty), day, flat_override=override).total
        return costs.leg(side, fill * qty, 1, day, flat_override=override).total

    def liquidate(pos: dict, day: date, raw: Decimal, qty: Decimal, reason: str) -> None:
        nonlocal cash, cost_rupees
        held = _qty(pos)
        if qty > held:
            qty = held
        if qty <= 0:
            return
        fill = slipped("SELL", raw, slip_of(pos["size"]))
        bill = charge("SELL", fill, qty, day)
        room = cash + fill * qty
        if bill > room:
            bill = room
        pieces, actual = _take_lots(pos, qty)
        proceeds = fill * qty
        cash += proceeds - bill
        if cash < 0:
            cash = Decimal("0")
        cost_rupees += bill + (raw - fill) * qty
        tax_paid = Decimal("0")
        for lot in pieces:
            share = lot["qty"] / qty
            lot_bill = bill * share
            sale = fill * lot["qty"]
            basis = lot["cost"]
            if taxes.row_for(day).grandfather and lot["entry"] < GRANDFATHER_BEFORE and is_long_term(lot["entry"], day):
                basis = grandfather_basis(lot["cost"] / lot["qty"], fill, lot["fmv"]) * lot["qty"]
            tax = ledger.on_sale(lot["entry"], day, sale - lot_bill - basis)
            if tax >= 0:
                pay = min(tax, cash)
                short = tax - pay
                if short > 0:
                    ledger.paid -= short
                    ledger.total -= short
                cash -= pay
                tax_paid += pay
            else:
                cash += -tax
                tax_paid += tax
        trades.append(
            {
                "lineage": pos["lineage"],
                "symbol": pos["symbol"],
                "size": pos["size"],
                "qty": float(qty),
                "signal_date": pos["signal_date"].isoformat(),
                "entry_date": pos["opened"].isoformat(),
                "exit_date": day.isoformat(),
                "entry_open": float(pos["entry_open"]),
                "exit_px": float(raw),
                "exit_fill": float(fill),
                "reason": reason,
                "pnl": float(proceeds - bill - actual),
                "notional": float(pos["entry_fill"] * qty) if qty == held else float(fill * qty),
                "cost": float(bill + (raw - fill) * qty),
            }
        )
        if _qty(pos) <= 0:
            del positions[pos["lineage"]]

    def _buy_name(lineage: str, group: pd.DataFrame, day: date, signal_day: date, slot: Decimal) -> None:
        nonlocal cash, cost_rupees, traded
        info = _info(group, lineage)
        bar = _bar(lookup, lineage, day)
        if info is None or bar is None or _lower_lock(bar):
            return
        raw = bar[0]
        fill = slipped("BUY", raw, slip_of(info["size"]))
        qty = _buy_qty(cash, fill, slot, lambda q: charge("BUY", fill, q, day), spec)
        if qty <= 0:
            return
        bill = charge("BUY", fill, qty, day)
        cash -= fill * qty + bill
        cost_rupees += bill + (fill - raw) * qty
        traded += fill * qty
        lot = {"entry": day, "qty": qty, "cost": fill * qty + bill, "fmv": fmv.get(lineage)}
        if lineage in positions:
            positions[lineage]["lots"].append(lot)
        else:
            positions[lineage] = _open_position(info, day, signal_day, raw, fill, qty, bill, fmv.get(lineage))

    def _equalise(target: list[str], group: pd.DataFrame, day: date, signal_day: date) -> None:
        """Full rebalance. Kept names are resized. There is no ₹5,000 floor on this book."""
        nonlocal cash, traded
        active = []
        for lineage in target:
            if lineage not in positions and (lineage, day) in blocked:
                continue
            if _bar(lookup, lineage, day) is None:
                continue
            active.append(lineage)
        if not active:
            return
        wealth = _wealth(cash, positions, last_close, day, lookup)
        slot = wealth / Decimal(len(active))
        for lineage in list(active):
            if lineage not in positions:
                continue
            bar = _bar(lookup, lineage, day)
            if bar is None or _lower_lock(bar):
                continue
            raw = bar[0]
            held = _qty(positions[lineage])
            if raw * held <= slot:
                continue
            qty = (raw * held - slot) / raw
            traded += raw * qty
            liquidate(positions[lineage], day, raw, qty, "rebalance")
        wealth = _wealth(cash, positions, last_close, day, lookup)
        slot = wealth / Decimal(len(active))
        for lineage in active:
            bar = _bar(lookup, lineage, day)
            if bar is not None and lineage in positions and _lower_lock(bar):
                continue
            held = _qty(positions[lineage]) if lineage in positions else Decimal("0")
            bar = _bar(lookup, lineage, day)
            if bar is None:
                continue
            gap = slot - bar[0] * held
            if gap <= 1:
                continue
            _buy_name(lineage, group, day, signal_day, gap)

    for today in window:
        traded = Decimal("0")
        equity_before = _wealth(cash, positions, last_close)

        def row(lineage: str):
            return _bar(lookup, lineage, today)

        for lineage, pos in list(positions.items()):
            bar = row(lineage)
            if bar is None:
                pos["absent"] = int(pos.get("absent", 0)) + 1
                last = last_close.get(lineage)
                ended = last_bar.get(lineage, today) < today
                if last is not None and (ended or pos["absent"] >= 5 or today == window[-1]):
                    reason = "delisted" if ended else ("no_bar" if pos["absent"] >= 5 else "sample_end")
                    traded += last * _qty(pos)
                    liquidate(pos, today, last, _qty(pos), reason)
                continue
            pos["absent"] = 0
            if _lower_lock(bar):
                continue
            if (lineage, today) in flags:
                traded += bar[0] * _qty(pos)
                liquidate(pos, today, bar[0], _qty(pos), "event_open")

        signal_day = entries.get(today)
        if signal_day is not None:
            group = by_signal.get(signal_day, pd.DataFrame(columns=["lineage", "symbol", "size", "score"]))
            regime_ok = bool(regime.get(signal_day, False))
            target = _target(group, positions, spec, regime_ok, blocked, today)
            target_set = set(target)
            for lineage, pos in list(positions.items()):
                if lineage in target_set:
                    continue
                bar = row(lineage)
                if bar is None or _lower_lock(bar):
                    continue
                traded += bar[0] * _qty(pos)
                liquidate(pos, today, bar[0], _qty(pos), "rank")
            if spec.mode == "equal":
                _equalise(target, group, today, signal_day)
            else:
                wealth = _wealth(cash, positions, last_close, today, lookup)
                slots = funded_slots(wealth, spec.n, spec.min_notional)
                slot = wealth / Decimal(slots) if slots else wealth
                # A top name that does not fit the slot is skipped. The next score fills it.
                # After a loss the account may fund fewer than N names. It still opens
                # as many ₹5,000 positions as the equity allows.
                for lineage in _ranked_order(group) if slots and not (spec.regime and not regime_ok) else []:
                    if len(positions) >= slots:
                        break
                    if lineage in positions or (lineage, today) in blocked:
                        continue
                    _buy_name(lineage, group, today, signal_day, slot)
            if equity_before > 0:
                turns.append(float(traded / 2 / equity_before))

        for lineage, pos in list(positions.items()):
            bar = row(lineage)
            if bar is None or _lower_lock(bar):
                continue
            tomorrow = nxt.get(today)
            if tomorrow is not None and (lineage, tomorrow) in flags:
                traded += bar[3] * _qty(pos)
                liquidate(pos, today, bar[3], _qty(pos), "event")
            elif today == window[-1] and lineage in positions:
                traded += bar[3] * _qty(pos)
                liquidate(pos, today, bar[3], _qty(pos), "sample_end")
        marked = Decimal("0")
        for lineage, pos in positions.items():
            bar = row(lineage)
            if bar is not None:
                last_close[lineage] = bar[3]
            price = last_close.get(lineage)
            if price is not None:
                marked += price * _qty(pos)
                size_days[str(pos["size"])] += 1
        equity = cash + marked
        if equity < 0:
            equity = Decimal("0")
        pretax = equity + ledger.total
        daily.append(
            {
                "date": today.isoformat(),
                "equity": float(equity),
                "pretax": float(pretax),
                "cash": float(cash),
                "exposure": float(marked / pretax) if pretax > 0 else 0.0,
                "positions": len(positions),
            }
        )
    frame = pd.DataFrame(trades)
    curve = pd.DataFrame(daily)
    return {
        "trades": frame,
        "daily": curve,
        "metrics": metrics(frame, curve, spec.capital, spec.start, spec.end, cost_rupees, turns, size_days),
    }


def _open_position(info, today, signal_day, raw, fill, qty, bill, fmv_price) -> dict:
    return {
        "lineage": info["lineage"],
        "symbol": info["symbol"],
        "size": info["size"],
        "signal_date": signal_day,
        "opened": today,
        "entry_open": raw,
        "entry_fill": fill,
        "absent": 0,
        "lots": [
            {
                "entry": today,
                "qty": qty,
                "cost": fill * qty + bill,
                "fmv": fmv_price,
            }
        ],
    }


def _info(group: pd.DataFrame, lineage: str) -> dict | None:
    if group is None or group.empty:
        return None
    hit = group[group["lineage"].astype(str) == lineage]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return {"lineage": lineage, "symbol": str(row["symbol"]), "size": str(row["size"]), "score": float(row["score"])}


def funded_slots(wealth: Decimal, n: int, minimum: Decimal) -> int:
    """How many minimum-sized positions the account can fund, capped at N."""
    if n < 1 or wealth <= 0:
        return 0
    if minimum <= 0:
        return n
    return min(n, int(wealth // minimum))


def _ranked_order(group: pd.DataFrame) -> list[str]:
    if group is None or group.empty:
        return []
    ranked = group.sort_values(["score", "lineage"], ascending=[False, True])
    return [str(item) for item in ranked["lineage"]]


def _target(group, positions, spec: MonthSpec, regime_ok: bool, blocked: set | None = None, today: date | None = None) -> list[str]:
    blocked = blocked or set()
    if spec.regime and not regime_ok:
        return []
    order = _ranked_order(group)
    if not order:
        return []
    if spec.mode == "equal":
        return [lineage for lineage in order if lineage in positions or (lineage, today) not in blocked]
    rank = {lineage: i + 1 for i, lineage in enumerate(order)}
    kept = [lineage for lineage in order if lineage in positions and rank[lineage] <= 2 * spec.n]
    target = list(kept)
    for lineage in order:
        if len(target) >= spec.n:
            break
        if lineage in target:
            continue
        if lineage not in positions and (lineage, today) in blocked:
            continue
        target.append(lineage)
    return target


def _buy_qty(cash: Decimal, fill: Decimal, slot: Decimal, bill_of, spec: MonthSpec) -> Decimal:
    if spec.fractional:
        if fill <= 0 or slot <= 0 or cash <= 0:
            return Decimal("0")
        qty = slot / fill
        bill = bill_of(qty)
        guard = 0
        while qty > 0 and fill * qty + bill > cash and guard < 8:
            qty = qty * cash / (fill * qty + bill)
            bill = bill_of(qty)
            guard += 1
        if fill * qty < spec.min_notional:
            return Decimal("0")
        return qty
    # The slot has to be at least ₹5,000 before a new name is opened. Shares are
    # the greatest integer that fits in the slot. A ₹5,000 slot rarely divides
    # a share price, so the holding can sit a few rupees under ₹5,000. That
    # remainder is kept. A holding that leaves more than 20% of the slot unspent
    # is skipped: the next share does not fit, and the position is materially
    # under the minimum. One share priced under ₹5,000 in a larger slot is the
    # case this rejects.
    if slot < spec.min_notional or fill <= 0 or cash <= 0:
        return Decimal("0")
    qty = int(slot // fill)
    while qty > 0 and fill * Decimal(qty) + bill_of(Decimal(qty)) > cash:
        qty -= 1
    if qty < 1:
        return Decimal("0")
    notional = fill * Decimal(qty)
    if notional >= spec.min_notional:
        return Decimal(qty)
    if slot - notional <= slot * Decimal("0.20"):
        return Decimal(qty)
    return Decimal("0")


def _qty(pos: dict) -> Decimal:
    return sum((lot["qty"] for lot in pos["lots"]), Decimal("0"))


def _take_lots(pos: dict, qty: Decimal) -> tuple[list[dict], Decimal]:
    left = qty
    pieces = []
    actual = Decimal("0")
    while left > 0 and pos["lots"]:
        lot = pos["lots"][0]
        take = min(lot["qty"], left)
        share = take / lot["qty"]
        cost = lot["cost"] * share
        pieces.append({"entry": lot["entry"], "qty": take, "cost": cost, "fmv": lot["fmv"]})
        lot["qty"] -= take
        lot["cost"] -= cost
        actual += cost
        left -= take
        if lot["qty"] <= 0:
            pos["lots"].pop(0)
    return pieces, actual


def _wealth(cash, positions, last_close, today=None, lookup=None) -> Decimal:
    total = cash
    for lineage, pos in positions.items():
        price = None
        if today is not None and lookup is not None:
            bar = _bar(lookup, lineage, today)
            if bar is not None:
                price = bar[0]
        if price is None:
            price = last_close.get(lineage)
        if price is not None:
            total += price * _qty(pos)
    return total


def _lookup(frame: pd.DataFrame) -> dict[tuple[str, date], tuple]:
    if frame.empty:
        return {}
    lineages = frame["lineage"].astype(str).to_numpy()
    days = [as_date(day) for day in frame["date"]]
    opens = frame["px_open"].to_numpy(dtype=float)
    highs = frame["px_high"].to_numpy(dtype=float)
    lows = frame["px_low"].to_numpy(dtype=float)
    closes = frame["px_close"].to_numpy(dtype=float)
    prev = frame["prev_close"].to_numpy(dtype=float) if "prev_close" in frame.columns else np.full(len(frame), np.nan)
    return {
        (lineages[i], days[i]): (float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]), float(prev[i]))
        for i in range(len(frame))
    }


def _bar(lookup: dict, lineage: str, day: date):
    raw = lookup.get((lineage, day))
    if raw is None:
        return None
    if isinstance(raw[0], Decimal):
        return raw
    prev = raw[4]
    return (
        Decimal(str(raw[0])),
        Decimal(str(raw[1])),
        Decimal(str(raw[2])),
        Decimal(str(raw[3])),
        Decimal(str(prev)) if prev == prev else None,
    )


def _last_bar(frame: pd.DataFrame) -> dict[str, date]:
    if frame.empty or "lineage" not in frame.columns:
        return {}
    last = frame.groupby(frame["lineage"].astype(str), sort=False)["date"].max()
    return {str(lineage): as_date(day) for lineage, day in last.items()}


def _lower_lock(bar) -> bool:
    prev = bar[4]
    if prev is None or not (bar[3] < prev):
        return False
    return max(bar[0], bar[1], bar[2], bar[3]) - min(bar[0], bar[1], bar[2], bar[3]) <= Decimal("0.000001")


def metrics(trades, daily, capital, start, end, cost_rupees, turns, size_days) -> dict:
    years = max((end - start).days / 365.25, 1 / 365.25)
    pretax_end = float(daily["pretax"].iloc[-1]) if not daily.empty else float(capital)
    tax_end = float(daily["equity"].iloc[-1]) if not daily.empty else float(capital)
    cagr = _cagr(pretax_end, float(capital), years)
    cagr_tax = _cagr(tax_end, float(capital), years)
    max_dd, vol, sharpe, exposure = _path(daily)
    pnl = float(trades["pnl"].sum()) if trades is not None and not trades.empty else 0.0
    by_size = _size_rows(trades)
    hold_days = int(sum(size_days.values()))
    return {
        "cagr": cagr,
        "cagr_tax": cagr_tax,
        "max_dd": max_dd,
        "vol": vol,
        "sharpe": sharpe,
        "turnover": float(np.mean(turns)) if turns else 0.0,
        "cost_drag": float(cost_rupees) / (float(daily["pretax"].mean()) * years) if not daily.empty and float(daily["pretax"].mean()) else 0.0,
        "pnl": pnl,
        "end_equity": pretax_end,
        "end_equity_tax": tax_end,
        "trades": int(len(trades)) if trades is not None else 0,
        "exposure": exposure,
        "by_size": by_size,
        "by_year": _year_rows(trades, daily),
        "hold_share": {size: (count / hold_days if hold_days else 0.0) for size, count in size_days.items()},
    }


def _cagr(end_equity: float, capital: float, years: float) -> float:
    if end_equity <= 0 or capital <= 0:
        return -1.0
    return (end_equity / capital) ** (1.0 / years) - 1.0


def _path(daily: pd.DataFrame) -> tuple[float, float, float | None, float]:
    if daily.empty:
        return 0.0, 0.0, None, 0.0
    equity = daily["pretax"].to_numpy(dtype=float)
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        draw = np.where(peak > 0, (peak - equity) / peak, 0.0)
    ret = np.diff(equity) / np.where(equity[:-1] > 0, equity[:-1], np.nan) if len(equity) > 1 else np.array([])
    ret = ret[np.isfinite(ret)]
    vol = float(np.std(ret, ddof=1) * np.sqrt(252)) if len(ret) > 1 else 0.0
    sharpe = None
    if len(ret) > 1 and float(np.std(ret, ddof=1)) > 0:
        sharpe = float(np.mean(ret) / np.std(ret, ddof=1) * np.sqrt(252))
    exposure = float(daily["exposure"].mean())
    return float(np.max(draw)) if len(draw) else 0.0, vol, sharpe, exposure


def _size_rows(trades: pd.DataFrame) -> list[dict]:
    if trades is None or trades.empty:
        return []
    rows = []
    for size, part in trades.groupby("size", sort=False):
        rows.append({"size": str(size), "trades": int(len(part)), "pnl": float(part["pnl"].sum())})
    return rows


def _year_rows(trades: pd.DataFrame, daily: pd.DataFrame) -> list[dict]:
    if daily.empty:
        return []
    curve = daily.copy()
    curve["year"] = curve["date"].str.slice(0, 4).astype(int)
    rows = []
    for year, part in curve.groupby("year"):
        booked = trades[trades["exit_date"].str.startswith(str(year))] if trades is not None and not trades.empty else trades
        rows.append(
            {
                "year": int(year),
                "trades": 0 if booked is None else int(len(booked)),
                "pnl": 0.0 if booked is None or booked.empty else float(booked["pnl"].sum()),
                "end_pretax": float(part["pretax"].iloc[-1]),
                "end_tax": float(part["equity"].iloc[-1]),
                "exposure": float(part["exposure"].mean()),
            }
        )
    return rows


def index_path(frame: pd.DataFrame, start: date, end: date, capital: Decimal, costs: DeliveryTable, taxes: TaxTable, brokerage: str) -> dict | None:
    """Buy the index at the first open and sell at the last close. One round trip, then tax."""
    work = frame.copy()
    work["date"] = work["date"].map(as_date)
    work = work[(work["date"] >= start) & (work["date"] <= end)].sort_values("date")
    if len(work) < 2 or not (float(work["open"].iloc[0]) > 0):
        return None
    first = Decimal(str(work["open"].iloc[0]))
    last = Decimal(str(work["close"].iloc[-1]))
    override = Decimal("0") if brokerage == "zero" else None
    buy_day = as_date(work["date"].iloc[0])
    sell_day = as_date(work["date"].iloc[-1])
    # One delivery ticket on a fractional holding, so the cash residual is the charge rather than a whole index point.
    buy = costs.leg("BUY", capital, 1, buy_day, flat_override=override).total
    qty = (capital - buy) / first
    if qty <= 0:
        return None
    buy = costs.leg("BUY", first * qty, 1, buy_day, flat_override=override).total
    if first * qty + buy > capital:
        qty = (capital - buy) / first
        buy = costs.leg("BUY", first * qty, 1, buy_day, flat_override=override).total
    sell = costs.leg("SELL", last * qty, 1, sell_day, flat_override=override).total
    basis = first * qty + buy
    proceeds = last * qty - sell
    fmv_close = _fmv_close(work)
    taxable = proceeds - (first * qty)
    if taxes.row_for(sell_day).grandfather and buy_day < GRANDFATHER_BEFORE and is_long_term(buy_day, sell_day) and fmv_close is not None:
        basis_ps = grandfather_basis(first + buy / qty, last, fmv_close)
        taxable = (last - basis_ps) * qty - sell
    else:
        taxable = proceeds - basis
    ledger = TaxLedger(taxes)
    tax = ledger.on_sale(buy_day, sell_day, taxable)
    residual = capital - basis
    if residual < 0:
        residual = Decimal("0")
    pretax_end = float(residual + proceeds)
    tax_end = pretax_end - float(tax if tax > 0 else 0)
    years = max((end - start).days / 365.25, 1 / 365.25)
    closes = work["close"].to_numpy(dtype=float)
    marks = float(residual) + float(qty) * closes
    path = np.concatenate(([float(capital)], marks))
    peak = np.maximum.accumulate(path)
    draw = np.where(peak > 0, (peak - path) / peak, 0.0)
    return {
        "cagr": _cagr(pretax_end, float(capital), years),
        "cagr_tax": _cagr(tax_end, float(capital), years),
        "max_dd": float(np.max(draw)),
        "end_equity": pretax_end,
        "end_equity_tax": tax_end,
    }


def _fmv_close(work: pd.DataFrame) -> Decimal | None:
    prior = work[work["date"].map(as_date) <= date(2018, 1, 31)]
    if prior.empty:
        return None
    return Decimal(str(prior["close"].iloc[-1]))


def judge(dev: dict, oos: dict, nifty: dict | None, basket: dict | None, random_cagrs: list[float], cliffs: list[str]) -> dict:
    """Every listed condition is required. Top-10 removal is reported and is not a condition."""
    reasons = []
    if dev["cagr"] <= 0 or dev["pnl"] <= 0:
        reasons.append("DEV is not positive after costs")
    if oos["cagr"] <= 0 or oos["pnl"] <= 0:
        reasons.append("OOS is not positive after costs")
    if nifty is None or basket is None:
        reasons.append("a benchmark is missing")
    else:
        if oos["cagr"] <= nifty["cagr"] or oos["cagr"] <= basket["cagr"]:
            reasons.append("OOS CAGR does not beat both benchmarks after costs")
        if oos["cagr_tax"] <= nifty["cagr_tax"] or oos["cagr_tax"] <= basket["cagr_tax"]:
            reasons.append("OOS after-tax CAGR does not beat both benchmarks")
        if oos["max_dd"] > NIFTY_DD_MULT * nifty["max_dd"]:
            reasons.append("OOS drawdown is worse than 1.5× Nifty 500")
    beaten = sum(1 for value in random_cagrs if oos["cagr"] > value)
    share = beaten / len(random_cagrs) if random_cagrs else 0.0
    if share < RANDOM_BEAT:
        reasons.append("does not beat 95% of the random seeds")
    if cliffs:
        reasons.append("a ±20% lookback change cliffs")
    return {"pass": not reasons, "reasons": reasons, "random_hit": share, "random_beaten": beaten}


def variant_grid(kind: str) -> list[tuple[str, dict]]:
    base = "M1" if kind.startswith("M1") else "M2"
    return param_grid(DEFAULTS[base])


__all_helpers__ = (cliff_labels, shift_param)
