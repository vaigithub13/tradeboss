"""What a strategy can see: the past only (critical for look-ahead safety).

The engine feeds `History` bar by bar and `IndicatorHub` value by value, so the objects behind
`ctx` never contain a bar or indicator value that has not closed yet - there is nothing to peek
at, and the guarded accessors raise `LookAheadError` (and record it, so a strategy that swallows
the exception still fails the run).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from app.backtest.contracts import LookAheadError
from app.data.candle import Candle
from app.indicators import registry


class _Growing:
    """Append-only float array: the visible part is exactly what was appended."""

    def __init__(self, dtype: Any = float) -> None:
        self._a = np.empty(1024, dtype=dtype)
        self._n = 0

    def append(self, v: float) -> None:
        if self._n == len(self._a):
            grown = np.empty(len(self._a) * 2, dtype=self._a.dtype)
            grown[: self._n] = self._a[: self._n]
            self._a = grown
        self._a[self._n] = v
        self._n += 1

    def view(self) -> np.ndarray:
        v = self._a[: self._n].view()
        v.flags.writeable = False
        return v

    def __len__(self) -> int:
        return self._n


class History:
    """The closed signal-timeframe bars so far."""

    def __init__(self) -> None:
        self.candles: list[Candle] = []
        self.times = _Growing(np.int64)
        self.cols = {k: _Growing() for k in ("open", "high", "low", "close", "volume")}
        self.violations: list[str] = []

    def append(self, bar: Candle) -> None:
        self.candles.append(bar)
        self.times.append(bar["time"])
        for k, g in self.cols.items():
            g.append(bar[k])  # type: ignore[literal-required]

    def violate(self, what: str) -> LookAheadError:
        self.violations.append(what)
        return LookAheadError(what)


class PastBars:
    """Bars up to and including the one that just closed. Index -1 is the current bar."""

    def __init__(self, history: History) -> None:
        self._h = history

    def __len__(self) -> int:
        return len(self._h.candles)

    def __getitem__(self, idx: int | slice) -> Any:
        n = len(self._h.candles)
        if isinstance(idx, slice):
            start, stop, step = idx.start, idx.stop, idx.step
            for name, v in (("start", start), ("stop", stop)):
                if v is not None and v > n:
                    raise self._h.violate(f"bars[{start}:{stop}] reaches past the {n} closed bars ({name}={v})")
            return [dict(c) for c in self._h.candles[slice(start, stop, step)]]
        i = idx + n if idx < 0 else idx
        if i >= n:
            raise self._h.violate(f"bars[{idx}] is in the future: only {n} bars have closed")
        if i < 0:
            raise IndexError(idx)
        return dict(self._h.candles[i])

    def at_time(self, t: int) -> Candle | None:
        """The bar that STARTS at unix time t, or None if there is none."""
        if self._h.candles and t > self._h.candles[-1]["time"]:
            raise self._h.violate(f"at_time({t}) is after the current bar ({self._h.candles[-1]['time']})")
        times = self._h.times.view()
        i = int(np.searchsorted(times, t))
        if i < len(times) and times[i] == t:
            return dict(self._h.candles[i])  # type: ignore[return-value]
        return None

    @property
    def times(self) -> np.ndarray:
        return self._h.times.view()

    @property
    def open(self) -> np.ndarray:
        return self._h.cols["open"].view()

    @property
    def high(self) -> np.ndarray:
        return self._h.cols["high"].view()

    @property
    def low(self) -> np.ndarray:
        return self._h.cols["low"].view()

    @property
    def close(self) -> np.ndarray:
        return self._h.cols["close"].view()

    @property
    def volume(self) -> np.ndarray:
        return self._h.cols["volume"].view()


class PastSeries:
    """One indicator output, up to the current bar. NaN while the indicator has not warmed up."""

    def __init__(self, history: History, buf: _Growing, label: str) -> None:
        self._h, self._b, self._label = history, buf, label

    def __len__(self) -> int:
        return len(self._b)

    def __getitem__(self, idx: int | slice) -> Any:
        n = len(self._b)
        if isinstance(idx, slice):
            for v in (idx.start, idx.stop):
                if v is not None and v > n:
                    raise self._h.violate(f"{self._label}[{idx.start}:{idx.stop}] reaches past the {n} closed bars")
            return self._b.view()[idx]
        i = idx + n if idx < 0 else idx
        if i >= n:
            raise self._h.violate(f"{self._label}[{idx}] is in the future: only {n} bars have closed")
        if i < 0:
            raise IndexError(idx)
        return float(self._b.view()[i])

    def array(self) -> np.ndarray:
        return self._b.view()


class IndicatorView:
    def __init__(self, history: History, outputs: dict[str, _Growing], label: str) -> None:
        self._h, self._o, self._label = history, outputs, label

    @property
    def names(self) -> list[str]:
        return list(self._o)

    def __getitem__(self, name: str) -> PastSeries:
        return PastSeries(self._h, self._o[name], f"{self._label}.{name}")

    def series(self, name: str) -> np.ndarray:
        return self._o[name].view()

    def last(self, name: str) -> float:
        v = self._o[name].view()
        return float(v[-1]) if len(v) else float("nan")

    def prev(self, name: str) -> float:
        v = self._o[name].view()
        return float(v[-2]) if len(v) > 1 else float("nan")


class IndicatorHub:
    """Computes each requested indicator with the chart's own code, hands out only the past."""

    def __init__(self, frame: Any, history: History) -> None:
        self._frame = frame  # the full frame stays inside the engine; strategies never see it
        self._h = history
        self._entries: dict[str, tuple[dict[str, np.ndarray], dict[str, _Growing]]] = {}

    def view(self, itype: str, params: dict[str, Any]) -> IndicatorView:
        clean = registry.validate_params(itype, params)
        key = itype + repr(sorted(clean.items()))
        if key not in self._entries:
            full = registry.compute(self._frame, itype, clean)
            bufs = {name: _Growing() for name in full}
            self._entries[key] = (full, bufs)
            for i in range(len(self._h.candles)):
                for name, g in bufs.items():
                    g.append(float(full[name][i]))
        return IndicatorView(self._h, self._entries[key][1], f"{itype}{clean}")

    def advance(self, i: int) -> None:
        """Bar i has just closed: reveal its value for every indicator already in use."""
        for full, bufs in self._entries.values():
            for name, g in bufs.items():
                g.append(float(full[name][i]))


