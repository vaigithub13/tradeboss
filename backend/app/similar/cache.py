"""Feature banks and reliability files. Callers pass the directory.

Tests pass ``tmp_path``. Nothing here defaults to the real ``data/`` tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

BANK_VERSION = 1


def bank_path(cache_dir: Path, symbol: str, timeframe: str, window: int) -> Path:
    return Path(cache_dir) / f"{symbol}_{timeframe}_w{window}_v{BANK_VERSION}.npz"


def reliability_path(cache_dir: Path, symbol: str, timeframe: str, window: int) -> Path:
    return Path(cache_dir) / f"{symbol}_{timeframe}_w{window}_v{BANK_VERSION}_reliability.json"


def days_reliability_path(cache_dir: Path, symbol: str) -> Path:
    return Path(cache_dir) / f"{symbol}_days_v{BANK_VERSION}_reliability.json"


def save_bank(path: Path, payload: dict[str, np.ndarray]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, version=np.array([BANK_VERSION]), **payload)


def load_bank(path: Path) -> dict[str, np.ndarray] | None:
    path = Path(path)
    if not path.is_file():
        return None
    with np.load(path) as data:
        if int(data["version"][0]) != BANK_VERSION:
            return None
        return {key: data[key] for key in data.files if key != "version"}


def save_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
