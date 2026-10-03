"""SpringPad Log XZ.

Default average is Pine's RMA(close, length). `ma="ema"` uses Pine's EMA, which
seeds with the SMA of the first `length` closes. XZ is log(average)[1] - log(average)[4]:
the current bar's close is not in it. A buy is XZ[1] <= 0 and XZ > 0.
"""

from __future__ import annotations

import math
from typing import Any

from app.backtest.contracts import Signal
from app.strategies.pine_common import PinePort, entry_window, timeframe_minutes

AVERAGES = ("rma", "ema")


class LogXZ(PinePort):
    name = "log_xz"

    def __init__(self, z_length: int = 14, ma: str = "rma", **kw: Any) -> None:
        if isinstance(z_length, bool) or not isinstance(z_length, int) or z_length < 1:
            raise ValueError("z_length must be a whole number >= 1")
        if ma not in AVERAGES:
            raise ValueError(f"ma must be one of {AVERAGES}")
        super().__init__(z_length=z_length, ma=ma, **kw)
        self.z_length = z_length
        self.ma = ma
        self._n = 0
        self._warm: list[float] = []
        self._avg: float | None = None
        self._log: list[float | None] = []
        self._prev_xz: float | None = None
        self._alpha = 2.0 / (z_length + 1)

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        current = self._push(float(bar["close"]))
        previous = self._prev_xz
        self._prev_xz = current
        closing = self.session_exit(bar, ctx)
        if closing is not None:
            return closing
        if not entry_window(int(bar["time"]), timeframe_minutes(ctx.timeframe)) or current is None or previous is None:
            return []
        buy = previous <= 0 < current
        sell = previous >= 0 > current
        side = ctx.position.side
        if buy and side <= 0:
            return [self.entry("BUY", None, "LE")]
        if sell and side >= 0:
            return [self.entry("SELL", None, "SE")]
        return []

    def _push(self, close: float) -> float | None:
        self._n += 1
        avg = self._average(close)
        self._log.append(None if avg is None or avg <= 0 else math.log(avg))
        if len(self._log) < 5:
            return None
        newer, older = self._log[-2], self._log[-5]
        if newer is None or older is None:
            return None
        return newer - older

    def _average(self, close: float) -> float | None:
        length = self.z_length
        if self._n < length:
            self._warm.append(close)
            return None
        if self._n == length:
            self._avg = (sum(self._warm) + close) / length
            return self._avg
        assert self._avg is not None
        if self.ma == "rma":
            self._avg = (self._avg * (length - 1) + close) / length
        else:
            self._avg = self._alpha * close + (1.0 - self._alpha) * self._avg
        return self._avg
