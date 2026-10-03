"""Isolated strategy process. Bars in, signals out. No API keys in the environment."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.context import PositionView
from app.backtest.contracts import LookAheadError, Signal, Strategy
from app.indicators.frame import candles_to_frame
from app.indicators.registry import compute, validate_params
from app.pine.sandbox import SandboxError, check_source

SECRET_ENV = (
    "OPENAI_API_KEY",
    "AI_API_KEY",
    "UPSTOX_ANALYTICS_TOKEN",
    "UPSTOX_API_KEY",
    "UPSTOX_API_SECRET",
)


def scrubbed_env(base: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    return {key: value for key, value in source.items() if key not in SECRET_ENV and not key.startswith("UPSTOX_")}


class WorkerBars:
    """Closed bars, with the same reads as the engine's PastBars."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append(self, bar: dict[str, Any]) -> None:
        self._rows.append(bar)

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, idx: int | slice) -> Any:
        n = len(self._rows)
        if isinstance(idx, slice):
            start, stop, step = idx.start, idx.stop, idx.step
            for name, value in (("start", start), ("stop", stop)):
                if value is not None and value > n:
                    raise LookAheadError(
                        f"bars[{start}:{stop}] reaches past the {n} closed bars ({name}={value})"
                    )
            return [dict(row) for row in self._rows[slice(start, stop, step)]]
        i = idx + n if idx < 0 else idx
        if i >= n:
            raise LookAheadError(f"bars[{idx}] is in the future: only {n} bars have closed")
        if i < 0:
            raise IndexError(idx)
        return dict(self._rows[i])

    def at_time(self, t: int) -> dict[str, Any] | None:
        if self._rows and t > self._rows[-1]["time"]:
            raise LookAheadError(f"at_time({t}) is after the current bar ({self._rows[-1]['time']})")
        for row in self._rows:
            if int(row["time"]) == t:
                return dict(row)
        return None

    def _col(self, name: str) -> np.ndarray:
        values = np.array([float(row.get(name) or 0.0) for row in self._rows], dtype=float)
        values.flags.writeable = False
        return values

    @property
    def times(self) -> np.ndarray:
        values = np.array([int(row["time"]) for row in self._rows], dtype=np.int64)
        values.flags.writeable = False
        return values

    @property
    def open(self) -> np.ndarray:
        return self._col("open")

    @property
    def high(self) -> np.ndarray:
        return self._col("high")

    @property
    def low(self) -> np.ndarray:
        return self._col("low")

    @property
    def close(self) -> np.ndarray:
        return self._col("close")

    @property
    def volume(self) -> np.ndarray:
        return self._col("volume")


class _Indicator:
    def __init__(self, series: dict[str, np.ndarray]) -> None:
        self._series = series

    def __getitem__(self, name: str) -> np.ndarray:
        values = self._series[name]
        values.flags.writeable = False
        return values


class WorkerContext:
    """The worker's strategy context. Same attributes as the engine Ctx."""

    def __init__(
        self,
        bars: WorkerBars,
        position: PositionView,
        *,
        symbol: str,
        timeframe: str,
        base_minutes: int,
        time: int,
        cash: float,
        lot_size: int,
        open_orders: list[dict[str, Any]],
    ) -> None:
        self.bars = bars
        self.position = position
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_minutes = int(base_minutes)
        self.time = int(time)
        self._cash = float(cash)
        self._lot_size = int(lot_size)
        self._open_orders = list(open_orders)
        self.cancelled: list[str | None] = []

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def lot_size(self) -> int:
        return self._lot_size

    @property
    def open_orders(self) -> list[dict[str, Any]]:
        return list(self._open_orders)

    def indicator(self, itype: str, **params: Any) -> _Indicator:
        clean = validate_params(itype, params)
        frame = candles_to_frame(self.bars._rows)
        return _Indicator(compute(frame, itype, clean))

    def cancel_working(self, tag: str | None = None) -> int:
        self.cancelled.append(tag)
        return 0


def make_worker_context(
    rows: list[dict[str, Any]],
    *,
    symbol: str = "NIFTY50",
    timeframe: str = "5m",
    base_minutes: int = 5,
    time: int = 0,
    cash: float = 0.0,
    lot_size: int = 1,
    open_orders: list[dict[str, Any]] | None = None,
    position: PositionView | None = None,
) -> WorkerContext:
    bars = WorkerBars()
    for row in rows:
        bars.append(row)
    return WorkerContext(
        bars,
        position or PositionView(0, 0, 0, 0.0),
        symbol=symbol,
        timeframe=timeframe,
        base_minutes=base_minutes,
        time=time,
        cash=cash,
        lot_size=lot_size,
        open_orders=open_orders or [],
    )


def _load(path: Path, params: dict[str, Any]) -> Any:
    source = path.read_text()
    check_source(source)
    spec = importlib.util.spec_from_file_location(f"user_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise SandboxError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    classes = [
        value for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, Strategy) and value.__module__ == module.__name__
    ]
    if not classes:
        raise SandboxError("no Strategy subclass in the module")
    return classes[0](**params)


def _signal(value: Signal) -> dict[str, Any]:
    return {
        "side": value.side, "qty": value.qty, "type": value.type, "price": value.price, "tag": value.tag,
        "stop": value.stop, "target": value.target, "oco": value.oco,
        "stop_points": value.stop_points, "target_points": value.target_points,
    }


def main() -> None:
    reply_to = sys.stdout
    sys.stdout = sys.stderr
    secrets = [key for key in SECRET_ENV if key in os.environ]

    def send(payload: dict[str, Any]) -> None:
        reply_to.write(json.dumps(payload) + "\n")
        reply_to.flush()

    send({"op": "ready", "secrets": secrets})
    strategy = None
    bars = WorkerBars()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            op = message.get("op")
            if op == "load":
                strategy = _load(Path(message["path"]), dict(message.get("params") or {}))
                send({"op": "loaded"})
            elif op == "bar":
                if strategy is None:
                    raise SandboxError("strategy is not loaded")
                fields = message.get("context")
                if not isinstance(fields, dict):
                    raise SandboxError("bar is missing the strategy context")
                bar = dict(message["bar"])
                bars.append(bar)
                held = dict(message.get("position") or {})
                ctx = WorkerContext(
                    bars,
                    PositionView(
                        int(held.get("side", 0)),
                        int(held.get("lots", 0)),
                        int(held.get("units", 0)),
                        float(held.get("avg_price", 0)),
                    ),
                    symbol=str(fields.get("symbol") or ""),
                    timeframe=str(fields["timeframe"]),
                    base_minutes=int(fields["base_minutes"]),
                    time=int(fields["time"]),
                    cash=float(fields.get("cash") or 0),
                    lot_size=int(fields.get("lot_size") or 1),
                    open_orders=list(fields.get("open_orders") or []),
                )
                raw = strategy.on_bar(bar, ctx) or []
                send({"op": "signals", "signals": [_signal(item) for item in raw], "cancel": ctx.cancelled})
            elif op == "stop":
                send({"op": "stopped"})
                return
            else:
                send({"op": "error", "error": "bad-op", "message": str(op)})
        except LookAheadError as exc:
            send({"op": "error", "error": "LookAheadError", "message": str(exc)})
        except Exception as exc:  # the parent turns this into a failed check
            send({"op": "error", "error": type(exc).__name__, "message": str(exc), "trace": traceback.format_exc()})


if __name__ == "__main__":
    main()
