"""Phase 3 research book. No orders.

The gate is fixed here, before any run. One primary book is judged:
₹50,000, 5 positions, Upstox brokerage. The other capital, slot, and
brokerage combinations are reported and are not extra attempts at the gate.

Holdout 2026-07-01 through 2026-10-01 is refused unless the caller has already
decided the strategy passed and sets `holdout=True`.

Sizing is equity divided by the slot count at the entry, then cut so the buy
does not spend more cash than the account has. A position still sells at the
previous close when the next session is an event-guard flag, and a lower
circuit lock is not sold.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd

from app.swing.delivery import DeliveryTable
from app.swing.engine import LIQUID_SIZES, slipped
from app.swing.fixes import as_date, blocked_entries, event_flags

HOLDOUT_START = date(2026, 7, 1)
HOLDOUT_END = date(2026, 10, 1)
DEV_START = date(2016, 1, 1)
DEV_END = date(2021, 12, 31)
OOS_START = date(2022, 1, 1)
OOS_END = date(2026, 6, 30)
FORWARD_START = date(2026, 10, 2)
PRIMARY_CAPITAL = Decimal("50000")
PRIMARY_SLOTS = 5
PRIMARY_BROKERAGE = "upstox"
RANDOM_SEEDS = tuple(range(1, 21))
MIN_OOS_TRADES = 100
MIN_AVG_TRADE = 0.005
RANDOM_BEAT = 0.95
NIFTY_DD_MULT = 1.5
TOP_WINNERS = 10
CLIFF_CAGR = 0.5


def overlaps_holdout(start: date, end: date) -> bool:
    return not (end < HOLDOUT_START or start > HOLDOUT_END)


def research_windows(latest: date) -> dict[str, tuple[date, date]]:
    """DEV, OOS, and forward. The holdout is not a window."""
    return {
        "DEV": (DEV_START, DEV_END),
        "OOS": (OOS_START, OOS_END),
        "FORWARD": (FORWARD_START, latest),
    }


def shift_param(value: float | int, direction: float) -> float | int:
    """±20% of a default. Integers round half up, and a no-change rounds away by one."""
    updated = float(value) * (1.0 + direction)
    if isinstance(value, int) and not isinstance(value, bool):
        shifted = int(Decimal(str(updated)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if shifted == value:
            shifted = value + (1 if direction > 0 else -1)
        return max(shifted, 2)
    return updated


def param_grid(params: dict) -> list[tuple[str, dict]]:
    """One parameter at a time, down 20% and up 20%."""
    grid = []
    for key, value in params.items():
        for direction, label in ((-0.2, "-20%"), (0.2, "+20%")):
            nxt = dict(params)
            nxt[key] = shift_param(value, direction)
            grid.append((f"{key} {label}", nxt))
    return grid


@dataclass(frozen=True)
class BookSpec:
    capital: Decimal
    max_positions: int
    start: date
    end: date
    brokerage: str = PRIMARY_BROKERAGE
    slip_liquid: Decimal = Decimal("0.001")
    slip_other: Decimal = Decimal("0.0025")
    holdout: bool = False

    def __post_init__(self) -> None:
        if self.brokerage not in ("upstox", "zero"):
            raise ValueError("brokerage is upstox or zero")
        if self.max_positions < 1 or self.capital <= 0:
            raise ValueError("capital and slots must be positive")
        if self.start > self.end:
            raise ValueError("start is after end")
        if overlaps_holdout(self.start, self.end) and not self.holdout:
            raise ValueError("holdout 2026-07-01 through 2026-10-01 is untouched")


def _slip(size: str, spec: BookSpec) -> Decimal:
    if str(size) in LIQUID_SIZES:
        return spec.slip_liquid
    return spec.slip_other


def _charge(costs: DeliveryTable, spec: BookSpec, side: str, price, qty: int, on: date):
    if spec.brokerage == "zero":
        return costs.leg(side, price, qty, on, flat_override=Decimal("0"))
    return costs.leg(side, price, qty, on)


def _exit_average(bar, n: int) -> float:
    """4, 5, and 6 are the default exit average and its ±20% neighbours."""
    if n == 4:
        return bar[6]
    if n == 6:
        return bar[7]
    return bar[4]


def _lower_lock(open_, high, low, close, prev) -> bool:
    if prev is None or prev != prev or not (close < prev):
        return False
    return max(open_, high, low, close) - min(open_, high, low, close) <= 1e-6


def _buy_qty(cash: Decimal, fill: Decimal, slot: Decimal, costs, spec: BookSpec, on: date) -> int:
    if fill <= 0 or slot <= 0 or cash <= 0:
        return 0
    qty = int(slot // fill)
    while qty > 0:
        bill = fill * qty + _charge(costs, spec, "BUY", fill, qty, on).total
        if bill <= cash:
            return qty
        qty -= 1
    return 0


def run_research(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    costs: DeliveryTable,
    spec: BookSpec,
    calendar: list[date] | None = None,
    flags: pd.DataFrame | None = None,
    week_ends: set[date] | None = None,
    lookup: dict | None = None,
) -> dict:
    """Fill the book. Signals are closes; buys are the next open."""
    work = bars
    if calendar is None:
        calendar = sorted({as_date(day) for day in work["date"]})
    if flags is None:
        flags = event_flags(work)
    if lookup is None:
        lookup = bar_lookup(work)
    ends = week_ends or set()
    index = {day: i for i, day in enumerate(calendar)}
    nxt = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    blocked = blocked_entries(flags, calendar, 2)
    flag_set = set()
    if flags is not None and not flags.empty:
        flag_set = {(str(a), as_date(b)) for a, b in zip(flags["lineage"], flags["date"], strict=False)}
    pending: dict[date, list] = defaultdict(list)
    if signals is not None and not signals.empty:
        for signal in signals.itertuples(index=False):
            entry = nxt.get(as_date(signal.date))
            if entry is None or entry < spec.start or entry > spec.end:
                continue
            pending[entry].append(signal)
    cash = spec.capital
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    daily: list[dict] = []
    last_close: dict[str, float] = {}
    window = [day for day in calendar if spec.start <= day <= spec.end]

    def row(lineage: str, day: date):
        return lookup.get((lineage, day))

    def mark() -> Decimal:
        total = cash
        for lineage, pos in positions.items():
            price = last_close.get(lineage)
            if price is not None:
                total += Decimal(str(price)) * pos["qty"]
        return total

    def sell(pos: dict, day: date, price: Decimal, reason: str) -> None:
        nonlocal cash
        slip = _slip(pos["size"], spec)
        fill = slipped("SELL", price, slip)
        charges = _charge(costs, spec, "SELL", fill, pos["qty"], day).total
        cash += fill * pos["qty"] - charges
        bought = pos["entry_fill"] * pos["qty"] + pos["entry_charges"]
        slip_rupees = (pos["entry_fill"] - pos["entry_open"]) * pos["qty"] + (price - fill) * pos["qty"]
        cost = pos["entry_charges"] + charges + slip_rupees
        trades.append(
            {
                "lineage": pos["lineage"],
                "symbol": pos["symbol"],
                "size": pos["size"],
                "score": pos["score"],
                "qty": pos["qty"],
                "signal_date": pos["signal_date"].isoformat(),
                "entry_date": pos["entry_date"].isoformat(),
                "exit_date": day.isoformat(),
                "entry_open": float(pos["entry_open"]),
                "entry_fill": float(pos["entry_fill"]),
                "exit_px": float(price),
                "exit_fill": float(fill),
                "stop": float(pos["stop"]),
                "reason": reason,
                "hold_sessions": index[day] - pos["entry_index"] + 1,
                "pnl": float(fill * pos["qty"] - charges - bought),
                "notional": float(pos["entry_fill"] * pos["qty"]),
                "open_notional": float(pos["entry_open"] * pos["qty"]),
                "cost": float(cost),
            }
        )
        del positions[pos["lineage"]]

    for today in window:
        exited: set[str] = set()
        for lineage, pos in list(positions.items()):
            bar = row(lineage, today)
            if bar is None or _lower_lock(bar[0], bar[1], bar[2], bar[3], bar[5]):
                continue
            open_ = Decimal(str(bar[0]))
            if (lineage, today) in flag_set or open_ <= pos["stop"]:
                sell(pos, today, open_, "stop_gap" if open_ <= pos["stop"] else "event_open")
                exited.add(lineage)
        free = spec.max_positions - len(positions)
        slot = mark() / Decimal(spec.max_positions)
        ordered = sorted(pending.get(today, []), key=lambda s: (-float(s.score), str(s.lineage)))
        seen: set[str] = set()
        for signal in ordered:
            lineage = str(signal.lineage)
            if lineage in seen or lineage in positions:
                continue
            seen.add(lineage)
            if free <= 0 or (lineage, today) in blocked:
                continue
            bar = row(lineage, today)
            if bar is None:
                continue
            open_ = Decimal(str(bar[0]))
            fill = slipped("BUY", open_, _slip(str(signal.size), spec))
            qty = _buy_qty(cash, fill, slot, costs, spec, today)
            if qty < 1:
                continue
            charges = _charge(costs, spec, "BUY", fill, qty, today).total
            cash -= fill * qty + charges
            stop_px = getattr(signal, "stop_price", None)
            if stop_px is not None and stop_px == stop_px:
                stop = Decimal(str(stop_px))
            else:
                stop = open_ - Decimal(str(signal.atr_mult)) * Decimal(str(signal.atr))
            positions[lineage] = {
                "lineage": lineage,
                "symbol": str(signal.symbol),
                "qty": qty,
                "entry_date": today,
                "entry_index": index[today],
                "entry_open": open_,
                "entry_fill": fill,
                "entry_charges": charges,
                "stop": stop,
                "size": str(signal.size),
                "score": float(signal.score),
                "signal_date": as_date(signal.date),
                "exit_kind": str(getattr(signal, "exit_kind", "time")),
                "exit_sma": int(getattr(signal, "exit_sma", 5) or 5),
                "max_hold": int(getattr(signal, "max_hold", 5) or 5),
            }
            free -= 1
        for lineage, pos in list(positions.items()):
            if lineage in exited:
                continue
            bar = row(lineage, today)
            if bar is None or _lower_lock(bar[0], bar[1], bar[2], bar[3], bar[5]):
                continue
            open_ = Decimal(str(bar[0]))
            low = Decimal(str(bar[2]))
            # A position entered this morning can already be through its stop.
            # Overnight gaps were sold before the entries.
            if open_ <= pos["stop"]:
                sell(pos, today, open_, "stop_gap")
                exited.add(lineage)
            elif low <= pos["stop"]:
                sell(pos, today, pos["stop"], "stop")
                exited.add(lineage)
        for lineage, pos in list(positions.items()):
            if lineage in exited:
                continue
            bar = row(lineage, today)
            if bar is None or _lower_lock(bar[0], bar[1], bar[2], bar[3], bar[5]):
                continue
            close = Decimal(str(bar[3]))
            tomorrow = nxt.get(today)
            held = index[today] - pos["entry_index"] + 1
            sma = _exit_average(bar, pos["exit_sma"])
            week_exit = pos["exit_kind"] == "week" and today in ends and today.isocalendar()[:2] == pos["entry_date"].isocalendar()[:2]
            sma_exit = pos["exit_kind"] == "sma" and sma == sma and close > Decimal(str(sma))
            if tomorrow is not None and (lineage, tomorrow) in flag_set:
                sell(pos, today, close, "event")
            elif sma_exit:
                sell(pos, today, close, "sma")
            elif week_exit:
                sell(pos, today, close, "week")
            elif held >= pos["max_hold"]:
                sell(pos, today, close, "time")
            elif today == window[-1]:
                sell(pos, today, close, "sample_end")
        marked = Decimal(0)
        for lineage, pos in positions.items():
            bar = row(lineage, today)
            if bar is not None:
                last_close[lineage] = float(bar[3])
            price = last_close.get(lineage)
            if price is not None:
                marked += Decimal(str(price)) * pos["qty"]
        equity = cash + marked
        daily.append(
            {
                "date": today.isoformat(),
                "equity": float(equity),
                "cash": float(cash),
                "exposure": float(marked / equity) if equity else 0.0,
                "positions": len(positions),
            }
        )
    return {
        "trades": pd.DataFrame(trades),
        "daily": pd.DataFrame(daily),
        "metrics": metrics(pd.DataFrame(trades), pd.DataFrame(daily), spec.capital, spec.start, spec.end),
    }


def bar_lookup(frame: pd.DataFrame) -> dict[tuple[str, date], tuple]:
    """(open, high, low, close, sma5, previous close) for each lineage and session."""
    lineages = frame["lineage"].astype(str).to_numpy()
    days = [as_date(day) for day in frame["date"]]
    opens = frame["px_open"].to_numpy(dtype=float)
    highs = frame["px_high"].to_numpy(dtype=float)
    lows = frame["px_low"].to_numpy(dtype=float)
    closes = frame["px_close"].to_numpy(dtype=float)
    def column(name: str) -> np.ndarray:
        if name in frame.columns:
            return frame[name].to_numpy(dtype=float)
        return np.full(len(frame), np.nan)

    sma4, sma5, sma6 = column("sma4"), column("sma5"), column("sma6")
    prev = column("prev_close")
    return {
        (lineages[i], days[i]): (opens[i], highs[i], lows[i], closes[i], sma5[i], prev[i], sma4[i], sma6[i])
        for i in range(len(frame))
    }


def metrics(trades: pd.DataFrame, daily: pd.DataFrame, capital: Decimal, start: date, end: date) -> dict:
    years = max((end - start).days / 365.25, 1 / 365.25)
    end_equity = float(daily["equity"].iloc[-1]) if not daily.empty else float(capital)
    if end_equity <= 0:
        cagr = -1.0
    else:
        cagr = (end_equity / float(capital)) ** (1.0 / years) - 1.0
    if daily.empty:
        max_dd = 0.0
        exposure = 0.0
    else:
        equity = daily["equity"].to_numpy(dtype=float)
        peak = np.maximum.accumulate(equity)
        with np.errstate(divide="ignore", invalid="ignore"):
            draw = np.where(peak > 0, (peak - equity) / peak, 0.0)
        max_dd = float(np.max(draw))
        exposure = float(daily["exposure"].mean())
    if trades.empty:
        pnl = 0.0
        count = 0
        win = 0.0
        pf = 0.0
        avg = 0.0
        cost = 0.0
        by_size = []
    else:
        pnl = float(trades["pnl"].sum())
        count = int(len(trades))
        win = float((trades["pnl"] > 0).mean())
        gains = float(trades.loc[trades["pnl"] > 0, "pnl"].sum())
        losses = float(-trades.loc[trades["pnl"] < 0, "pnl"].sum())
        pf = gains / losses if losses else (float("inf") if gains else 0.0)
        avg = float((trades["pnl"] / trades["notional"]).mean()) if "notional" in trades.columns else 0.0
        cost = float((trades["cost"] / trades["open_notional"]).mean()) if "cost" in trades.columns else 0.0
        by_size = _size_rows(trades)
    return {
        "cagr": cagr,
        "max_dd": max_dd,
        "pf": pf,
        "trades": count,
        "win": win,
        "avg_trade_pct": avg,
        "cost_pct": cost,
        "exposure": exposure,
        "pnl": pnl,
        "end_equity": end_equity,
        "by_size": by_size,
        "by_year": _year_rows(trades, daily),
    }


def _size_rows(trades: pd.DataFrame) -> list[dict]:
    rows = []
    for size, part in trades.groupby("size", sort=False):
        rows.append(
            {
                "size": str(size),
                "trades": int(len(part)),
                "pnl": float(part["pnl"].sum()),
                "win": float((part["pnl"] > 0).mean()),
                "avg_trade_pct": float((part["pnl"] / part["notional"]).mean()),
            }
        )
    return rows


def _year_rows(trades: pd.DataFrame, daily: pd.DataFrame) -> list[dict]:
    if daily.empty:
        return []
    curve = daily.copy()
    curve["year"] = curve["date"].str.slice(0, 4).astype(int)
    rows = []
    for year, part in curve.groupby("year"):
        booked = trades[trades["exit_date"].str.startswith(str(year))] if not trades.empty and "exit_date" in trades.columns else trades.iloc[0:0]
        rows.append(
            {
                "year": int(year),
                "trades": int(len(booked)),
                "pnl": float(booked["pnl"].sum()) if len(booked) else 0.0,
                "end_equity": float(part["equity"].iloc[-1]),
                "exposure": float(part["exposure"].mean()),
            }
        )
    return rows


def pnl_without_top_winners(pnls: list[float], n: int = TOP_WINNERS) -> float:
    """Net P&L after deleting the largest winning trades. Fewer than `n` winners deletes every winner."""
    winners = sorted((p for p in pnls if p > 0), reverse=True)
    return float(sum(pnls) - sum(winners[:n]))


def cliff_labels(base_cagr: float, base_pnl: float, variants: list[tuple[str, float, float]]) -> list[str]:
    """A variant cliffs when OOS profit disappears, or CAGR falls below half the base."""
    found = []
    for label, cagr, pnl in variants:
        if base_pnl > 0 and pnl <= 0:
            found.append(f"{label} profit gone")
        elif base_cagr > 0 and cagr < CLIFF_CAGR * base_cagr:
            found.append(f"{label} CAGR below half")
    return found


def judge(
    dev: dict,
    oos: dict,
    nifty: dict | None,
    random_cagrs: list[float],
    top10_pnl: float,
    cliffs: list[str],
) -> dict:
    """Every condition is required. Missing a Nifty series fails the benchmark clause."""
    reasons = []
    if dev["cagr"] <= 0 or dev["pnl"] <= 0:
        reasons.append("DEV is not positive after costs")
    if oos["cagr"] <= 0 or oos["pnl"] <= 0:
        reasons.append("OOS is not positive after costs")
    if nifty is None:
        reasons.append("Nifty 500 does not cover OOS")
    else:
        if oos["cagr"] <= nifty["cagr"]:
            reasons.append("OOS CAGR is not above Nifty 500")
        limit = NIFTY_DD_MULT * nifty["max_dd"]
        if oos["max_dd"] > limit:
            reasons.append("OOS drawdown is worse than 1.5× Nifty 500")
    beaten = sum(1 for value in random_cagrs if oos["cagr"] > value)
    share = beaten / len(random_cagrs) if random_cagrs else 0.0
    if share < RANDOM_BEAT:
        reasons.append("does not beat 95% of the random seeds")
    if oos["avg_trade_pct"] < MIN_AVG_TRADE:
        reasons.append("average net trade is under 0.5%")
    if top10_pnl <= 0:
        reasons.append("OOS profit does not survive removing the top 10 winners")
    if cliffs:
        reasons.append("a ±20% parameter change cliffs")
    if oos["trades"] < MIN_OOS_TRADES:
        reasons.append("fewer than 100 OOS trades")
    return {"pass": not reasons, "reasons": reasons, "random_hit": share, "random_beaten": beaten}


def random_signals(
    counts: list[tuple[date, int]],
    pool: pd.DataFrame,
    seed: int,
    kind: str,
    prev_of: dict[date, date],
    blocked: set[tuple[str, date]],
) -> pd.DataFrame:
    """Same number of entries on each date, drawn from that day's eligible names.

    `counts` is (entry date, filled trades). The signal date is the previous
    session, so the book still buys the next open. The exit rule matches `kind`.
    """
    rng = np.random.default_rng(seed)
    exit_kind = {"S2": "week", "S3": "sma"}.get(kind, "time")
    atr_mult = {"S1": 2.0, "S2": 2.0, "S3": 2.5, "S4": 2.0}.get(kind, 0.0)
    rows = []
    if pool.empty:
        return pd.DataFrame()
    grouped = {as_date(day): part for day, part in pool.groupby("date", sort=False)}
    for entry, count in counts:
        signal_day = prev_of.get(entry)
        part = None if signal_day is None else grouped.get(signal_day)
        if part is None or count < 1:
            continue
        mask = np.array([(str(lineage), entry) not in blocked for lineage in part["lineage"]], dtype=bool)
        choice = part.iloc[np.flatnonzero(mask)]
        if choice.empty:
            continue
        take = min(count, len(choice))
        picked = choice.iloc[rng.choice(len(choice), size=take, replace=False)]
        for item in picked.itertuples(index=False):
            stop = float(item.px_low) if kind == "S5" else float("nan")
            rows.append(
                {
                    "lineage": str(item.lineage),
                    "symbol": str(item.symbol),
                    "date": signal_day,
                    "score": float(rng.random()),
                    "size": str(item.size),
                    "atr": float(item.atr),
                    "atr_mult": atr_mult,
                    "stop_price": stop,
                    "exit_kind": exit_kind,
                    "exit_sma": 5,
                    "max_hold": 5,
                }
            )
    return pd.DataFrame(rows)


def index_hold(frame: pd.DataFrame, start: date, end: date, capital: float) -> dict | None:
    """Buy the index at the first open in the window and hold to the last close. No costs.

    `frame` has date, open, close. A total-return series is used when the caller
    passes one. This function does not invent one.
    """
    work = frame.copy()
    work["date"] = work["date"].map(as_date)
    work = work[(work["date"] >= start) & (work["date"] <= end)].sort_values("date")
    if work.empty or not (work["open"].iloc[0] > 0):
        return None
    first_open = float(work["open"].iloc[0])
    equity = capital * work["close"].to_numpy(dtype=float) / first_open
    peak = np.maximum.accumulate(np.concatenate(([capital], equity)))
    path = np.concatenate(([capital], equity))
    draw = np.where(peak > 0, (peak - path) / peak, 0.0)
    years = max((end - start).days / 365.25, 1 / 365.25)
    last = float(equity[-1])
    cagr = -1.0 if last <= 0 else (last / capital) ** (1.0 / years) - 1.0
    return {"cagr": cagr, "max_dd": float(np.max(draw)), "end_equity": last, "sessions": int(len(work))}
