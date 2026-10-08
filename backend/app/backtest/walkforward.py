"""Rolling walk-forward on options. Selection uses the train window only."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.backtest.catalog import (
    NIFTY_SYMBOL,
    PREMIUM_EXIT_KEYS,
    RunRequestError,
    _CATALOG,
    _date,
    _live_timing,
    _option_fill,
    _sessions,
    parse_premium_exits,
)
from app.backtest.curve import equity_and_drawdown
from app.backtest.holdout import assert_research_range, holdout_record, load_holdout
from app.backtest.metrics import TradeRecord
from app.backtest.result import canonical
from app.options.model import HISTORY_START

NO_CHOICE_FLAT = "no choice: best train net not positive"
NO_CHOICE_NONE = "no choice: no eligible params"
TRIED = "walk-forward tried {n} combinations"

Evaluate = Callable[[dict[str, Any], date, date], dict[str, Any]]
Report = Callable[[dict[str, Any]], None]

_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


@dataclass(frozen=True)
class Window:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def add_months(day: date, months: int) -> date:
    """Same day, `months` later. Day 31 lands on the last day of a shorter month."""
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def build_windows(
    start: date,
    research_end: date,
    train_months: int,
    test_months: int,
    step_months: int,
) -> list[Window]:
    """Train, then the following test. Step moves the next train onto the old test.

    A window is kept only when its test ends on or before `research_end`, so the
    holdout and an unfinished last slice are left out. Test windows abut when the
    step equals the test length.
    """
    if train_months < 1 or test_months < 1 or step_months < 1:
        raise ValueError("train, test and step must be at least 1 month")
    if step_months < test_months:
        raise ValueError("step must be at least the test length so test windows do not overlap")
    windows: list[Window] = []
    cursor = start
    while cursor <= research_end:
        train_end = add_months(cursor, train_months) - timedelta(days=1)
        test_start = train_end + timedelta(days=1)
        test_end = add_months(test_start, test_months) - timedelta(days=1)
        if test_end > research_end or train_end > research_end:
            break
        windows.append(Window(cursor, train_end, test_start, test_end))
        nxt = add_months(cursor, step_months)
        if nxt <= cursor:
            break
        cursor = nxt
    return windows


def pick_params(rows: list[dict[str, Any]], min_trades: int) -> dict[str, Any] | None:
    """Best eligible row. Higher score, then more trades, then smaller canonical params.

    A row is ineligible below `min_trades`, or when max drawdown is 0 and net is
    not positive. max drawdown 0 with a positive net outranks every finite score.
    """
    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for row in rows:
        score = _score(row, min_trades)
        if score is None:
            continue
        ranked.append((score, int(row["trades"]), canonical(row["params"]), row))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (
        0 if item[0] == math.inf else 1,
        0.0 if item[0] == math.inf else -float(item[0]),
        -item[1],
        item[2],
    ))
    return ranked[0][3]


def run_walk_forward(
    spec: dict[str, Any],
    evaluate: Evaluate,
    peeks: int = 0,
    report: Report | None = None,
) -> dict[str, Any]:
    """Score each train window, then run the chosen params once on that test window.

    A best eligible train net that is not positive records `NO_CHOICE_FLAT` and
    contributes no trades. Degradation is only the windows whose train
    net-per-trade is positive.
    """
    start = _as_date(spec["start"])
    research_end = _as_date(spec["research_end"])
    include_forward = bool(spec.get("include_forward", False))
    spans = [(start, research_end)]
    forward_end = spec.get("forward_end")
    if include_forward and forward_end:
        _holdout_start, holdout_end = load_holdout()
        spans.append((holdout_end + timedelta(days=1), _as_date(forward_end)))

    windows: list[Window] = []
    for span_start, span_end in spans:
        windows.extend(build_windows(
            span_start,
            span_end,
            int(spec["train_months"]),
            int(spec["test_months"]),
            int(spec["step_months"]),
        ))

    grid = [
        dict(params) for params in spec["grid"]
        if _constructs(spec["strategy"], _strategy_part(params), spec.get("timeframe"))
    ]
    tried = 0
    rows: list[dict[str, Any]] = []
    pnls: list[float] = []
    degradation: list[dict[str, Any]] = []
    total = len(windows)
    for index, window in enumerate(windows, start=1):
        assert_research_range(window.train_start, window.train_end, include_forward=include_forward)
        assert_research_range(window.test_start, window.test_end, include_forward=include_forward)
        evaluated: list[dict[str, Any]] = []
        for combo, params in enumerate(grid, start=1):
            if report is not None:
                report({
                    "phase": f"window {index} of {total}, combo {combo} of {len(grid)}",
                    "done": combo,
                    "total": len(grid),
                })
            outcome = evaluate(params, window.train_start, window.train_end)
            tried += 1
            evaluated.append({**outcome, "params": params})
        best = pick_params(evaluated, int(spec["min_trades"]))
        if best is None:
            rows.append(_flat_row(index, window, NO_CHOICE_NONE, None))
            continue
        if float(best["net_pnl"]) <= 0:
            rows.append(_flat_row(index, window, NO_CHOICE_FLAT, best))
            continue
        test = evaluate(best["params"], window.test_start, window.test_end)
        train_net = float(best["net_pnl"])
        train_trades = int(best["trades"])
        test_net = float(test["net_pnl"])
        test_trades = int(test["trades"])
        rows.append({
            "window": index,
            "train_start": window.train_start.isoformat(),
            "train_end": window.train_end.isoformat(),
            "test_start": window.test_start.isoformat(),
            "test_end": window.test_end.isoformat(),
            "params": best["params"],
            "reason": None,
            "train": {"net_pnl": train_net, "max_drawdown": float(best["max_drawdown"]), "trades": train_trades},
            "test": {"net_pnl": test_net, "max_drawdown": float(test["max_drawdown"]), "trades": test_trades, "flat": False},
        })
        pnls.extend(float(p) for p in test.get("pnls", []))
        train_npt = train_net / train_trades
        if train_npt > 0:
            test_npt = (test_net / test_trades) if test_trades else 0.0
            degradation.append({
                "window": index,
                "train_net_per_trade": train_npt,
                "test_net_per_trade": test_npt,
                "ratio": test_npt / train_npt,
            })

    curve = equity_and_drawdown([TradeRecord(i, i, pnl) for i, pnl in enumerate(pnls)])
    net = float(curve[-1]["equity"]) if curve else 0.0
    drawdown = max((float(point["drawdown"]) for point in curve), default=0.0)
    wins = sum(1 for pnl in pnls if pnl > 0)
    return {
        "kind": "walk_forward",
        "holdout": holdout_record(int(peeks)),
        "windows": rows,
        "degradation": degradation,
        "param_changes": _param_changes(rows),
        "combinations_tried": tried,
        "warnings": [TRIED.format(n=tried)],
        "summary": {
            "index": _empty_side(),
            "option": {
                "net_pnl": net,
                "trades": len(pnls),
                "win_rate": (wins / len(pnls)) if pnls else None,
                "max_drawdown": drawdown,
                "gross_pnl": None,
                "total_charges": None,
                "total_slippage": None,
            },
        },
        "equity": {"index": [], "option": curve},
        "trades": [],
        "breakdowns": {"weekday": {}, "time_of_day": {}, "dte": {}, "flags": {}},
        "mode": "options",
    }


def run_holdout(spec: dict[str, Any], evaluate: Evaluate, peeks: Any) -> dict[str, Any]:
    """One frozen config on the holdout dates. The peek is recorded before `evaluate`."""
    start, end = load_holdout()
    count = int(peeks.accept())
    outcome = evaluate(spec["params"], start, end)
    return {
        "kind": "holdout",
        "holdout": holdout_record(count),
        "params": spec["params"],
        "summary": {
            "index": _empty_side(),
            "option": {
                "net_pnl": float(outcome["net_pnl"]),
                "trades": int(outcome["trades"]),
                "win_rate": None,
                "max_drawdown": float(outcome["max_drawdown"]),
                "gross_pnl": None,
                "total_charges": None,
                "total_slippage": None,
            },
        },
    }


def last_chosen_params(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    chosen: dict[str, Any] | None = None
    for row in result.get("windows") or []:
        if isinstance(row, dict) and row.get("params"):
            chosen = dict(row["params"])
    return chosen


def parse_walk_forward(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "kind", "strategy", "symbol", "timeframe", "start", "end", "sessions",
        "mode", "strike_offset", "slippage_points", "option_fill", "live_timing", "train_months", "test_months",
        "step_months", "min_trades", "max_combinations", "include_forward", "grid",
    }
    extra = sorted(set(body) - allowed)
    if extra:
        raise RunRequestError(f"unknown field(s) {extra}")
    if body.get("kind") not in (None, "walk_forward"):
        raise RunRequestError("kind must be walk_forward")
    strategy = body.get("strategy")
    if strategy not in _CATALOG:
        raise RunRequestError(f"unknown strategy {strategy!r}")
    symbol = body.get("symbol", NIFTY_SYMBOL)
    if symbol != NIFTY_SYMBOL:
        raise RunRequestError("walk-forward runs on NIFTY50 only")
    mode = body.get("mode", "options")
    if mode != "options":
        raise RunRequestError("walk-forward runs in options mode")
    timeframe = body.get("timeframe", "5m")
    if timeframe not in _TF_MIN:
        raise RunRequestError(f"timeframe must be one of {list(_TF_MIN)}")
    start = _date("start", body.get("start"), required=True)
    if start is None or start < HISTORY_START:
        raise RunRequestError(f"start must be on or after {HISTORY_START.isoformat()}")
    end = _date("end", body.get("end"), required=False)
    holdout_start, holdout_end = load_holdout()
    if end is not None and end >= holdout_start:
        raise RunRequestError(
            f"end must be before the fixed holdout {holdout_start.isoformat()}..{holdout_end.isoformat()}"
        )
    research_end = (holdout_start - timedelta(days=1)) if end is None else end
    if research_end < start:
        raise RunRequestError("end is before start")
    train_months = _whole("train_months", body.get("train_months", 6), minimum=1)
    test_months = _whole("test_months", body.get("test_months", 2), minimum=1)
    step_months = _whole("step_months", body.get("step_months", 2), minimum=1)
    if step_months < test_months:
        raise RunRequestError("step_months must be at least test_months so test windows do not overlap")
    min_trades = _whole("min_trades", body.get("min_trades", 30), minimum=1)
    max_combinations = _whole("max_combinations", body.get("max_combinations", 50), minimum=1)
    include_forward = body.get("include_forward", _file_include_forward())
    if not isinstance(include_forward, bool):
        raise RunRequestError("include_forward must be true or false")
    offset = body.get("strike_offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset not in (-1, 0, 1):
        raise RunRequestError("strike_offset must be -1, 0 or 1")
    slip = body.get("slippage_points", 1.0)
    if isinstance(slip, bool) or not isinstance(slip, (int, float)) or slip < 0:
        raise RunRequestError("slippage_points must be a number >= 0")
    option_fill = _option_fill(body.get("option_fill", "delta_adjusted"))
    live_timing = _live_timing(body.get("live_timing", True))
    sessions = _sessions(body.get("sessions", ["normal", "weekend_full"]))
    grid = _expand_grid(strategy, body.get("grid"), timeframe, max_combinations)
    windows = build_windows(start, research_end, train_months, test_months, step_months)
    if not windows:
        raise RunRequestError("that range fits no full train/test window")
    _cls, fields = _CATALOG[strategy]
    defaults = {key: field.default for key, field in fields.items()}
    return {
        "kind": "walk_forward",
        "strategy": strategy,
        "params": defaults,
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": research_end.isoformat(),
        "research_end": research_end.isoformat(),
        "sessions": sessions,
        "mode": "options",
        "strike_offset": offset,
        "slippage_points": float(slip),
        "option_fill": option_fill,
        "live_timing": live_timing,
        "train_months": train_months,
        "test_months": test_months,
        "step_months": step_months,
        "min_trades": min_trades,
        "max_combinations": max_combinations,
        "include_forward": include_forward,
        "grid": grid,
        "holdout": {"start": holdout_start.isoformat(), "end": holdout_end.isoformat()},
    }


def parse_holdout(body: dict[str, Any]) -> dict[str, Any]:
    """One explicit parameter set. Dates are the frozen holdout, not the request."""
    from app.backtest.catalog import parse_config

    holdout_start, holdout_end = load_holdout()
    cleaned = {key: value for key, value in body.items() if key not in {"kind", "from_run"}}
    cleaned["mode"] = "options"
    cleaned["start"] = holdout_start.isoformat()
    cleaned["end"] = holdout_end.isoformat()
    cleaned.setdefault("symbol", NIFTY_SYMBOL)
    cleaned.setdefault("timeframe", "5m")
    cleaned.setdefault("sessions", ["normal", "weekend_full"])
    cleaned.setdefault("strike_offset", 0)
    cleaned.setdefault("slippage_points", 1.0)
    config = parse_config(cleaned)
    if config["symbol"] != NIFTY_SYMBOL:
        raise RunRequestError("the holdout runs on NIFTY50 only")
    config["kind"] = "holdout"
    config["start"] = holdout_start.isoformat()
    config["end"] = holdout_end.isoformat()
    return config


def default_grid(strategy: str) -> list[dict[str, Any]]:
    if strategy == "ema_crossover":
        pairs = (
            {"fast": fast, "slow": slow, "mode": "long_short", "lots": 1}
            for fast in (5, 9, 12)
            for slow in (15, 21, 34)
            if fast < slow
        )
        return list(pairs)
    if strategy == "supertrend_flip":
        return [
            {"atr_length": length, "multiplier": float(mult), "mode": "long_short", "lots": 1}
            for length in (7, 10, 14)
            for mult in (2, 3, 4)
        ]
    if strategy == "opening_range_breakout":
        return [{"range_minutes": minutes, "lots": 1} for minutes in (5, 15, 30)]
    if strategy == "pivot_extension":
        return [
            {"variant": variant, "timeframe": timeframe}
            for variant in ("faithful", "carried_pivots")
            for timeframe in ("5m", "15m")
        ]
    if strategy == "log_xz":
        return [
            {"z_length": length, "timeframe": timeframe}
            for length in (10, 14)
            for timeframe in ("5m", "15m")
        ]
    if strategy == "price_channel":
        return [
            {"length": length, "timeframe": timeframe}
            for length in (20, 40)
            for timeframe in ("5m", "15m")
        ]
    raise RunRequestError(f"unknown strategy {strategy!r}")


def split_timeframe(params: dict[str, Any], default: str) -> tuple[dict[str, Any], str]:
    """A grid may carry `timeframe`. It selects the child run's bars, not a strategy argument."""
    params = dict(params)
    timeframe = str(params.pop("timeframe", default))
    return params, timeframe


