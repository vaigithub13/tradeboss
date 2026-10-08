"""The strategies the backtest panel can run, and the form that starts one."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.backtest.contracts import Strategy
from app.backtest.engine import INTRADAY_MIN
from app.data.sessions import SESSION_TYPES
from app.strategies.ema_cross import EmaCrossover
from app.strategies.log_xz import LogXZ
from app.strategies.orb import OpeningRangeBreakout
from app.strategies.pivot_extension import PivotExtension
from app.strategies.price_channel import PriceChannel
from app.strategies.supertrend_flip import SupertrendFlip
from app.options.model import canonical_option_fill

NIFTY_SYMBOL = "NIFTY50"


class RunRequestError(ValueError):
    pass


class _Field:
    def __init__(self, kind: str, default: Any, *, min_value: float | None = None, choices: tuple[str, ...] | None = None) -> None:
        self.kind = kind
        self.default = default
        self.min_value = min_value
        self.choices = choices


_CATALOG: dict[str, tuple[type[Strategy], dict[str, _Field]]] = {
    "ema_crossover": (
        EmaCrossover,
        {
            "fast": _Field("int", 9, min_value=1),
            "slow": _Field("int", 21, min_value=2),
            "mode": _Field("str", "long_short", choices=("long_only", "long_short")),
            "lots": _Field("int", 1, min_value=1),
        },
    ),
    "supertrend_flip": (
        SupertrendFlip,
        {
            "atr_length": _Field("int", 10, min_value=1),
            "multiplier": _Field("float", 3.0, min_value=0.1),
            "mode": _Field("str", "long_short", choices=("long_only", "long_short")),
            "lots": _Field("int", 1, min_value=1),
        },
    ),
    "opening_range_breakout": (
        OpeningRangeBreakout,
        {
            "range_minutes": _Field("int", 15, min_value=1),
            "lots": _Field("int", 1, min_value=1),
        },
    ),
    "pivot_extension": (
        PivotExtension,
        {
            "left_bars": _Field("int", 4, min_value=1),
            "right_bars": _Field("int", 2, min_value=1),
            "variant": _Field("str", "faithful", choices=("faithful", "carried_pivots")),
            "execution": _Field("str", "realistic", choices=("realistic", "tv_parity")),
            "lots": _Field("int", 1, min_value=1),
        },
    ),
    "log_xz": (
        LogXZ,
        {
            "z_length": _Field("int", 14, min_value=1),
            "ma": _Field("str", "rma", choices=("rma", "ema")),
            "execution": _Field("str", "realistic", choices=("realistic", "tv_parity")),
            "lots": _Field("int", 1, min_value=1),
            # the script's own target/stop inputs (index points), off by default as in the script
            "use_target": _Field("bool", False),
            "use_stop": _Field("bool", False),
            "target_points": _Field("float", 10.0, min_value=0.05),
            "stop_points": _Field("float", 7.0, min_value=0.05),
        },
    ),
    "price_channel": (
        PriceChannel,
        {
            "length": _Field("int", 20, min_value=1),
            "execution": _Field("str", "realistic", choices=("realistic", "tv_parity")),
            "lots": _Field("int", 1, min_value=1),
        },
    ),
}

_TOP = {
    "strategy", "params", "symbol", "timeframe", "start", "end", "sessions",
    "mode", "strike_offset", "slippage_points", "option_fill", "live_timing",
    *("premium_target_pct", "premium_stop_pct"),
}
#: option-premium exits: settings of the option overlay, not strategy parameters
PREMIUM_EXIT_KEYS = ("premium_target_pct", "premium_stop_pct")


def parse_premium_exits(body: dict[str, Any]) -> dict[str, float]:
    """`premium_target_pct` > 0 and 0 < `premium_stop_pct` < 1 (fractions of the entry premium), or absent."""
    out: dict[str, float] = {}
    for key in PREMIUM_EXIT_KEYS:
        value = body.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RunRequestError(f"{key} must be a number")
        number = float(value)
        if not number > 0 or (key == "premium_stop_pct" and number >= 1):
            raise RunRequestError(f"{key} must be > 0" + (" and < 1" if key == "premium_stop_pct" else ""))
        out[key] = number
    return out


def strategy_catalog() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, (_cls, fields) in _CATALOG.items():
        out.append({
            "name": name,
            "params": {
                key: {
                    "type": field.kind,
                    "default": field.default,
                    **({} if field.min_value is None else {"min": field.min_value}),
                    **({} if field.choices is None else {"choices": list(field.choices)}),
                }
                for key, field in fields.items()
            },
        })
    from app.pine.save import USER_DIR
    if USER_DIR.is_dir():
        for path in sorted(USER_DIR.glob("*.py")):
            if path.name == "__init__.py" or not path.stem.isidentifier():
                continue
            out.append({"name": f"user:{path.stem}", "params": {}})
    return out


def _option_fill(value: Any) -> str:
    if not isinstance(value, str):
        raise RunRequestError("option_fill must be delta_adjusted, optimistic, adverse or worst")
    try:
        return canonical_option_fill(value)
    except ValueError as exc:
        raise RunRequestError(str(exc)) from exc


def parse_config(body: dict[str, Any]) -> dict[str, Any]:
    extra = sorted(set(body) - _TOP)
    if extra:
        raise RunRequestError(f"unknown field(s) {extra}")
    strategy = body.get("strategy")
    user_strategy = isinstance(strategy, str) and strategy.startswith("user:")
    if not user_strategy and strategy not in _CATALOG:
        raise RunRequestError(f"unknown strategy {strategy!r}")
    if user_strategy:
        from app.pine.save import USER_DIR
        slug = str(strategy).split(":", 1)[1]
        if not (USER_DIR / f"{slug}.py").is_file():
            raise RunRequestError(f"unknown strategy {strategy!r}")
        fields = {}
    else:
        cls, fields = _CATALOG[strategy]
    raw_params = body.get("params", {})
    if not isinstance(raw_params, dict):
        raise RunRequestError("params must be an object")
    unknown = sorted(set(raw_params) - set(fields))
    if unknown:
        raise RunRequestError(f"unknown parameter(s) {unknown} for {strategy}")
    params: dict[str, Any] = {}
    for key, field in fields.items():
        params[key] = _coerce(key, raw_params[key], field) if key in raw_params else field.default
    if not user_strategy:
        try:
            cls(**params)
        except (TypeError, ValueError) as exc:
            raise RunRequestError(str(exc)) from exc

    symbol = body.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise RunRequestError("symbol is required")
    timeframe = body.get("timeframe")
    if timeframe not in INTRADAY_MIN:
        raise RunRequestError(f"timeframe must be one of {list(INTRADAY_MIN)}")
    start = _date("start", body.get("start"), required=True)
    end = _date("end", body.get("end"), required=False)
    if start is None:
        raise RunRequestError("start is required")
    if end is not None and end < start:
        raise RunRequestError("end is before start")
    sessions = _sessions(body.get("sessions"))
    mode = body.get("mode")
    if mode not in ("index", "options"):
        raise RunRequestError("mode must be 'index' or 'options'")
    if mode == "options" and symbol != NIFTY_SYMBOL:
        raise RunRequestError("options mode runs on NIFTY50 only")
    offset = body.get("strike_offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset not in (-1, 0, 1):
        raise RunRequestError("strike_offset must be -1, 0 or 1")
    slip = body.get("slippage_points", 0.5)
    if isinstance(slip, bool) or not isinstance(slip, (int, float)) or slip < 0:
        raise RunRequestError("slippage_points must be a number >= 0")
    option_fill = _option_fill(body.get("option_fill", "delta_adjusted"))
    live_timing = _live_timing(body.get("live_timing", True))
    premium = parse_premium_exits(body)
    if premium and mode != "options":
        raise RunRequestError("premium exits apply to options mode")
    return {
        **premium,
        "strategy": strategy,
        "params": params,
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": None if end is None else end.isoformat(),
        "sessions": sessions,
        "mode": mode,
        "strike_offset": offset,
        "slippage_points": float(slip),
        "option_fill": option_fill,
        "live_timing": live_timing,
    }


def _live_timing(value: Any) -> bool:
    """On (the default): orders work from one minute after the bar ends, as in paper. Off: from the bar's end."""
    if not isinstance(value, bool):
        raise RunRequestError("live_timing must be true or false")
    return value


