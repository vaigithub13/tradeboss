"""Scanner records. `instrument` stays generic so a stock future can use the same plan later."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Side = Literal["long", "short", "flat"]
InstrumentKind = Literal["equity_intraday", "stock_future"]


@dataclass
class Hit:
    setup: str
    side: Side
    time: int
    close: float
    stop: float | None = None
    range_high: float | None = None
    range_low: float | None = None
    atr: float | None = None
    swing: float | None = None
    volume_ratio: float | None = None
    forming: bool = False
    resistance: float | None = None
    support: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    instrument: InstrumentKind
    symbol: str
    setup: str
    side: Side
    status: str
    entry: float | None
    stop: float | None
    target: float | None
    qty: int
    risk_per_share: float | None
    rupee_risk: float | None
    rupee_reward: float | None
    capital: float | None
    rr: float | None
    resistance: float | None
    support: float | None
    entry_time: int
    target_r: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TimeMachineResult:
    paths: int
    seed: int
    target_pct: float
    stop_pct: float
    open_pct: float
    avg_rupees: float
    histogram: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HistoryResult:
    fires: int
    wins: int
    losses: int
    neither: int
    win_rate: float | None
    avg_r: float | None
    occurrences: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