def child_backtest_config(config: dict[str, Any], params: dict[str, Any], start: date, end: date) -> dict[str, Any]:
    """One walk-forward combination. `timeframe` in the combo overrides the request; premium exits go to the
    option overlay of the child run."""
    strategy_params, timeframe = split_timeframe(params, config["timeframe"])
    premium = {key: strategy_params.pop(key) for key in PREMIUM_EXIT_KEYS if key in strategy_params}
    return {
        **{key: value for key, value in premium.items() if value is not None},
        "strategy": config["strategy"],
        "params": strategy_params,
        "symbol": config["symbol"],
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": config["sessions"],
        "mode": "options",
        "strike_offset": config["strike_offset"],
        "slippage_points": config["slippage_points"],
        "option_fill": config.get("option_fill", "delta_adjusted"),
        "live_timing": bool(config.get("live_timing", True)),
    }


def _expand_grid(strategy: str, grid: Any, timeframe: str, max_combinations: int) -> list[dict[str, Any]]:
    if grid is None:
        combos = default_grid(strategy)
    elif isinstance(grid, list):
        combos = [dict(item) for item in grid]
    elif isinstance(grid, dict):
        keys = list(grid)
        values: list[list[Any]] = []
        total = 1
        for key in keys:
            raw = grid[key]
            column = list(raw) if isinstance(raw, list) else [raw]
            values.append(column)
            total *= len(column)
            if total > max_combinations:
                raise RunRequestError(f"grid has more than {max_combinations} combinations")
        combos = [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*values)]
    else:
        raise RunRequestError("grid must be a list of parameter sets or an object of lists")
    if len(combos) > max_combinations:
        raise RunRequestError(f"grid has more than {max_combinations} combinations")
    valid = []
    for params in combos:
        raw = dict(params)
        chosen = raw.pop("timeframe", None)
        premium_raw = {key: raw.pop(key) for key in PREMIUM_EXIT_KEYS if key in raw}
        try:
            premium = parse_premium_exits(premium_raw)
        except RunRequestError:
            continue
        full = _complete(strategy, raw)
        check = timeframe if chosen is None else str(chosen)
        if full is not None and _constructs(strategy, full, check):
            if chosen is not None:
                full["timeframe"] = chosen
            full.update(premium)
            valid.append(full)
    if not valid:
        raise RunRequestError("grid has no valid combinations")
    return valid


