"""Side-by-side index vs estimated-option result. The index block is never rewritten."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.backtest.result import BacktestResult, canonical, digest


def _trade_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "contract" in out and hasattr(out["contract"], "to_dict"):
        out["contract"] = out["contract"].to_dict()
    return out


@dataclass
class OptionEstimate:
    label: str
    settings: dict[str, Any]
    trades: list[dict[str, Any]]
    unpriced: list[dict[str, Any]]
    metrics: dict[str, Any]
    warnings: list[str]
    counters: dict[str, int]
    weak: dict[str, Any]
    carry: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)
    premium_source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "settings": self.settings,
            "carry": self.carry,
            "warnings": self.warnings,
            "model_version": self.settings.get("model_version"),
            "counters": self.counters,
            "premium_source": self.premium_source,
            "metrics": self.metrics,
            "weak": self.weak,
            "data": self.data,
            "trades": [_trade_dict(t) for t in self.trades],
            "unpriced": list(self.unpriced),
        }

    def to_json(self) -> str:
        return canonical(self.to_dict())


@dataclass
class EstimatedResult:
    """Index result (byte-identical to the engine) plus an ESTIMATED option block."""

    label: str
    index: BacktestResult
    option: OptionEstimate
    run_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "run_id": self.run_id,
            "index": self.index.to_dict(),
            "option": self.option.to_dict(),
        }

    def to_json(self) -> str:
        return canonical(self.to_dict())


def estimated_run_id(index_run_id: str, settings: dict[str, Any], vix_sha256: str) -> str:
    return digest({"index_run_id": index_run_id, "overlay": settings, "vix_sha256": vix_sha256})
