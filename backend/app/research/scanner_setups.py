"""Research-only bridge from the scanner setups to the backtest engine.

Not a paper strategy and not in the backtest catalog. Slot 8 is unchanged.
The finders, plan rules, and duplicate-plan key are the scanner's own code
(version 2026-11-23.2): wrong-side stops are skipped, a stop tighter than
0.5% of price is skipped, and a later plan with the same side and prices
on the same day is dropped.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.backtest.contracts import Signal, Strategy
from app.scanner.bars import BAR_S, Bar, day_of, entry_allowed
from app.scanner.config import SETUP_IDS, ScannerSettings
from app.scanner.history import walk
from app.scanner.models import Hit
from app.scanner.plan import MIN_STOP_FRACTION, build_plan
from app.scanner.setups import run_setups
from app.scanner.setups.gap import find_gaps
from app.scanner.setups.orb15 import find_orb
from app.scanner.setups.vwap_cross import find_vwap_crosses

SETUPS = ("vwap_cross", "gap_fill", "orb15")
LOOKBACK_DAYS = 21


def research_settings() -> ScannerSettings:
    """Plan maths only. Quantity is resized later by the Phase C risk wrapper."""
    return ScannerSettings(
        max_loss_per_trade=1.0e12,
        max_capital_per_trade=1.0e12,
        min_rr=2.0,
        entry_mode="trigger_close",
        orb_stop="midpoint",
        target_mode="2r",
        setups={name: False for name in SETUP_IDS},
    )


def history_settings(setup: str) -> ScannerSettings:
    """The scanner's own defaults, one setup on, so a verdict uses the live judge inputs."""
    return ScannerSettings(
        setups={name: name == setup for name in SETUP_IDS},
        target_mode="2r",
    )


def _plan_key(plan: Any) -> tuple | None:
    if plan.status != "triggered":
        return None
    if plan.entry is None or plan.stop is None or plan.target is None or plan.rr is None:
        return None
    if plan.rr + 1e-9 < 2.0:
        return None
    return (
        plan.side,
        round(float(plan.entry), 2),
        round(float(plan.stop), 2),
        round(float(plan.target), 2),
    )


