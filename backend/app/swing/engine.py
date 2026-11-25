"""Daily swing book for research. No orders, no live feed, no same-day fill.

A signal is known at the close of day D. The buy is the open of the next
session. The position is out by the close of the fifth session (D+5), or
sooner on a stop. A gap through the stop sells at the open. A day locked at
the lower circuit (open = high = low = close, below the previous close) cannot
be sold.

The event guard looks at the next session's flag before that session's open
and sells at today's close, so the book does not hold across a flagged date.
If today is a lower lock, that sell does not happen. A flagged session and the
two sessions after it take no new entry.

This phase trades 2016-01-01 through 2018-12-31 only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pandas as pd

from app.swing.delivery import DeliveryTable
from app.swing.fixes import EVENT_MOVE, as_date, blocked_entries, event_flags, research_prices

DRY_RUN_START = date(2016, 1, 1)
DRY_RUN_END = date(2018, 12, 31)
LIQUID_SIZES = frozenset({"large", "mid"})


def research_window(start: date, end: date) -> str | None:
    """This phase refuses any window other than the 2016–2018 dry run."""
    if start != DRY_RUN_START or end != DRY_RUN_END:
        return "Swing Phase 2 dry run is 2016-01-01 through 2018-12-31 only. 2019+ is not run."
    return None


@dataclass(frozen=True)
class EngineConfig:
    capital: Decimal = Decimal("50000")
    max_positions: int = 5
    max_hold: int = 5
    atr_mult: float = 2.0
    atr_n: int = 14
    breakout_n: int = 20
    nifty_sma: int = 200
    slip_liquid: Decimal = Decimal("0.001")
    slip_other: Decimal = Decimal("0.0025")
    start: date = DRY_RUN_START
    end: date = DRY_RUN_END
    event_move: float = EVENT_MOVE
    entry_ban: int = 2

    def __post_init__(self) -> None:
        if self.start < DRY_RUN_START or self.end > DRY_RUN_END:
            raise ValueError("Swing Phase 2 dry run is 2016-01-01 through 2018-12-31 only")
        if self.start > self.end:
            raise ValueError("start is after end")
        if self.max_positions < 1 or self.max_hold < 1:
            raise ValueError("positions and hold must be at least 1")
        if self.atr_n < 1 or self.breakout_n < 1 or self.nifty_sma < 1:
            raise ValueError("lookbacks must be at least 1")
        if self.slip_liquid < 0 or self.slip_other < 0:
            raise ValueError("slippage must be >= 0")


@dataclass
class Position:
    lineage: str
    symbol: str
    qty: int
    entry_date: date
    entry_index: int
    entry_open: Decimal
    entry_fill: Decimal
    entry_charges: Decimal
    stop: Decimal
    target: Decimal | None
    size: str
    score: float
    signal_date: date


def slipped(side: str, price: Decimal, slip: Decimal) -> Decimal:
    """Buy pays up, sell receives less."""
    if side == "BUY":
        return price * (Decimal(1) + slip)
    if side == "SELL":
        return price * (Decimal(1) - slip)
    raise ValueError(f"side must be BUY or SELL, got {side!r}")


def _slip_for(size: str, cfg: EngineConfig) -> Decimal:
    if str(size) in LIQUID_SIZES:
        return cfg.slip_liquid
    return cfg.slip_other


def _wilder(true_range: list[float], n: int) -> list[float | None]:
    """Seed with the mean of the first n true ranges, then (prev*(n-1) + tr) / n."""
    out: list[float | None] = [None] * len(true_range)
    if len(true_range) < n:
        return out
    seed = sum(true_range[:n]) / n
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(true_range)):
        prev = (prev * (n - 1) + true_range[i]) / n
        out[i] = prev
    return out


def _true_range(high: list[float], low: list[float], close: list[float]) -> list[float]:
    out = []
    for i, (hi, lo, cl) in enumerate(zip(high, low, close, strict=True)):
        if i == 0:
            out.append(hi - lo)
            continue
        prev = close[i - 1]
        out.append(max(hi - lo, abs(hi - prev), abs(lo - prev)))
    return out


def nifty_above(closes: pd.Series, window: int) -> dict[date, bool]:
    """True on days where the index close is above its average through that day."""
    series = closes.copy()
    series.index = [as_date(value) for value in series.index]
    series = series[~series.index.duplicated(keep="last")].sort_index()
    average = series.rolling(window, min_periods=window).mean()
    return {day: bool(series.loc[day] > average.loc[day]) for day in series.index if pd.notna(average.loc[day])}


def prepare_signals(bars: pd.DataFrame, nifty_close: pd.Series, cfg: EngineConfig) -> pd.DataFrame:
    """Breakout signals known at the close. Day D's high is not inside the prior window.

    Buy when the close is above the prior `breakout_n` highs and the index is
    above its average. The score is how far the close cleared that high.
    """
    columns = ["lineage", "symbol", "date", "score", "atr", "size", "px_close", "prior_high"]
    work = research_prices(bars)
    if work.empty:
        return pd.DataFrame(columns=columns)
    work["date"] = work["date"].map(as_date)
    work = work.sort_values(["lineage", "date"]).drop_duplicates(["lineage", "date"], keep="last")
    if "passes" not in work.columns:
        work["passes"] = False
    work["passes"] = work["passes"].fillna(False).astype(bool)
    if "size" not in work.columns:
        work["size"] = "rest"
    work["size"] = work["size"].fillna("rest").astype(str)
    above = nifty_above(nifty_close, cfg.nifty_sma)
    rows = []
    for lineage, group in work.groupby("lineage", sort=False):
        group = group.reset_index(drop=True)
        high = group["px_high"].astype(float).tolist()
        low = group["px_low"].astype(float).tolist()
        close = group["px_close"].astype(float).tolist()
        prior = pd.Series(high, dtype="float64").shift(1).rolling(cfg.breakout_n, min_periods=cfg.breakout_n).max().tolist()
        atr = _wilder(_true_range(high, low, close), cfg.atr_n)
        passed = group["passes"].tolist()
        symbols = group["symbol"].astype(str).tolist()
        sizes = group["size"].astype(str).tolist()
        dates = group["date"].tolist()
        for i in range(len(group)):
            level = prior[i]
            width = atr[i]
            if level != level or width is None or width <= 0:  # NaN check without numpy
                continue
            if not passed[i]:
                continue
            if close[i] <= level:
                continue
            day = dates[i]
            if not above.get(day, False):
                continue
            rows.append(
                {
                    "lineage": str(lineage),
                    "symbol": symbols[i],
                    "date": day,
                    "score": close[i] / level - 1,
                    "atr": float(width),
                    "size": sizes[i],
                    "px_close": close[i],
                    "prior_high": float(level),
                }
            )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)


def _lower_lock(open_, high, low, close, prev_close) -> bool:
    if prev_close is None or not (close < prev_close):
        return False
    return max(open_, high, low, close) - min(open_, high, low, close) <= 1e-6


def _buy_qty(cash: Decimal, fill: Decimal, slot: Decimal, costs: DeliveryTable, on: date) -> int:
    if fill <= 0:
        return 0
    qty = int(slot // fill)
    while qty > 0:
        charges = costs.leg("BUY", fill, qty, on).total
        if fill * qty + charges <= cash:
            return qty
        qty -= 1
    return 0


def _calendar(bars: pd.DataFrame, calendar: list[date] | None) -> list[date]:
    if calendar is not None:
        return [as_date(day) for day in calendar]
    return sorted({as_date(day) for day in bars["date"]})


def _prev_closes(bars: pd.DataFrame) -> dict[tuple[str, date], float]:
    work = bars.sort_values(["lineage", "date"])
    found: dict[tuple[str, date], float] = {}
    last: dict[str, float] = {}
    for lineage, day, close in zip(work["lineage"], work["date"], work["px_close"], strict=False):
        key = str(lineage)
        stamp = as_date(day)
        if key in last:
            found[(key, stamp)] = last[key]
        last[key] = float(close)
    return found


def run_breakout(
    bars: pd.DataFrame,
    nifty_close: pd.Series,
    costs: DeliveryTable,
    cfg: EngineConfig | None = None,
    calendar: list[date] | None = None,
    signals: pd.DataFrame | None = None,
    flags: pd.DataFrame | None = None,
) -> dict:
    """Run the book. Returns trades, daily equity, and the size and year summaries.

    `signals`, when passed, replaces the breakout rule. Each row is a signal at
    the close of `date`. The fill is still the next session's open.
    """
    cfg = cfg or EngineConfig()
    book = research_prices(bars)
    book["date"] = book["date"].map(as_date)
    sessions = _calendar(book, calendar)
    if signals is None:
        signals = prepare_signals(book, nifty_close, cfg)
    if flags is None:
        flags = event_flags(book, cfg.event_move)
    return _run_book(book, signals, flags, sessions, costs, cfg)


def _run_book(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    flags: pd.DataFrame,
    calendar: list[date],
    costs: DeliveryTable,
    cfg: EngineConfig,
) -> dict:
    index = {day: i for i, day in enumerate(calendar)}
    nxt = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    blocked = blocked_entries(flags, calendar, cfg.entry_ban)
    flag_set = set()
    if flags is not None and not flags.empty:
        flag_set = {(str(lineage), as_date(day)) for lineage, day in zip(flags["lineage"], flags["date"], strict=False)}
    lineages = bars["lineage"].astype(str).to_numpy()
    days = [as_date(day) for day in bars["date"]]
    opens = bars["px_open"].to_numpy(dtype=float)
    highs = bars["px_high"].to_numpy(dtype=float)
    lows = bars["px_low"].to_numpy(dtype=float)
    closes = bars["px_close"].to_numpy(dtype=float)
    bars_at = {
        (lineages[i], days[i]): (opens[i], highs[i], lows[i], closes[i])
        for i in range(len(bars))
    }
    previous = _prev_closes(bars)
    pending: dict[date, list] = defaultdict(list)
    if not signals.empty:
        for signal in signals.itertuples(index=False):
            entry = nxt.get(as_date(signal.date))
            if entry is None or entry < cfg.start or entry > cfg.end:
                continue
            pending[entry].append(signal)

    slot = cfg.capital / Decimal(cfg.max_positions)
    cash = cfg.capital
    positions: dict[str, Position] = {}
    trades: list[dict] = []
    daily: list[dict] = []
    last_close: dict[str, float] = {}
    window = [day for day in calendar if cfg.start <= day <= cfg.end]
    if not window:
        return _summarize(trades, daily, cfg)

    def bar(lineage: str, day: date):
        return bars_at.get((lineage, day))

    def lock(lineage: str, day: date, row) -> bool:
        return _lower_lock(row[0], row[1], row[2], row[3], previous.get((lineage, day)))

    def sell(pos: Position, day: date, price: Decimal, reason: str) -> None:
        nonlocal cash
        slip = _slip_for(pos.size, cfg)
        fill = slipped("SELL", price, slip)
        charges = costs.leg("SELL", fill, pos.qty, day).total
        cash += fill * pos.qty - charges
        bought = pos.entry_fill * pos.qty + pos.entry_charges
        trades.append(
            {
                "lineage": pos.lineage,
                "symbol": pos.symbol,
                "size": pos.size,
                "score": pos.score,
                "qty": pos.qty,
                "signal_date": pos.signal_date.isoformat(),
                "entry_date": pos.entry_date.isoformat(),
                "exit_date": day.isoformat(),
                "entry_open": float(pos.entry_open),
                "entry_fill": float(pos.entry_fill),
                "exit_fill": float(fill),
                "stop": float(pos.stop),
                "reason": reason,
                "hold_sessions": index[day] - pos.entry_index + 1,
                "pnl": float(fill * pos.qty - charges - bought),
            }
        )
        del positions[pos.lineage]

    for today in window:
        exited: set[str] = set()
        # Morning: a position that is still on because yesterday's lock blocked
        # the guard, then stops that gap through the open.
        for lineage, pos in list(positions.items()):
            row = bar(lineage, today)
            if row is None or lock(lineage, today, row):
                continue
            open_ = Decimal(str(row[0]))
            if (lineage, today) in flag_set or open_ <= pos.stop:
                reason = "stop_gap" if open_ <= pos.stop else "event_open"
                sell(pos, today, open_, reason)
                exited.add(lineage)

        free = cfg.max_positions - len(positions)
        ordered = sorted(pending.get(today, []), key=lambda signal: (-float(signal.score), str(signal.lineage)))
        seen: set[str] = set()
        for signal in ordered:
            lineage = str(signal.lineage)
            if lineage in seen or lineage in positions:
                continue
            seen.add(lineage)
            if free <= 0 or (lineage, today) in blocked:
                continue
            row = bar(lineage, today)
            if row is None:
                continue
            open_ = Decimal(str(row[0]))
            slip = _slip_for(str(signal.size), cfg)
            fill = slipped("BUY", open_, slip)
            qty = _buy_qty(cash, fill, slot, costs, today)
            if qty < 1:
                continue
            charges = costs.leg("BUY", fill, qty, today).total
            cash -= fill * qty + charges
            stop = open_ - Decimal(str(cfg.atr_mult)) * Decimal(str(signal.atr))
            target = getattr(signal, "target", None)
            positions[lineage] = Position(
                lineage=lineage,
                symbol=str(signal.symbol),
                qty=qty,
                entry_date=today,
                entry_index=index[today],
                entry_open=open_,
                entry_fill=fill,
                entry_charges=charges,
                stop=stop,
                target=None if target is None or pd.isna(target) else Decimal(str(target)),
                size=str(signal.size),
                score=float(signal.score),
                signal_date=as_date(signal.date),
            )
            free -= 1

        for lineage, pos in list(positions.items()):
            if lineage in exited:
                continue
            row = bar(lineage, today)
            if row is None or lock(lineage, today, row):
                continue
            open_ = Decimal(str(row[0]))
            low = Decimal(str(row[2]))
            high = Decimal(str(row[1]))
            # A gap through the target sells at the open. When the open is
            # between the stop and the target and the day touches both, the
            # stop is the fill.
            if pos.target is not None and open_ >= pos.target:
                sell(pos, today, open_, "target_gap")
                exited.add(lineage)
                continue
            if low <= pos.stop:
                sell(pos, today, pos.stop, "stop")
                exited.add(lineage)
                continue
            if pos.target is not None and high >= pos.target:
                sell(pos, today, pos.target, "target")
                exited.add(lineage)

        for lineage, pos in list(positions.items()):
            if lineage in exited:
                continue
            row = bar(lineage, today)
            if row is None or lock(lineage, today, row):
                continue
            close = Decimal(str(row[3]))
            tomorrow = nxt.get(today)
            held = index[today] - pos.entry_index + 1
            if tomorrow is not None and (lineage, tomorrow) in flag_set:
                sell(pos, today, close, "event")
            elif held >= cfg.max_hold:
                sell(pos, today, close, "time")
            elif today == window[-1]:
                sell(pos, today, close, "sample_end")

        marked = Decimal(0)
        for lineage, pos in positions.items():
            row = bar(lineage, today)
            if row is not None:
                last_close[lineage] = float(row[3])
            price = last_close.get(lineage)
            if price is not None:
                marked += Decimal(str(price)) * pos.qty
        equity = cash + marked
        exposure = float(marked / equity) if equity else 0.0
        daily.append(
            {
                "date": today.isoformat(),
                "equity": float(equity),
                "cash": float(cash),
                "exposure": exposure,
                "positions": len(positions),
            }
        )

    return _summarize(trades, daily, cfg)


def _summarize(trades: list[dict], daily: list[dict], cfg: EngineConfig) -> dict:
    trade_frame = pd.DataFrame(trades)
    daily_frame = pd.DataFrame(daily)
    if trade_frame.empty:
        by_size = pd.DataFrame(columns=["size", "trades", "pnl"])
        by_year = pd.DataFrame(columns=["year", "trades", "pnl", "end_equity", "avg_exposure"])
    else:
        grouped = trade_frame.groupby("size", sort=False)["pnl"].agg(["count", "sum"]).reset_index()
        grouped.columns = ["size", "trades", "pnl"]
        by_size = grouped
        stamped = trade_frame.copy()
        stamped["year"] = stamped["exit_date"].str.slice(0, 4).astype(int)
        yearly = stamped.groupby("year")["pnl"].agg(["count", "sum"]).reset_index()
        yearly.columns = ["year", "trades", "pnl"]
        by_year = yearly
    if not daily_frame.empty:
        curve = daily_frame.copy()
        curve["year"] = curve["date"].str.slice(0, 4).astype(int)
        ends = curve.groupby("year").tail(1)[["year", "equity"]].rename(columns={"equity": "end_equity"})
        exposure = curve.groupby("year")["exposure"].mean().rename("avg_exposure")
        if by_year.empty:
            by_year = ends.merge(exposure, on="year")
            by_year["trades"] = 0
            by_year["pnl"] = 0.0
        else:
            by_year = by_year.merge(ends, on="year", how="outer").merge(exposure, on="year", how="left")
            by_year["trades"] = by_year["trades"].fillna(0).astype(int)
            by_year["pnl"] = by_year["pnl"].fillna(0.0)
    return {
        "trades": trade_frame,
        "daily": daily_frame,
        "by_size": by_size,
        "by_year": by_year.sort_values("year").reset_index(drop=True) if not by_year.empty else by_year,
        "config": {
            "capital": str(cfg.capital),
            "max_positions": cfg.max_positions,
            "max_hold": cfg.max_hold,
            "start": cfg.start.isoformat(),
            "end": cfg.end.isoformat(),
            "slip_liquid": str(cfg.slip_liquid),
            "slip_other": str(cfg.slip_other),
        },
    }
