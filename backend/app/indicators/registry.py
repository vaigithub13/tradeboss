"""Single entry point to every indicator: parameter validation, warm-up, compute().

The chart API and (Phase 3) backtests both go through `compute`, so they always agree.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.indicators.basic import SOURCES, bollinger, ema, sma, source_series
from app.indicators.momentum import macd, rsi
from app.indicators.volatility import supertrend
from app.indicators.volume import vwap

INDICATOR_TYPES: tuple[str, ...] = ("sma", "ema", "bb", "supertrend", "rsi", "macd", "vwap")

PANES: dict[str, str] = {
    "sma": "price", "ema": "price", "bb": "price", "supertrend": "price", "vwap": "price",
    "rsi": "separate", "macd": "separate",
}  # fmt: skip

OUTPUTS: dict[str, list[str]] = {
    "sma": ["sma"],
    "ema": ["ema"],
    "bb": ["basis", "upper", "lower"],
    "supertrend": ["supertrend", "direction"],
    "rsi": ["rsi"],
    "macd": ["macd", "signal", "hist"],
    "vwap": ["vwap"],
}

MIN_WARMUP_BARS = 500
MIN_SUPERTREND_WARMUP_BARS = 1000  # band/direction state only converges after a trend flip
MAX_LENGTH = 2000
MAX_MULTIPLIER = 100.0


@dataclass(frozen=True)
class ParamSpec:
    kind: str  # "int" | "float" | "source"
    default: Any
    minimum: float = 1
    maximum: float = MAX_LENGTH


_LENGTH = ParamSpec("int", 20, 1, MAX_LENGTH)
_SOURCE = ParamSpec("source", "close")

PARAMS: dict[str, dict[str, ParamSpec]] = {
    "sma": {"length": _LENGTH, "source": _SOURCE},
    "ema": {"length": _LENGTH, "source": _SOURCE},
    "bb": {
        "length": _LENGTH,
        "mult": ParamSpec("float", 2.0, 0, MAX_MULTIPLIER),
        "source": _SOURCE,
    },
    "supertrend": {
        "atr_length": ParamSpec("int", 10, 1, MAX_LENGTH),
        "multiplier": ParamSpec("float", 3.0, 0, MAX_MULTIPLIER),
    },
    "rsi": {"length": ParamSpec("int", 14, 2, MAX_LENGTH), "source": _SOURCE},
    "macd": {
        "fast": ParamSpec("int", 12, 1, MAX_LENGTH),
        "slow": ParamSpec("int", 26, 1, MAX_LENGTH),
        "signal": ParamSpec("int", 9, 1, MAX_LENGTH),
        "source": _SOURCE,
    },
    "vwap": {"source": ParamSpec("source", "hlc3")},
}


def _coerce(name: str, spec: ParamSpec, value: Any) -> Any:
    if spec.kind == "source":
        if value not in SOURCES:
            raise ValueError(f"{name}: unknown source {value!r}; expected one of {list(SOURCES)}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if spec.kind == "int":
        if value != int(value):
            raise ValueError(f"{name} must be a whole number, got {value!r}")
        value = int(value)
        if not spec.minimum <= value <= spec.maximum:
            raise ValueError(f"{name} must be between {int(spec.minimum)} and {int(spec.maximum)}")
        return value
    value = float(value)
    if not spec.minimum < value <= spec.maximum:
        raise ValueError(f"{name} must be > {spec.minimum:g} and <= {spec.maximum:g}")
    return value


def validate_params(itype: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Defaults filled in; unknown names / out-of-range values raise ValueError."""
    if itype not in PARAMS:
        raise ValueError(f"Unknown indicator type {itype!r}; expected one of {list(INDICATOR_TYPES)}")
    specs = PARAMS[itype]
    given = dict(params or {})
    unknown = sorted(set(given) - set(specs))
    if unknown:
        raise ValueError(f"{itype}: unknown parameter(s) {unknown}; expected {list(specs)}")
    out = {name: _coerce(name, spec, given.get(name, spec.default)) for name, spec in specs.items()}
    if itype == "macd" and out["fast"] >= out["slow"]:
        raise ValueError("macd: fast length must be smaller than slow length")
    return out


def warmup_bars(itype: str, params: Mapping[str, Any], bar_minutes: int) -> int:
    """Bars to calculate BEFORE the first visible bar so its value equals full-history values.

    max(per-indicator rule, 500); Supertrend at least 1000.
    """
    if itype in ("sma", "bb"):
        rule = params["length"]
    elif itype == "ema":
        rule = 8 * (params["length"] + 1)  # seed error shrinks by e^-16
    elif itype == "rsi":
        rule = 10 * params["length"]  # Wilder smoothing, seeded with an SMA
    elif itype == "supertrend":
        rule = 10 * params["atr_length"]
    elif itype == "macd":
        rule = 8 * (params["slow"] + 1) + 8 * (params["signal"] + 1)
    elif itype == "vwap":
        rule = math.ceil(1440 / bar_minutes)  # 24h of bars: always reaches the start of the day
    else:
        raise ValueError(f"Unknown indicator type {itype!r}")
    floor = MIN_SUPERTREND_WARMUP_BARS if itype == "supertrend" else MIN_WARMUP_BARS
    return max(int(rule), floor)


def compute(df: pd.DataFrame, itype: str, params: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """Run one indicator over `df` (columns time/open/high/low/close/volume). Params must be validated."""
    if itype == "sma":
        series = {"sma": sma(source_series(df, params["source"]), params["length"])}
    elif itype == "ema":
        series = {"ema": ema(source_series(df, params["source"]), params["length"])}
    elif itype == "bb":
        bb = bollinger(source_series(df, params["source"]), params["length"], params["mult"])
        series = {name: bb[name] for name in OUTPUTS["bb"]}
    elif itype == "supertrend":
        st = supertrend(df, params["atr_length"], params["multiplier"])
        series = {name: st[name] for name in OUTPUTS["supertrend"]}
    elif itype == "rsi":
        series = {"rsi": rsi(source_series(df, params["source"]), params["length"])}
    elif itype == "macd":
        m = macd(source_series(df, params["source"]), params["fast"], params["slow"], params["signal"])
        series = {name: m[name] for name in OUTPUTS["macd"]}
    elif itype == "vwap":
        series = {"vwap": vwap(df, params["source"])}
    else:
        raise ValueError(f"Unknown indicator type {itype!r}")
    return {name: s.to_numpy(dtype=float) for name, s in series.items()}


@dataclass(frozen=True)
class IndicatorSpec:
    id: str
    type: str
    params: dict[str, Any]