@dataclass(frozen=True)
class PositionView:
    side: int  # +1 long, -1 short, 0 flat
    lots: int
    units: int
    avg_price: float

    @property
    def is_flat(self) -> bool:
        return self.lots == 0


# The engine Ctx and the worker context both expose exactly these names.
STRATEGY_CONTEXT_ATTRS = (
    "symbol",
    "timeframe",
    "base_minutes",
    "bars",
    "time",
    "position",
    "cash",
    "lot_size",
    "open_orders",
    "indicator",
    "cancel_working",
)
BARS_ATTRS = ("times", "open", "high", "low", "close", "volume", "at_time")
POSITION_ATTRS = ("side", "lots", "units", "avg_price", "is_flat")


class StrategyContext(Protocol):
    """What on_bar may read. The engine and the worker both provide every name.

    `timeframe` is the chart string ("5m", "15m"). Minutes come from
    timeframe_minutes(ctx.timeframe). `bars` is the closed bars only: len,
    an index or a slice, at_time, and the columns times, open, high, low,
    close, volume. `position` has side, lots, units, avg_price, and is_flat.
    """

    symbol: str
    timeframe: str
    base_minutes: int
    bars: Any
    time: int

    @property
    def position(self) -> PositionView: ...

    @property
    def cash(self) -> float: ...

    @property
    def lot_size(self) -> int: ...

    @property
    def open_orders(self) -> list[dict[str, Any]]: ...

    def indicator(self, itype: str, **params: Any) -> Any: ...

    def cancel_working(self, tag: str | None = None) -> int: ...


def missing_context_attributes(obj: Any) -> list[str]:
    """Names from the strategy context that this object does not provide."""
    missing = [name for name in STRATEGY_CONTEXT_ATTRS if not hasattr(obj, name)]
    bars = getattr(obj, "bars", None)
    if bars is not None:
        missing.extend(f"bars.{name}" for name in BARS_ATTRS if not hasattr(bars, name))
    position = getattr(obj, "position", None)
    if position is not None:
        missing.extend(f"position.{name}" for name in POSITION_ATTRS if not hasattr(position, name))
    return missing


class Ctx:
    """Passed to every strategy callback. Everything public here is about the past or the present."""

    def __init__(self, history: History, hub: IndicatorHub, broker: Any, *, symbol: str, timeframe: str,
                 base_minutes: int, lot_for_bar: Any, initial_capital: float) -> None:
        self._h = history
        self._hub = hub
        self._broker = broker
        self._lot_for_bar = lot_for_bar
        self._capital = initial_capital
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_minutes = base_minutes
        self.bars = PastBars(history)
        self.time = 0  # decision time: the END of the bar that just closed

    def _set_time(self, t: int) -> None:
        self.time = t

    def indicator(self, itype: str, **params: Any) -> IndicatorView:
        return self._hub.view(itype, params)

    @property
    def position(self) -> PositionView:
        return self._broker.position_view()

    @property
    def cash(self) -> float:
        return round(self._capital + self._broker.realized_net(), 2)

    @property
    def lot_size(self) -> int:
        return int(self._lot_for_bar())

    @property
    def open_orders(self) -> list[dict[str, Any]]:
        return self._broker.open_orders()

    def cancel_working(self, tag: str | None = None) -> int:
        """Cancel this strategy's working orders (all, or those with `tag`). Returns how many."""
        return int(self._broker.cancel_matching(tag, self.time))


__all__ = [
    "BARS_ATTRS",
    "Ctx",
    "History",
    "IndicatorHub",
    "IndicatorView",
    "LookAheadError",
    "POSITION_ATTRS",
    "PastBars",
    "PastSeries",
    "PositionView",
    "STRATEGY_CONTEXT_ATTRS",
    "StrategyContext",
    "missing_context_attributes",
]
