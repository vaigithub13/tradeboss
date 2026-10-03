"""Isolated strategy process. Bars in, signals out. No API keys in the environment."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from app.backtest.contracts import LookAheadError, Signal, Strategy
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


class _Bars:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append(self, bar: dict[str, Any]) -> None:
        self._rows.append(bar)

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        n = len(self._rows)
        i = idx + n if isinstance(idx, int) and idx < 0 else idx
        if not isinstance(i, int) or i < 0 or i >= n:
            raise LookAheadError(f"bars[{idx}] is in the future: only {n} bars have closed")
        return self._rows[i]

    def _col(self, name: str) -> list[float]:
        return [float(row[name]) for row in self._rows]

    @property
    def open(self) -> list[float]:
        return self._col("open")

    @property
    def high(self) -> list[float]:
        return self._col("high")

    @property
    def low(self) -> list[float]:
        return self._col("low")

    @property
    def close(self) -> list[float]:
        return self._col("close")


class _Position:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.side = int(payload.get("side", 0))
        self.lots = int(payload.get("lots", 0))
        self.units = int(payload.get("units", 0))
        self.avg_price = float(payload.get("avg_price", 0))

    @property
    def is_flat(self) -> bool:
        return self.lots == 0


class _Ctx:
    def __init__(self, bars: _Bars, position: _Position) -> None:
        self.bars = bars
        self.position = position
        self.cancelled: list[str | None] = []

    def cancel_working(self, tag: str | None = None) -> int:
        self.cancelled.append(tag)
        return 0


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
    bars = _Bars()
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
                bar = dict(message["bar"])
                bars.append(bar)
                ctx = _Ctx(bars, _Position(dict(message.get("position") or {})))
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
