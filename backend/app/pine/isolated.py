"""Run a user strategy in a worker. The engine only sees the signals that come back."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from app.backtest.contracts import LookAheadError, Signal, Strategy
from app.pine.worker import SECRET_ENV, scrubbed_env

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class WorkerTimeout(TimeoutError):
    pass


class WorkerError(RuntimeError):
    pass


class IsolatedStrategy(Strategy):
    """Proxy. `on_bar` ships the closed bar to the worker and applies the signals here."""

    name = "user"

    def __init__(self, path: Path, *, call_timeout: float = 30.0, **params: Any) -> None:
        super().__init__(**params)
        self.path = Path(path)
        self.call_timeout = call_timeout
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "app.pine.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=scrubbed_env(dict(os.environ)),
            cwd=str(BACKEND_ROOT),
            text=True,
        )
        self._err: list[str] = []
        assert self._proc.stderr is not None
        threading.Thread(target=self._drain, args=(self._proc.stderr,), daemon=True).start()
        ready = self._read(5.0)
        self.secrets_seen = [key for key in ready.get("secrets", []) if key in SECRET_ENV]
        self._rpc({"op": "load", "path": str(self.path), "params": self.params}, 5.0)

    def _drain(self, pipe: Any) -> None:
        for line in pipe:
            self._err.append(line)

    def _read(self, timeout: float) -> dict[str, Any]:
        assert self._proc.stdout is not None
        box: dict[str, str] = {}

        def read() -> None:
            box["line"] = self._proc.stdout.readline()  # type: ignore[union-attr]

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            self.close()
            raise WorkerTimeout(f"worker exceeded {timeout}s")
        line = box.get("line", "")
        if not line:
            self.close()
            raise WorkerError("".join(self._err) or "worker exited")
        return json.loads(line)

    def _rpc(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        reply = self._read(timeout)
        if reply.get("op") == "error":
            self.close()
            if reply.get("error") == "LookAheadError":
                raise LookAheadError(str(reply.get("message") or "future bar"))
            raise WorkerError(str(reply.get("message") or reply.get("error")))
        return reply

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        position = ctx.position
        reply = self._rpc({
            "op": "bar",
            "bar": {key: bar[key] for key in ("time", "open", "high", "low", "close", "volume")},
            "position": {
                "side": position.side, "lots": position.lots,
                "units": position.units, "avg_price": position.avg_price,
            },
        }, self.call_timeout)
        for tag in reply.get("cancel") or []:
            ctx.cancel_working(None if tag is None else str(tag))
        signals: list[Signal] = []
        for raw in reply.get("signals") or []:
            signals.append(Signal(
                raw["side"], int(raw["qty"]), raw.get("type") or "MARKET", raw.get("price"),
                tag=raw.get("tag") or "", stop=raw.get("stop"), target=raw.get("target"), oco=raw.get("oco"),
                stop_points=raw.get("stop_points"), target_points=raw.get("target_points"),
            ))
        return signals

    def on_stop(self, ctx: Any) -> None:
        self.close()

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is not None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.write(json.dumps({"op": "stop"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
