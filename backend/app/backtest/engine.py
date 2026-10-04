"""The backtest engine: bar by bar, the strategy sees only closed bars (critical module).

Per signal-timeframe bar i:
  1. execution - orders placed at earlier decisions are matched against the 1m (base) bars inside
     bar i, in time order (intrabar ordering, gaps, brackets, square-off, session end);
  2. the bar closes: it is appended to the strategy's history and indicators, `on_bar` is called;
  3. its signals become orders that may fill from the NEXT bar (or, only if asked for and then
     flagged optimistic, at this bar's close).

Candles and indicators are the chart's own code (`CandleStore`, `resample`, `registry.compute`).
Indicators are computed once over the series and revealed one value per bar; tests/test_bt_lookahead.py
proves that this equals computing on the past only, and that replacing the future changes nothing.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import numpy as np

from app.backtest.broker import BacktestBroker
from app.backtest.context import Ctx, History, IndicatorHub
from app.backtest.contracts import LookAheadError, Signal, Strategy

ZERO_TRADES_WARNING = "this run produced 0 trades"
from app.backtest.costs import CostModel, Slippage
from app.backtest.lots import LotSizeTable
from app.backtest.metrics import TradeRecord, compute_metrics
from app.backtest.result import BacktestResult, Trade, canonical, digest
from app.backtest.sources import CandleSource, Loaded, ist_date
from app.data.resampler import DAY_S, IST_OFFSET_S, TIMEFRAMES, resample
from app.data.service import TimeframeUnavailable
from app.data.sessions import DEFAULT_INCLUDED_SESSION_TYPES, SESSION_TYPES
from app.indicators.frame import candles_to_frame

INTRADAY_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
OPEN_MIN, CLOSE_MIN = 9 * 60 + 15, 15 * 60 + 30
DEFAULT_SQUARE_OFF = "15:15"
FILL_MODES = ("next_open", "same_bar_close")
Sub = tuple[int, float, float, float, float]  # time, open, high, low, close of one base bar


class ConfigError(ValueError):
    pass


def _tod(t: int) -> int:
    """Minute of the IST day."""
    return ((t + IST_OFFSET_S) % DAY_S) // 60


def _parse_date(name: str, value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name}: {value!r} is not a YYYY-MM-DD date") from exc


@dataclass(frozen=True)
class BacktestConfig:
    timeframe: str = "5m"
    start: str | None = None  # first IST date traded (earlier bars only warm the indicators up)
    end: str | None = None  # last IST date traded
    session_types: tuple[str, ...] = DEFAULT_INCLUDED_SESSION_TYPES
    fill_mode: str = "next_open"  # or "same_bar_close" (optimistic, flagged in the result)
    square_off: str | None = "default"  # None, "HH:MM" or "default" (15:15)
    warmup_bars: int = 500
    lot_size: int | None = None  # fixed lot size; None -> lot_table + underlying (dated)
    lot_table: LotSizeTable | None = None
    underlying: str | None = None
    contract: Callable[[date], tuple[date, str]] | None = None  # trade date -> (expiry, "weekly"|"monthly")
    slippage: Slippage = field(default_factory=Slippage.none)
    cost_model: CostModel = field(default_factory=CostModel.zero)
    initial_capital: float = 100_000.0

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise ConfigError(f"timeframe {self.timeframe!r}: expected one of {list(TIMEFRAMES)}")
        if self.fill_mode not in FILL_MODES:
            raise ConfigError(f"fill_mode {self.fill_mode!r}: expected one of {list(FILL_MODES)}")
        self.square_off_minute()
        bad = sorted(set(self.session_types) - set(SESSION_TYPES))
        if bad:
            raise ConfigError(f"unknown session type(s) {bad}")
        if not isinstance(self.warmup_bars, int) or self.warmup_bars < 0:
            raise ConfigError("warmup_bars must be a whole number >= 0")
        if self.lot_size is not None and (isinstance(self.lot_size, bool) or not isinstance(self.lot_size, int) or self.lot_size < 1):
            raise ConfigError("lot_size must be a positive whole number")
        _parse_date("start", self.start)
        _parse_date("end", self.end)

    def square_off_minute(self) -> int | None:
        v = self.square_off
        if v is None:
            return None
        if v == "default":
            v = DEFAULT_SQUARE_OFF
        m = re.fullmatch(r"(\d{2}):(\d{2})", str(v))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ConfigError(f"square_off {self.square_off!r}: use HH:MM, 'default' or None")
        return int(m.group(1)) * 60 + int(m.group(2))

    def to_dict(self) -> dict[str, Any]:
        sq = self.square_off_minute()
        return {
            "timeframe": self.timeframe, "start": self.start, "end": self.end,
            "session_types": sorted(self.session_types), "fill_mode": self.fill_mode,
            "square_off": None if sq is None else f"{sq // 60:02d}:{sq % 60:02d}",
            "warmup_bars": self.warmup_bars, "lot_size": self.lot_size, "underlying": self.underlying,
            "lot_table": None if self.lot_table is None or self.lot_size is not None else self.lot_table.to_dict(),
            "contract": None if self.contract is None else "callable",
            "slippage": self.slippage.to_dict(), "cost_model": self.cost_model.to_dict(),
            "initial_capital": self.initial_capital,
        }


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


@dataclass
class _Series:
    bars: list[Any]  # signal-timeframe candles (resampled), the strategy's history
    subs: list[list[Sub]]  # base bars inside each bar, as used for matching
    ends: list[int]  # decision time of each bar (its end)
    lasts: list[int]  # start time of the last base bar inside each bar
    first: int  # index of the first tradable bar
    coarse_bars: int


def _build_series(cfg: BacktestConfig, source: CandleSource) -> tuple[_Series, int, list[str]]:
    start, end = _parse_date("start", cfg.start), _parse_date("end", cfg.end)
    tf = cfg.timeframe
    tf_min = INTRADAY_MIN.get(tf)
    from_time = to_time = None
    if start is not None:
        if tf_min is not None:
            margin = -(-cfg.warmup_bars * tf_min // 300) + 6
        else:
            margin = cfg.warmup_bars * (7 if tf == "1W" else 2) + 14
        from_time = _ist_midnight(start) - margin * DAY_S
    if end is not None:
        to_time = _ist_midnight(end + timedelta(days=1)) - 1
    loaded = source.load(from_time, to_time, cfg.session_types)
    base = loaded.base_minutes
    if tf_min is not None and tf_min % base != 0:
        raise TimeframeUnavailable(f"{tf} is not available: stored data is {base}m (candles are never fabricated)")
    base_s = base * 60
    warnings: list[str] = []
    candles = loaded.candles
    bars = resample(candles, tf, base, anchor_to_first_bar_dates=loaded.anchored)
    anchored_days = {d.toordinal() for d in loaded.anchored}
    starts = [b["time"] for b in bars]
    tf_s = (tf_min or 0) * 60
    subs: list[list[Sub]] = [[] for _ in bars]
    for c in candles:
        t = c["time"]
        idx = bisect_right(starts, t) - 1
        if idx < 0:
            continue
        d = ist_date(t)
        if d.toordinal() not in anchored_days and not (OPEN_MIN <= _tod(t) < CLOSE_MIN):
            continue
        if tf_min is not None:
            if t >= starts[idx] + tf_s:
                continue
        elif tf == "1D":
            if ist_date(starts[idx]) != d:
                continue
        else:  # 1W
            a = ist_date(starts[idx])
            if (a.isocalendar()[:2]) != (d.isocalendar()[:2]):
                continue
        subs[idx].append((t, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])))

    # tradable range
    dates = [ist_date(b["time"]) for b in bars]
    idx_ok = [i for i, d in enumerate(dates) if (start is None or d >= start) and (end is None or d <= end)]
    if not idx_ok:
        return _Series([], [], [], [], 0, 0), base, ["no bars in the requested range"]
    first, last = idx_ok[0], idx_ok[-1]
    lo = max(0, first - cfg.warmup_bars)
    hi = last + 1
    coarse = 0
    out_subs: list[list[Sub]] = []
    ends: list[int] = []
    lasts: list[int] = []
    for i in range(lo, hi):
        b = bars[i]
        s = subs[i]
        need = (tf_min // base) if tf_min is not None else None
        if not s:
            s = [(b["time"], float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]))]
            coarse += 1
        elif need is not None and len(s) != need:
            s = [(s[0][0], float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]))]
            coarse += 1
        last_base = subs[i][-1][0] if subs[i] else b["time"]
        out_subs.append(s)
        lasts.append(last_base)
        ends.append(b["time"] + tf_s if tf_min is not None else last_base + base_s)
    return _Series(bars[lo:hi], out_subs, ends, lasts, first - lo, coarse), base, warnings


def _note_vwap_fallback(warnings: list[str], store: Any, symbol: str, cfg: BacktestConfig, series: _Series) -> None:
    """Count signal bars whose futures VWAP used the other contract, and say so on the result."""
    tf_min = INTRADAY_MIN.get(cfg.timeframe)
    if store is None or symbol != "NIFTY50" or tf_min is None or not series.bars:
        return
    from app.data.service import TimeframeUnavailable
    from app.indicators.futures_vwap import values_for_chart
    from app.indicators.registry import validate_params

    try:
        _values, flags = values_for_chart(
            store, series.bars, validate_params("vwap_fut", {}), cfg.session_types, tf_min * 60,
            to_time=None, cursor=None,
        )
    except TimeframeUnavailable:
        return
    count = int(np.sum(flags))
    if count:
        warnings.append(f"VWAP (futures volume): fallback {count}")


def _ist_midnight(d: date) -> int:
    return (d.toordinal() - date(1970, 1, 1).toordinal()) * DAY_S - IST_OFFSET_S


def _data_digest(series: _Series) -> str:
    h = hashlib.sha256()
    if series.bars:
        h.update(np.array([[b["time"], b["open"], b["high"], b["low"], b["close"], b["volume"]] for b in series.bars],
                          dtype=np.float64).tobytes())
        h.update(np.array([s for subs in series.subs for s in subs], dtype=np.float64).tobytes())
    return h.hexdigest()


def run_backtest(
    strategy: Strategy,
    source: CandleSource,
    config: BacktestConfig | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    replay_cursor: int | None = None,
) -> BacktestResult:
    """`replay_cursor` drops source minutes after that time and does not flatten a position
    merely because the clipped series ends mid-session."""
    cfg = config or BacktestConfig()
    candle_store = getattr(source, "store", None)
    candle_symbol = getattr(source, "symbol", "")
    data_continues = False
    if replay_cursor is not None:
        cursor = replay_cursor
        inner = source

        class _Clip:
            symbol = getattr(inner, "symbol", "")

            def load(self, from_time: int | None, to_time: int | None, session_types: Iterable[str]) -> Loaded:
                nonlocal data_continues
                loaded = inner.load(from_time, to_time, session_types)
                kept = [c for c in loaded.candles if c["time"] <= cursor]
                data_continues = any(c["time"] > cursor for c in loaded.candles)
                return Loaded(kept, loaded.base_minutes, loaded.anchored)

        source = _Clip()
    tf = cfg.timeframe
    intraday = tf in INTRADAY_MIN
    overnight = bool(getattr(strategy, "allow_overnight", False))
    pine_path = getattr(strategy, "bar_path", None) == "pine_ohlc"
    if not intraday and not overnight:
        raise ConfigError(f"{tf} bars hold positions across sessions: the strategy must set allow_overnight = True")
    if cfg.lot_size is None and (cfg.lot_table is None or not cfg.underlying):
        raise ConfigError("give a lot_size, or a lot_table and an underlying (lot sizes are dated, never assumed)")

    series, base, warnings = _build_series(cfg, source)
    _note_vwap_fallback(warnings, candle_store, candle_symbol, cfg, series)
    base_s = base * 60
    sq = cfg.square_off_minute()
    sq_active = sq is not None and intraday
    if sq is not None and overnight:
        sq_active = False
        warnings.append("square_off ignored: this strategy is allowed to carry positions overnight")
    if base > 1 and not pine_path:
        warnings.append(
            f"source bars are {base}m: orders inside a bar are resolved at {base}m resolution; "
            f"when a stop and a target both fall in one {base}m bar the stop is assumed first (ambiguous)"
        )
    if series.coarse_bars:
        warnings.append(
            f"coarse intrabar data: {series.coarse_bars} bar(s) had missing base bars, whole-bar high/low used "
            f"(stop assumed first when both are touched)"
        )
    if cfg.fill_mode == "same_bar_close":
        warnings.append("fill_mode=same_bar_close fills at the signal bar's close: optimistic, not achievable live")

    events: list[dict[str, Any]] = []
    counters = {k: 0 for k in ("bars", "signals", "orders", "fills", "rejected", "cancelled", "unfilled", "gaps",
                               "ambiguous", "end_of_data_exits")}

    def lot_on(day: date) -> int:
        if cfg.lot_size is not None:
            return cfg.lot_size
        assert cfg.lot_table is not None and cfg.underlying
        expiry, cycle = cfg.contract(day) if cfg.contract is not None else (None, None)
        return cfg.lot_table.lot_size(cfg.underlying, day, expiry=expiry, cycle=cycle)

    broker = BacktestBroker(cost_model=cfg.cost_model, slippage=cfg.slippage, lot_resolver=lot_on, events=events,
                            counters=counters)
    broker.pyramiding = getattr(strategy, "pyramiding", None)
    history = History()
    frame = candles_to_frame(series.bars)
    hub = IndicatorHub(frame, history)
    cur_day: list[date] = [date(1970, 1, 1)]
    ctx = Ctx(history, hub, broker, symbol=getattr(source, "symbol", ""), timeframe=tf, base_minutes=base,
              lot_for_bar=lambda: lot_on(cur_day[0]), initial_capital=cfg.initial_capital)

    bars, subs, ends, lasts, first = series.bars, series.subs, series.ends, series.lasts, series.first
    n = len(bars)
    dates = [ist_date(b["time"]) for b in bars]
    anchored = {ist_date(b["time"]) for b in bars if not (OPEN_MIN <= _tod(b["time"]) < CLOSE_MIN)}
    sq_done: set[date] = set()

    def session_complete(k: int) -> bool:
        if dates[k] in anchored:
            return True
        return _tod(lasts[k]) + base >= CLOSE_MIN

    for k in range(n):
        bar = bars[k]
        cur_day[0] = dates[k]
        if k < first:
            history.append(bar)
            hub.advance(k)
            continue
        if k == first:
            strategy.on_start(ctx)
        counters["bars"] += 1
        if on_progress is not None and (counters["bars"] == 1 or counters["bars"] % 50 == 0):
            on_progress(counters["bars"], max(n - first, 1))

        # 1. execution of the orders waiting from earlier decisions
        if pine_path:
            broker.on_pine_path(int(bar["time"]), float(bar["open"]), float(bar["high"]), float(bar["low"]),
                                float(bar["close"]))
        else:
            for ts, o, h, lo_, c in subs[k]:
                d = dates[k]
                if sq_active and d not in anchored and d not in sq_done and _tod(ts) >= sq:  # type: ignore[operator]
                    sq_done.add(d)
                    broker.force_exit(ts, o, "square_off", "square_off")
                if d in sq_done:
                    continue
                broker.on_sub_bar(ts, o, h, lo_, base_s)
        final = k == n - 1 and not data_continues
        day_ends = k + 1 < n and dates[k + 1] != dates[k]
        clipped_session_done = k == n - 1 and data_continues and session_complete(k)
        if day_ends or final or clipped_session_done:
            last_ts, last_c = lasts[k], subs[k][-1][4]
            if not overnight:
                reason = "end_of_data" if final and not session_complete(k) else "session_end"
                broker.force_exit(last_ts, last_c, reason, reason)
            elif final and getattr(strategy, "flatten_at_end", True):
                broker.force_exit(last_ts, last_c, "end_of_data", "end_of_data")

        # 2. the bar has closed: the strategy may look at it
        history.append(bar)
        hub.advance(k)
        end_t = ends[k]
        ctx._set_time(end_t)
        signals = strategy.on_bar(dict(bar), ctx) or []

        # 3. its signals
        nxt_first = subs[k + 1][0][0] if k + 1 < n else None
        for sig in signals:
            if not isinstance(sig, Signal):
                raise TypeError(f"on_bar must return Signal objects, got {sig!r}")
            counters["signals"] += 1
            events.append({"kind": "signal", "t": int(end_t), "side": sig.side, "type": sig.type, "lots": sig.qty,
                           "price": sig.price, "tag": sig.tag})
            immediate = cfg.fill_mode == "same_bar_close" and sig.type == "MARKET"
            block: tuple[str, bool] | None = None
            if immediate:
                if sq_active and _tod(end_t) >= sq:  # type: ignore[operator]
                    block = ("after_square_off", False)
            elif nxt_first is None or (intraday and dates[k + 1] != dates[k] and not overnight):
                block = ("no_next_bar", True)
            elif sq_active and dates[k + 1] == dates[k] and _tod(nxt_first) >= sq:  # type: ignore[operator]
                block = ("after_square_off", False)
            oid = broker.place(sig, t=end_t, ref_price=float(bar["close"]), min_t=end_t, block=block)
            if immediate and oid is not None:
                broker.fill_now(oid, float(bar["close"]), lasts[k], end_t)

    if on_progress is not None and counters["bars"]:
        on_progress(counters["bars"], max(n - first, 1))
    if first < n:
        strategy.on_stop(ctx)
    if history.violations:
        raise LookAheadError("; ".join(history.violations[:3]) + (" ..." if len(history.violations) > 3 else ""))

    trades = broker.trades
    metrics = compute_metrics([TradeRecord(t.entry_time, t.exit_time, t.net_pnl) for t in trades])
    days = {ist_date(t.entry_time) for t in trades} | {ist_date(t.exit_time) for t in trades}
    warnings.extend(cfg.cost_model.unverified_warnings(days))
    warnings.extend(str(note) for note in getattr(strategy, "settings_warnings", ()))
    if not trades:
        warnings.append(ZERO_TRADES_WARNING)
    metrics["gross_pnl"] = float(sum((Decimal(str(t.gross_pnl)) for t in trades), Decimal("0")))
    metrics["total_charges"] = float(sum((Decimal(str(t.charges_total)) for t in trades), Decimal("0")))
    metrics["total_slippage"] = float(sum((Decimal(str(t.slippage_cost)) for t in trades), Decimal("0")))
    metrics["ambiguous_trades"] = sum(1 for t in trades if t.ambiguous)
    metrics["gap_trades"] = sum(1 for t in trades if t.gap)

    config_dict = cfg.to_dict()
    strat_dict = {"class": type(strategy).__name__, "name": strategy.name, "params": _plain(strategy.params),
                  "allow_overnight": overnight}
    data = {
        "symbol": getattr(source, "symbol", ""), "base_minutes": base, "bars": len(bars), "tradable_bars": counters["bars"],
        "first_time": bars[0]["time"] if bars else None, "last_time": bars[-1]["time"] if bars else None,
        "sha256": _data_digest(series),
    }
    optimistic = cfg.fill_mode == "same_bar_close"
    run_id = digest({"config": config_dict, "strategy": strat_dict, "data": data})
    return BacktestResult(trades=trades, events=events, metrics=metrics, counters=counters, warnings=warnings,
                          optimistic=optimistic, run_id=run_id, config=config_dict, strategy=strat_dict, data=data)


__all__ = ["BacktestConfig", "ConfigError", "run_backtest", "canonical", "Trade"]