def build_strategy(config: dict[str, Any]) -> Strategy:
    name = config["strategy"]
    if isinstance(name, str) and name.startswith("user:"):
        # Every mode (backtest, walk-forward, and later paper/live) loads user code in the worker.
        from app.pine.isolated import IsolatedStrategy
        from app.pine.save import USER_DIR
        slug = name.split(":", 1)[1]
        return IsolatedStrategy(USER_DIR / f"{slug}.py", **dict(config.get("params") or {}))
    cls, _fields = _CATALOG[name]
    return cls(**config["params"])


def _coerce(key: str, value: Any, field: _Field) -> Any:
    if field.kind == "bool":
        if not isinstance(value, bool):
            raise RunRequestError(f"{key} must be true or false")
        return value
    if field.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise RunRequestError(f"{key} must be a whole number")
        if field.min_value is not None and value < field.min_value:
            raise RunRequestError(f"{key} must be >= {field.min_value}")
        return value
    if field.kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RunRequestError(f"{key} must be a number")
        number = float(value)
        if field.min_value is not None and number < field.min_value:
            raise RunRequestError(f"{key} must be >= {field.min_value}")
        return number
    if not isinstance(value, str) or (field.choices is not None and value not in field.choices):
        raise RunRequestError(f"{key} must be one of {list(field.choices or ())}")
    return value


def _date(name: str, value: Any, *, required: bool) -> date | None:
    if value is None or value == "":
        if required:
            raise RunRequestError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise RunRequestError(f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RunRequestError(f"{name} must be YYYY-MM-DD") from exc


def _sessions(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RunRequestError("sessions must be a non-empty list")
    unknown = sorted(set(value) - set(SESSION_TYPES))
    if unknown:
        raise RunRequestError(f"unknown session type(s) {unknown}")
    ordered = [name for name in SESSION_TYPES if name in value or name == "normal"]
    return ordered
