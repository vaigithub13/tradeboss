"""One settings file for the scanner. Defaults live here; overrides live in data/scanner/settings.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

SETUP_IDS = ("orb15", "flag", "rsi_extreme", "vwap_cross", "gap_go", "gap_fill")


def _setups_on() -> dict[str, bool]:
    return {name: True for name in SETUP_IDS}


@dataclass
class ScannerSettings:
    account_size: float = 200_000
    max_loss_per_trade: float = 500
    daily_loss_limit: float = 1_500
    max_trades_per_day: int = 3
    max_capital_per_trade: float = 50_000
    min_rr: float = 2.0
    entry_mode: str = "trigger_close"  # trigger_close | next_open
    orb_stop: str = "midpoint"  # midpoint | opposite
    target_mode: str = "2r"  # 2r | 3r | next_level
    trail_breakeven: bool = False
    tm_paths: int = 10_000
    tm_seed: int = 20261123
    tm_edge_points: float = 10.0
    tm_min_win_rate: float = 0.40
    tm_min_fires: int = 8
    universe: str = "nifty50"
    only_proven: bool = False
    auto_trade: bool = True
    live_subscribe: bool = True
    slippage_ticks: float = 1.0
    tick_size: float = 0.05
    setups: dict[str, bool] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.setups is None:
            self.setups = _setups_on()
        else:
            merged = _setups_on()
            merged.update({str(k): bool(v) for k, v in self.setups.items() if k in merged})
            self.setups = merged
        if self.entry_mode not in ("trigger_close", "next_open"):
            raise ValueError("entry_mode must be trigger_close or next_open")
        if self.orb_stop not in ("midpoint", "opposite"):
            raise ValueError("orb_stop must be midpoint or opposite")
        if self.target_mode not in ("2r", "3r", "next_level"):
            raise ValueError("target_mode must be 2r, 3r, or next_level")
        if self.tm_paths < 1:
            raise ValueError("tm_paths must be >= 1")
        if self.max_trades_per_day < 1:
            raise ValueError("max_trades_per_day must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScannerSettings:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


def load_settings(path: Path | None) -> ScannerSettings:
    if path is None or not path.is_file():
        return ScannerSettings()
    return ScannerSettings.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_settings(path: Path, settings: ScannerSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2) + "\n", encoding="utf-8")
