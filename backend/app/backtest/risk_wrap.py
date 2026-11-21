"""Wrap a strategy to resize entries by fixed-₹ risk (Phase B equity/futures)."""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.backtest.sizing import RiskSizing, stop_distance_from_signal


def _atr_from_bars(ctx: Any, length: int) -> float | None:
    """Wilder ATR from closed bars visible on ctx (no registry ATR type)."""
    bars = ctx.bars
    n = len(bars)
    if n < length + 1:
        return None
    trs: list[float] = []
    for i in range(n - length, n):
        h = float(bars[i]["high"])
        lo = float(bars[i]["low"])
        prev_c = float(bars[i - 1]["close"]) if i > 0 else float(bars[i]["close"])
        trs.append(max(h - lo, abs(h - prev_c), abs(lo - prev_c)))
    if len(trs) < length:
        return None
    atr = sum(trs[:length]) / length
    for tr in trs[length:]:
        atr = atr + (tr - atr) / length
    return float(atr)


class WithRiskSizing(Strategy):
    """Resize BUY/SELL entry lots from stop distance; skip when under-sized or over notional cap.

    When the signal has no stop / stop_points, fall back to `atr_mult * ATR(atr_length)` on the
    strategy timeframe (sizing only — does not invent a broker stop unless the signal already had one).
    """

    def __init__(
        self,
        inner: Strategy,
        sizing: RiskSizing,
        *,
        atr_length: int = 14,
        atr_mult: float = 1.0,
    ) -> None:
        super().__init__(**dict(getattr(inner, "params", {})), _risk=sizing.to_dict())
        self.inner = inner
        self.sizing = sizing
        self.atr_length = atr_length
        self.atr_mult = float(atr_mult)
        self.skipped: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        if name in ("inner", "sizing", "skipped", "atr_length", "atr_mult", "params", "name"):
            raise AttributeError(name)
        return getattr(self.inner, name)

    @property
    def name(self) -> str:  # type: ignore[override]
        return str(getattr(self.inner, "name", "strategy"))

    @property
    def allow_overnight(self) -> bool:  # type: ignore[override]
        return bool(getattr(self.inner, "allow_overnight", False))

    def on_start(self, ctx: Any) -> None:
        self.inner.on_start(ctx)

    def on_stop(self, ctx: Any) -> None:
        self.inner.on_stop(ctx)

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        raw = list(self.inner.on_bar(bar, ctx) or [])
        out: list[Signal] = []
        close = float(bar["close"])
        for sig in raw:
            if not isinstance(sig, Signal) or sig.side == "EXIT":
                out.append(sig)
                continue
            if sig.side not in ("BUY", "SELL"):
                out.append(sig)
                continue
            dist = stop_distance_from_signal(entry=close, stop=sig.stop, stop_points=sig.stop_points)
            if dist is None:
                atr = _atr_from_bars(ctx, self.atr_length)
                if atr is None or atr <= 0:
                    self.skipped.append({"time": int(bar["time"]), "tag": sig.tag, "reason": "no_stop_or_atr"})
                    continue
                dist = self.atr_mult * atr
            qty_units, reason = self.sizing.size(stop_distance=dist, entry_price=close)
            if reason is not None:
                self.skipped.append(
                    {
                        "time": int(bar["time"]),
                        "tag": sig.tag,
                        "reason": reason,
                        "stop_distance": dist,
                        "close": close,
                    }
                )
                continue
            lots = max(1, qty_units // self.sizing.lot_size)
            out.append(
                Signal(
                    sig.side,
                    lots,
                    type=sig.type,
                    price=sig.price,
                    tag=sig.tag,
                    stop=sig.stop,
                    target=sig.target,
                    oco=sig.oco,
                    stop_points=sig.stop_points,
                    target_points=sig.target_points,
                )
            )
        return out

    @property
    def settings_warnings(self) -> list[str]:
        notes = list(getattr(self.inner, "settings_warnings", ()))
        if self.skipped:
            notes.append(f"risk sizing skipped {len(self.skipped)} signal(s)")
        return notes
