"""Wrap a strategy to resize entries by fixed-₹ risk (equity/futures)."""

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
    """Resize BUY/SELL entry lots from stop distance.

    Over-notional sizes are capped at floor(cap/price) (risk below target), not skipped.
    Skip only when qty < 1. Tracks capped trades and actual risk for reporting.
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
        self.capped: list[dict[str, Any]] = []
        self.accepted_risks: list[float] = []

    def __getattr__(self, name: str) -> Any:
        if name in (
            "inner", "sizing", "skipped", "capped", "accepted_risks",
            "atr_length", "atr_mult", "params", "name",
        ):
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

    def sizing_stats(self) -> dict[str, Any]:
        risks = self.accepted_risks
        return {
            "signals_skipped": len(self.skipped),
            "trades_capped": len(self.capped),
            "trades_sized": len(risks),
            "avg_actual_risk": (sum(risks) / len(risks)) if risks else None,
            "target_risk": self.sizing.risk_rupees,
            "skip_reasons": _count_reasons(self.skipped),
        }

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
            result = self.sizing.size(stop_distance=dist, entry_price=close)
            if result.skip_reason is not None:
                self.skipped.append(
                    {
                        "time": int(bar["time"]),
                        "tag": sig.tag,
                        "reason": result.skip_reason,
                        "stop_distance": dist,
                        "close": close,
                    }
                )
                continue
            if result.capped:
                self.capped.append(
                    {
                        "time": int(bar["time"]),
                        "tag": sig.tag,
                        "qty": result.qty_units,
                        "close": close,
                        "stop_distance": dist,
                        "actual_risk": result.actual_risk,
                        "target_risk": result.target_risk,
                    }
                )
            self.accepted_risks.append(result.actual_risk)
            lots = max(1, result.qty_units // self.sizing.lot_size)
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
        stats = self.sizing_stats()
        if stats["signals_skipped"]:
            notes.append(f"risk sizing skipped {stats['signals_skipped']} signal(s)")
        if stats["trades_capped"]:
            avg = stats["avg_actual_risk"]
            notes.append(
                f"risk sizing capped {stats['trades_capped']} trade(s); "
                f"avg actual risk ₹{avg:.1f} vs target ₹{stats['target_risk']:.0f}"
                if avg is not None
                else f"risk sizing capped {stats['trades_capped']} trade(s)"
            )
        return notes


def _count_reasons(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get("reason") or "unknown")
        out[key] = out.get(key, 0) + 1
    return out
