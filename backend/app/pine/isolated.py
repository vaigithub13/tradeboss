"""Run a user strategy in a worker. The engine only sees the signals that come back.

The worker runs under macOS sandbox-exec (C2, review 2026-10-11): deny by default, no network, no new process,
writes only in its own temp dir, reads only that dir plus the read-only Python runtime and the app code. The
strategy file is copied into the temp dir, and the bars arrive on stdin. The environment is an allow-list, so
no key or token reaches it, and the repo root (where .env lives) and data/ are not readable. Without
sandbox-exec a user strategy does not run (fail closed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.backtest.contracts import LookAheadError, Signal, Strategy
from app.pine.worker import SECRET_ENV

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
#: read-only system locations the Python runtime needs to start (dyld, frameworks, time zones, devices)
_SYSTEM_READS = ("/usr/lib", "/System", "/usr/share/zoneinfo", "/private/var/db/timezone")
_DEVICES = ("/dev/null", "/dev/urandom", "/dev/random", "/private/etc/localtime")


def _quoted(path: Path | str) -> str:
    text = str(path)
    if '"' in text or "\\" in text or "\n" in text:
        raise WorkerError(f"refusing a sandbox path with a quote, backslash or newline: {text!r}")
    return f'"{text}"'


def sandbox_profile(work_dir: Path) -> str:
    """The worker's sandbox-exec profile. `work_dir` is the only place it may write."""
    work = Path(os.path.realpath(work_dir))
    runtime = Path(os.path.realpath(sys.base_prefix))
    venv = Path(os.path.realpath(sys.prefix))
    backend = Path(os.path.realpath(BACKEND_ROOT))
    reads = [runtime, venv, backend / "app", work, *_SYSTEM_READS]
    return "\n".join([
        "(version 1)",
        "(deny default)",
        f"(allow process-exec (subpath {_quoted(runtime)}) (subpath {_quoted(venv)}))",
        "(allow file-read-metadata)",
        # the directory entries themselves (Python's import finder lists them), not the files in them
        f'(allow file-read* (literal "/") (literal {_quoted(backend)}))',
        "(allow file-read* " + " ".join(f"(subpath {_quoted(r)})" for r in reads)
        + " " + " ".join(f"(literal {_quoted(d)})" for d in _DEVICES) + ")",
        f'(allow file-write* (subpath {_quoted(work)}) (literal "/dev/null"))',
        "(allow sysctl-read)",
    ])


def sandboxed_command(work_dir: Path, args: list[str]) -> list[str]:
    """`python <args>` under the worker's sandbox profile."""
    if not SANDBOX_EXEC.is_file():
        raise WorkerError(f"{SANDBOX_EXEC} is not available: user strategies only run inside the sandbox")
    return [str(SANDBOX_EXEC), "-p", sandbox_profile(work_dir), sys.executable, *args]


def worker_env(work_dir: Path) -> dict[str, str]:
    """An allow-list: nothing from the backend's environment except the locale."""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(BACKEND_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "HOME": str(work_dir),
        "TMPDIR": str(work_dir),
    }
    for key in ("LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


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
        self._work = Path(os.path.realpath(tempfile.mkdtemp(prefix="tb-worker-")))
        try:
            self.command = sandboxed_command(self._work, ["-m", "app.pine.worker"])
        except WorkerError:
            shutil.rmtree(self._work, ignore_errors=True)
            raise
        copy = self._work / f"{self.path.stem}.py"
        shutil.copyfile(self.path, copy)
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=worker_env(self._work),
            cwd=str(self._work),
            text=True,
        )
        self._err: list[str] = []
        assert self._proc.stderr is not None
        threading.Thread(target=self._drain, args=(self._proc.stderr,), daemon=True).start()
        ready = self._read(15.0)
        self.worker_info = {key: ready.get(key) for key in ("config_loaded", "config_importable", "sandboxed")}
        self.secrets_seen = [key for key in ready.get("secrets", []) if key in SECRET_ENV]
        if ready.get("config_importable") or ready.get("config_loaded") or not ready.get("sandboxed"):
            self.close()
            raise WorkerError(f"the strategy worker is not isolated: {self.worker_info}")
        loaded = self._rpc({"op": "load", "path": str(copy), "params": self.params}, 5.0)
        self._apply_settings(loaded.get("settings") or {}, loaded.get("missing") or [])

    def _apply_settings(self, values: dict[str, Any], missing: list[str]) -> None:
        """Copy the loaded strategy's settings. A missing one keeps the safe value and is warned."""
        self.settings_warnings: list[str] = []
        for key, value in values.items():
            setattr(self, key, value)
        for key in missing:
            value = getattr(self, key)
            self.settings_warnings.append(f"strategy does not set {key}; using {value!r}")

    def on_start(self, ctx: Any) -> None:
        """Give the worker the warm-up bars the engine already has, and the same context."""
        rows = []
        for i in range(len(ctx.bars)):
            bar = ctx.bars[i]
            rows.append({key: bar[key] for key in ("time", "open", "high", "low", "close", "volume")})
        position = ctx.position
        self._rpc({
            "op": "seed",
            "bars": rows,
            "position": {
                "side": position.side, "lots": position.lots,
                "units": position.units, "avg_price": position.avg_price,
            },
            "context": {
                "symbol": ctx.symbol,
                "timeframe": ctx.timeframe,
                "base_minutes": ctx.base_minutes,
                "time": int(ctx.time),
                "cash": ctx.cash,
                "lot_size": ctx.lot_size,
                "open_orders": ctx.open_orders,
            },
        }, self.call_timeout)

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
            "context": {
                "symbol": ctx.symbol,
                "timeframe": ctx.timeframe,
                "base_minutes": ctx.base_minutes,
                "time": int(ctx.time),
                "cash": ctx.cash,
                "lot_size": ctx.lot_size,
                "open_orders": ctx.open_orders,
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
        if proc.poll() is None:
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
        shutil.rmtree(self._work, ignore_errors=True)
