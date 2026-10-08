"""Backtest result: plain data, canonical JSON (byte-identical for identical inputs)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Trade:
    id: int
    direction: str  # LONG | SHORT
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float  # weighted average if the exit was in parts
    lots: int
    units: int
    lot_size: int
    gross_pnl: float  # on the fill prices, i.e. after slippage
    charges: dict[str, float]
    charges_total: float
    slippage_cost: float  # already inside the fill prices; shown for information
    net_pnl: float  # gross - charges
    exit_reason: str  # market | stop | limit | square_off | session_end | end_of_data
    entry_tag: str
    exit_tag: str
    gap: bool
    ambiguous: bool
    optimistic: bool
    entry_at_open: bool = True  # market or a stop gapped through: the fill is the minute's open
    exit_at_open: bool = True
    entry_fills: int = 1  # more than one means the entry price is an average
    exit_fills: int = 1
    late: bool = False  # live timing: the entry stop was crossed in the decision lag and filled when it started working


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("ascii")).hexdigest()


@dataclass
class BacktestResult:
    trades: list[Trade]
    events: list[dict[str, Any]]
    metrics: dict[str, Any]
    counters: dict[str, int]
    warnings: list[str]
    optimistic: bool
    run_id: str
    config: dict[str, Any]
    strategy: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config": self.config,
            "strategy": self.strategy,
            "data": self.data,
            "optimistic": self.optimistic,
            "warnings": self.warnings,
            "counters": self.counters,
            "metrics": self.metrics,
            "trades": [asdict(t) for t in self.trades],
            "events": self.events,
        }

    def to_json(self) -> str:
        return canonical(self.to_dict())
