"""Isolated strategy process. Bars in, signals out.

Layers, outermost first (C2, review 2026-10-11):
* app/pine/isolated.py starts this process under a macOS sandbox-exec profile (no network, no new process,
  no file reads or writes outside its temp dir and the read-only runtime) with an allow-listed environment;
* `main` refuses every `app.*` import that was not loaded at start-up, so app.config (which reads .env) cannot
  be imported here, and checks that on start;
* the strategy module runs with restricted builtins and an import hook that allows only the strategy contract,
  pine_common, typing, __future__ and math (`exec_strategy_module`);
* the AST gate (app/pine/sandbox.py) runs on the source before any of it executes.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.abc
import json
import math
import os
import sys
import traceback
import types
from pathlib import Path
from typing import Any

import numpy as np

import app.strategies.pine_common  # noqa: F401  (loaded before the import guard is installed)
from app.backtest.context import PositionView
from app.backtest.contracts import LookAheadError, Signal, Strategy
from app.indicators.frame import candles_to_frame
from app.indicators.registry import compute, validate_params
from app.pine.sandbox import ALLOWED, TYPING_NAMES, SandboxError, check_source

#: Builtins a strategy module sees. No open, getattr, eval, exec, compile, __import__ (replaced by the hook),
#: globals, vars, type, input or breakpoint.
SAFE_BUILTIN_NAMES = (
    "__build_class__", "abs", "all", "any", "bool", "callable", "classmethod", "dict", "divmod", "enumerate",
    "filter", "float", "frozenset", "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min",
    "next", "object", "pow", "print", "property", "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "staticmethod", "str", "sum", "super", "tuple", "zip",
    "ArithmeticError", "AssertionError", "AttributeError", "Exception", "IndexError", "KeyError", "LookupError",
    "NotImplementedError", "OverflowError", "RuntimeError", "StopIteration", "TypeError", "ValueError",
    "ZeroDivisionError", "NotImplemented", "Ellipsis", "None", "True", "False",
)
#: What a strategy module may import: module -> the names it may take (None: the whole module).
STRATEGY_IMPORTS: dict[str, tuple[str, ...] | None] = {
    "__future__": ("annotations",),
    "typing": TYPING_NAMES,
    "math": None,
    **ALLOWED,
}

# Settings the engine reads off the strategy object. A missing one uses the safe value.
STRATEGY_SETTINGS = ("name", "pyramiding", "lots", "allow_overnight", "flatten_at_end", "bar_path")
SAFE_SETTINGS = {
    "name": "user",
    "pyramiding": 0,
    "lots": 1,
    "allow_overnight": False,
    "flatten_at_end": True,
    "bar_path": "1m",
}

SECRET_ENV = (
    "OPENAI_API_KEY",
    "AI_API_KEY",
    "UPSTOX_ANALYTICS_TOKEN",
    "UPSTOX_API_KEY",
    "UPSTOX_API_SECRET",
)


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


def strategy_settings(strategy: Any) -> tuple[dict[str, Any], list[str]]:
    """Values the loaded strategy defines, and the names it leaves unset.

    Class attributes on Strategy itself are not treated as a choice the strategy made.
    """
    present: dict[str, Any] = {}
    for cls in type(strategy).__mro__:
        if cls is Strategy or cls is object:
            break
        for key in STRATEGY_SETTINGS:
            if key in cls.__dict__ and key not in present:
                present[key] = cls.__dict__[key]
    for key in STRATEGY_SETTINGS:
        if key in vars(strategy):
            present[key] = vars(strategy)[key]
    missing = [key for key in STRATEGY_SETTINGS if key not in present]
    values = dict(SAFE_SETTINGS)
    values.update(present)
    return values, missing


def _strategy_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    """`__import__` for strategy code: only STRATEGY_IMPORTS, and only the listed names of each module.

    C code that imports while a strategy frame is running (numpy's lazy imports, through PyImport_Import) finds
    this hook in the strategy's builtins. That call always passes an empty list as `fromlist` and ignores the
    return value, so it imports the module for its side effect and gets None: nothing is handed back. `app.*`
    is refused there too.
    """
    if isinstance(fromlist, list) and not fromlist and not level:
        if name == "app" or name.startswith("app."):
            raise ImportError(f"{name} is not importable in a strategy")
        importlib.import_module(name)
        return None
    if level:
        raise ImportError("relative imports are not allowed in a strategy")
    if name not in STRATEGY_IMPORTS:
        raise ImportError(f"{name} is not importable in a strategy")
    allowed = STRATEGY_IMPORTS[name]
    real = math if name == "math" else importlib.import_module(name)
    if allowed is None:
        return real
    wanted = tuple(fromlist or ())
    if not wanted:
        raise ImportError(f"use 'from {name} import <name>'")
    refused = [item for item in wanted if item not in allowed]
    if refused:
        raise ImportError(f"{name}.{refused[0]} is not importable in a strategy")
    view = types.ModuleType(name)
    for item in wanted:
        setattr(view, item, getattr(real, item))
    return view


def safe_builtins() -> dict[str, Any]:
    out = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}
    out["__import__"] = _strategy_import
    return out


def exec_strategy_module(source: str, *, filename: str, module_name: str) -> types.ModuleType:
    """Run strategy source as a module with restricted builtins and the import hook."""
    code = compile(source, filename, "exec", dont_inherit=True)
    module = types.ModuleType(module_name)
    module.__dict__["__builtins__"] = safe_builtins()
    module.__dict__["__file__"] = filename
    exec(code, module.__dict__)  # noqa: S102 - the point of this function; see the module docstring
    return module


class _AppImportGuard(importlib.abc.MetaPathFinder):
    """After start-up no new `app.*` module can be imported in the worker; app.config never."""

    def __init__(self, loaded: set[str]) -> None:
        self.loaded = loaded

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        if fullname == "app.config" or (fullname.startswith("app.") and fullname not in self.loaded):
            raise ImportError(f"{fullname} cannot be imported in the strategy worker")
        return None


def install_import_guard() -> dict[str, bool]:
    """Block new app imports, then check that app.config is neither loaded nor importable."""
    sys.modules.pop("app.config", None)
    loaded = {name for name in sys.modules if name == "app" or name.startswith("app.")}
    if not any(isinstance(f, _AppImportGuard) for f in sys.meta_path):
        sys.meta_path.insert(0, _AppImportGuard(loaded))
    try:
        importlib.import_module("app.config")
        importable = True
    except ImportError:
        importable = False
    return {"config_loaded": "app.config" in sys.modules, "config_importable": importable}


def _reads_denied() -> bool:
    """True when the sandbox is on: a harmless file outside the worker's temp dir cannot be opened."""
    probe = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        with open(probe, "rb") as handle:
            handle.read(1)
    except PermissionError:
        return True
    except OSError:
        return False
    return False


def _load(path: Path, params: dict[str, Any]) -> Any:
    source = path.read_text()
    check_source(source)
    module = exec_strategy_module(source, filename=str(path), module_name=f"user_{path.stem}")
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
    guard = install_import_guard()

    def send(payload: dict[str, Any]) -> None:
        reply_to.write(json.dumps(payload) + "\n")
        reply_to.flush()

    send({"op": "ready", "secrets": secrets, **guard, "sandboxed": _reads_denied()})
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
                values, missing = strategy_settings(strategy)
                send({"op": "loaded", "settings": values, "missing": missing})
            elif op == "seed":
                if strategy is None:
                    raise SandboxError("strategy is not loaded")
                for row in message.get("bars") or []:
                    bars.append(dict(row))
                fields = message.get("context")
                if not isinstance(fields, dict):
                    raise SandboxError("seed is missing the strategy context")
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
                strategy.on_start(ctx)
                send({"op": "seeded", "bars": len(bars)})
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