class ScannerSetupStrategy(Strategy):
    """One scanner setup, one symbol, flat-to-flat. Emits the plan's stop and target."""

    name = "scanner_setup"

    def __init__(
        self,
        setup: str,
        *,
        volume_mult: float | None = None,
        gap_min: float | None = None,
        min_width: float | None = None,
        max_width: float | None = None,
    ) -> None:
        if setup not in SETUPS:
            raise ValueError(f"unknown setup {setup!r}")
        super().__init__(
            setup=setup,
            volume_mult=volume_mult,
            gap_min=gap_min,
            min_width=min_width,
            max_width=max_width,
            min_stop_fraction=MIN_STOP_FRACTION,
        )
        self.setup = setup
        self.volume_mult = volume_mult
        self.gap_min = gap_min
        self.min_width = min_width
        self.max_width = max_width
        self._settings = research_settings()
        self._bars: list[Bar] = []
        self._win_i = 0
        self._day: date | None = None
        self._prev_close: float | None = None
        self._seen: set[tuple] = set()

    def _window(self, day: date) -> list[Bar]:
        cutoff = day - timedelta(days=LOOKBACK_DAYS)
        i = self._win_i
        while i < len(self._bars) and day_of(int(self._bars[i]["time"])) < cutoff:
            i += 1
        self._win_i = i
        return self._bars[i:]

    def _hits(self, window: list[Bar], *, as_of: int, prev_close: float | None, hold_time: int) -> list[Hit]:
        if self.setup == "orb15":
            kw: dict[str, Any] = {}
            if self.volume_mult is not None:
                kw["volume_mult"] = self.volume_mult
            if self.min_width is not None:
                kw["min_width"] = self.min_width
            if self.max_width is not None:
                kw["max_width"] = self.max_width
            return find_orb(window, as_of=as_of, **kw)
        if self.setup == "vwap_cross":
            kw = {"hold_time": hold_time}
            if self.volume_mult is not None:
                kw["volume_mult"] = self.volume_mult
            return find_vwap_crosses(window, as_of=as_of, **kw)
        kw = {}
        if self.gap_min is not None:
            kw["gap_min"] = self.gap_min
        return [h for h in find_gaps(window, prev_close=prev_close, as_of=as_of, **kw) if h.setup == "gap_fill"]

    def on_bar(self, bar: dict[str, Any], ctx: Any) -> list[Signal]:
        day = day_of(int(bar["time"]))
        if self._day is not None and day != self._day:
            self._prev_close = float(self._bars[-1]["close"]) if self._bars else None
            self._seen.clear()
        self._day = day
        self._bars.append(
            {
                "time": int(bar["time"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar["volume"] or 0),
            }
        )
        if not entry_allowed(int(bar["time"])):
            return []
        if getattr(ctx.position, "side", 0):
            return []
        if ctx.open_orders:
            return []
        window = self._window(day)
        hits = self._hits(window, as_of=int(bar["time"]) + BAR_S, prev_close=self._prev_close, hold_time=int(bar["time"]))
        out: list[Signal] = []
        for hit in hits:
            if hit.forming or hit.setup != self.setup or int(hit.time) != int(bar["time"]):
                continue
            plan = build_plan(hit, self._settings, symbol=str(getattr(ctx, "symbol", "")))
            key = _plan_key(plan)
            if key is None or key in self._seen:
                continue
            self._seen.add(key)
            side = "BUY" if plan.side == "long" else "SELL"
            out.append(
                Signal(
                    side,
                    1,
                    stop=float(plan.stop),
                    target=float(plan.target),
                    tag=f"{self.setup}:{int(plan.entry_time)}",
                )
            )
        return out


def collect_history(bars: list[Bar], setup: str) -> dict[str, Any]:
    """One pass of the scanner history rule: last-59-session fires are sliced later.

    Each day is judged on the same 21-calendar-day window `history.occurrences` uses,
    built from a day index so the walk is one pass over the bars.
    """
    settings = history_settings(setup)
    by_day: dict[date, list[Bar]] = {}
    order: list[date] = []
    for bar in bars:
        d = day_of(int(bar["time"]))
        if d not in by_day:
            by_day[d] = []
            order.append(d)
        by_day[d].append(bar)
    days = [d.isoformat() for d in order]
    fires: list[dict[str, Any]] = []
    for i, day in enumerate(order):
        window_from = day - timedelta(days=LOOKBACK_DAYS)
        window: list[Bar] = []
        for prev_day in order[: i + 1]:
            if prev_day >= window_from:
                window.extend(by_day[prev_day])
        prev = float(by_day[order[i - 1]][-1]["close"]) if i else None
        hits = run_setups(window, enabled=settings.setups, prev_close=prev, as_of=None)
        for hit in hits:
            if hit.forming or hit.setup != setup or day_of(hit.time) != day:
                continue
            plan = build_plan(hit, settings, symbol="", next_bar=None)
            if plan.entry is None or plan.stop is None or plan.target is None or plan.rr is None:
                continue
            after = [b for b in by_day[day] if int(b["time"]) > plan.entry_time]
            reason, exit_px, r = walk(
                after,
                side=plan.side,
                entry=float(plan.entry),
                stop=float(plan.stop),
                target=float(plan.target),
                target_r=float(plan.target_r),
            )
            fires.append(
                {
                    "day": day.isoformat(),
                    "time": int(hit.time),
                    "side": plan.side,
                    "entry": float(plan.entry),
                    "stop": float(plan.stop),
                    "target": float(plan.target),
                    "qty": int(plan.qty),
                    "entry_time": int(plan.entry_time),
                    "reason": reason,
                    "r": float(r),
                    "exit": float(exit_px),
                }
            )
    return {"days": days, "fires": fires}


def history_counts(collected: dict[str, Any], up_to: date, *, limit: int = 59) -> tuple[int, float | None]:
    """Fires and win rate on the last `limit` sessions before `up_to`. A win is a target exit."""
    days = [date.fromisoformat(d) for d in collected["days"] if date.fromisoformat(d) < up_to][-limit:]
    wanted = {d.isoformat() for d in days}
    rows = [row for row in collected["fires"] if row["day"] in wanted]
    fires = len(rows)
    if not fires:
        return 0, None
    wins = sum(1 for row in rows if row["reason"] == "target")
    return fires, wins / fires


def pooled_counts(by_symbol: dict[str, dict[str, Any]], up_to: date, *, limit: int = 59) -> tuple[int, float | None]:
    fires = 0
    wins = 0
    for collected in by_symbol.values():
        days = [date.fromisoformat(d) for d in collected["days"] if date.fromisoformat(d) < up_to][-limit:]
        wanted = {d.isoformat() for d in days}
        rows = [row for row in collected["fires"] if row["day"] in wanted]
        fires += len(rows)
        wins += sum(1 for row in rows if row["reason"] == "target")
    if not fires:
        return 0, None
    return fires, wins / fires
