"""Exit rules shared by the backtest and paper (one code path for the levels and the hit rule).

Two 1:2 risk/reward rules:

* `premium`: the bought option's own premium. Stop at entry fill x (1 - stop), target at entry fill x (1 + target)
  (1:2 = -20% / +40%). The index position is not touched: an opposite signal still reverses it, and the 15:15
  square-off still closes what is open. The backtest checks the contract's real 1m bars (the option overlay);
  paper checks every traded price of the contract and sells at the bid.
* `atr`: the index. Stop `stop` x ATR(14) from the index fill, target `target` x ATR (1:2 = 1x / 2x), ATR on the
  strategy's own timeframe, as of the bar that placed the entry. These close the index position (the strategy
  sees itself flat). The backtest places them as the broker's bracket on the 1m bars; paper checks the live
  index price and sells the option at the bid.

The hit rule (`first_hit`): a price that opens through a level exits at the open; a minute that touches both
levels is the stop; otherwise the level that was touched, at the level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from app.backtest.contracts import Signal, Strategy

KINDS = ("premium", "atr")


@dataclass(frozen=True)
class ExitRule:
    kind: str  # premium | atr
    stop: float  # premium: fraction of the entry premium; atr: multiple of ATR
    target: float
    atr_length: int = 14

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"exit rule kind must be one of {KINDS}")
        for name in ("stop", "target"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ValueError(f"exit rule {name} must be a positive number")
        if self.kind == "premium" and self.stop >= 1:
            raise ValueError("a premium stop is a fraction of the entry premium, below 1")
        if isinstance(self.atr_length, bool) or not isinstance(self.atr_length, int) or self.atr_length < 1:
            raise ValueError("atr_length must be a whole number >= 1")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "stop": float(self.stop), "target": float(self.target)}
        if self.kind == "atr":
            out["atr_length"] = self.atr_length
        return out

    @property
    def label(self) -> str:
        if self.kind == "premium":
            return f"premium -{self.stop * 100:g}% / +{self.target * 100:g}%"
        return f"ATR({self.atr_length}) {self.stop:g}x / {self.target:g}x"


PREMIUM_1TO2 = ExitRule("premium", 0.20, 0.40)
ATR_1TO2 = ExitRule("atr", 1.0, 2.0)
NAMED = {"premium_1to2": PREMIUM_1TO2, "atr_1to2": ATR_1TO2}


def parse_exit_rule(value: Any) -> ExitRule | None:
    """None, a name ("premium_1to2", "atr_1to2") or {"kind", "stop", "target", "atr_length"?}."""
    if value is None or isinstance(value, ExitRule):
        return value
    if isinstance(value, str):
        if value not in NAMED:
            raise ValueError(f"exit rule must be one of {sorted(NAMED)} or an object")
        return NAMED[value]
    if not isinstance(value, dict):
        raise ValueError("exit rule must be a name or an object")
    unknown = sorted(set(value) - {"kind", "stop", "target", "atr_length"})
    if unknown:
        raise ValueError(f"unknown exit rule field(s) {unknown}")
    try:
        return ExitRule(kind=value["kind"], stop=value["stop"], target=value["target"],
                        atr_length=value.get("atr_length", 14))
    except KeyError as exc:
        raise ValueError(f"exit rule needs {exc.args[0]!r}") from None


def premium_levels(entry_fill: float, rule: ExitRule) -> tuple[float, float]:
    """(stop, target) premium of a bought option."""
    return round(entry_fill * (1 - rule.stop), 2), round(entry_fill * (1 + rule.target), 2)


def index_levels(direction: str, index_fill: float, stop_points: float, target_points: float) -> tuple[float, float]:
    """(stop, target) index levels of a LONG or SHORT index position."""
    if direction == "LONG":
        return round(index_fill - stop_points, 2), round(index_fill + target_points, 2)
    return round(index_fill + stop_points, 2), round(index_fill - target_points, 2)


def first_hit(stop: float | None, target: float | None, o: float, h: float, lo: float, *,
              long: bool) -> tuple[str, float] | None:
    """The exit inside one minute (open, high, low), or None. `long`: the stop is below and the target above
    (a bought option, a long index position); otherwise the reverse. A single price is o = h = lo."""
    if long:
        if stop is not None and o <= stop:
            return "stop", o
        if target is not None and o >= target:
            return "target", o
        if stop is not None and lo <= stop:
            return "stop", stop
        if target is not None and h >= target:
            return "target", target
        return None
    if stop is not None and o >= stop:
        return "stop", o
    if target is not None and o <= target:
        return "target", o
    if stop is not None and h >= stop:
        return "stop", stop
    if target is not None and lo <= target:
        return "target", target
    return None


class AtrState:
    """ta.atr, one closed bar at a time: true range (high - low on the first bar), Wilder RMA seeded with the SMA
    of the first `length` true ranges. Equal to app.indicators.volatility.atr on the same bars."""

    def __init__(self, length: int) -> None:
        self.length = length
        self.value: float | None = None
        self._prev_close: float | None = None
        self._seed: list[float] = []

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        if self.value is None:
            self._seed.append(tr)
            if len(self._seed) == self.length:
                self.value = sum(self._seed) / self.length
            return self.value
        self.value = self.value + (tr - self.value) / self.length
        return self.value


class WithExits(Strategy):
    """A strategy with an exit rule. For `atr`, each entry signal (BUY / SELL) carries stop_points = stop x ATR and
    target_points = target x ATR, ATR as of the bar just closed, so the broker (backtest) or the paper session
    places the bracket from the fill. Entries before ATR exists go without one (counted). `premium` changes no
    signal: the option overlay (backtest) and the paper session apply it to the premium."""

    def __init__(self, inner: Strategy, rule: ExitRule) -> None:
        self.inner = inner
        self.rule = rule
        self.params = {**dict(getattr(inner, "params", {})), "exit_rule": rule.to_dict()}
        self.atr = AtrState(rule.atr_length)
        self.warnings_for_unarmed = 0

    def __getattr__(self, name: str) -> Any:  # name, pyramiding, allow_overnight, bar_path, execution, ...
        if name in ("inner", "rule"):
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
        if self.rule.kind == "atr":
            self.atr.update(float(bar["high"]), float(bar["low"]), float(bar["close"]))
        signals = list(self.inner.on_bar(bar, ctx) or [])
        if self.rule.kind != "atr":
            return signals
        out = []
        for sig in signals:
            if isinstance(sig, Signal) and sig.side in ("BUY", "SELL"):
                a = self.atr.value
                if a is None or a <= 0:
                    self.warnings_for_unarmed += 1
                else:
                    sig = replace(sig, stop_points=self.rule.stop * a, target_points=self.rule.target * a)
            out.append(sig)
        return out

    @property
    def settings_warnings(self) -> list[str]:
        notes = list(getattr(self.inner, "settings_warnings", ()))
        if self.warnings_for_unarmed:
            notes.append(f"{self.warnings_for_unarmed} entry signal(s) came before ATR({self.rule.atr_length}) existed: "
                         "no stop or target on them")
        return notes