def _complete(strategy: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if strategy not in _CATALOG:
        return dict(params)
    _cls, fields = _CATALOG[strategy]
    unknown = sorted(set(params) - set(fields))
    if unknown:
        return None
    full = {key: field.default for key, field in fields.items()}
    full.update(params)
    if "lots" in fields:
        full["lots"] = 1
    if "mode" in fields:
        full["mode"] = "long_short"
    return full


def _strategy_part(params: dict[str, Any]) -> dict[str, Any]:
    """A combo without its option-overlay settings (premium exits), which are not strategy arguments."""
    return {key: value for key, value in params.items() if key not in PREMIUM_EXIT_KEYS}


def _constructs(strategy: str, params: dict[str, Any], timeframe: str | None) -> bool:
    params = {key: value for key, value in params.items() if key != "timeframe"}
    if strategy not in _CATALOG:
        return True
    cls, fields = _CATALOG[strategy]
    if set(params) - set(fields):
        return False
    try:
        cls(**params)
    except (TypeError, ValueError):
        return False
    if strategy == "opening_range_breakout" and timeframe is not None:
        minutes = int(params["range_minutes"])
        tf = _TF_MIN.get(timeframe)
        if tf is None or tf > minutes or minutes % tf:
            return False
    return True


def _score(row: dict[str, Any], min_trades: int) -> float | None:
    trades = int(row["trades"])
    net = float(row["net_pnl"])
    drawdown = float(row["max_drawdown"])
    if trades < min_trades or drawdown < 0:
        return None
    if drawdown == 0:
        return math.inf if net > 0 else None
    return net / drawdown


def _flat_row(index: int, window: Window, reason: str, best: dict[str, Any] | None) -> dict[str, Any]:
    train = None if best is None else {
        "net_pnl": float(best["net_pnl"]),
        "max_drawdown": float(best["max_drawdown"]),
        "trades": int(best["trades"]),
    }
    return {
        "window": index,
        "train_start": window.train_start.isoformat(),
        "train_end": window.train_end.isoformat(),
        "test_start": window.test_start.isoformat(),
        "test_end": window.test_end.isoformat(),
        "params": None,
        "reason": reason,
        "train": train,
        "test": {"net_pnl": 0.0, "max_drawdown": 0.0, "trades": 0, "flat": True},
    }


def _param_changes(rows: Iterable[dict[str, Any]]) -> int:
    changes = 0
    previous: str | None = None
    for row in rows:
        params = row.get("params")
        if not params:
            continue
        key = canonical(params)
        if previous is not None and key != previous:
            changes += 1
        previous = key
    return changes


def _empty_side() -> dict[str, Any]:
    return {
        "net_pnl": 0.0,
        "points": 0.0,
        "trades": 0,
        "win_rate": None,
        "max_drawdown": 0.0,
        "gross_pnl": 0.0,
        "total_charges": 0.0,
        "total_slippage": 0.0,
    }


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _whole(name: str, value: Any, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunRequestError(f"{name} must be a whole number")
    if value < minimum:
        raise RunRequestError(f"{name} must be >= {minimum}")
    return value


def _file_include_forward() -> bool:
    import json

    from app.backtest.holdout import HOLDOUT_PATH

    raw = json.loads(HOLDOUT_PATH.read_text())
    return bool(raw.get("include_forward_period", False))
