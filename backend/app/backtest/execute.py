"""Run one saved config on the candle store and package the result the UI shows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.breakdowns import dte_breakdown, flag_breakdown
from app.backtest.catalog import NIFTY_SYMBOL, build_strategy
from app.backtest.costs import get_cost_model, load_default_cost_table
from app.backtest.curve import equity_and_drawdown
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.expiry import load_default_calendar
from app.backtest.holdout import holdout_record, load_holdout, resolve_period
from app.backtest.lots import load_default_lot_table
from app.backtest.metrics import TradeRecord
from app.backtest.result import BacktestResult, Trade, digest
from app.backtest.runs import RunStore
from app.backtest.sources import StoreSource
from app.backtest.walkforward import child_backtest_config, run_walk_forward
from app.config import settings
from app.data.store import CandleStore
from app.options.history import default_history_store
from app.options.model import OptionModelConfig, canonical_option_fill, load_option_model, overlay_options

IST = timezone(timedelta(hours=5, minutes=30))
VIX_SYMBOL = "NSE_INDEX_India_VIX"
Report = Callable[[dict[str, Any]], None]


def execute_run(config: dict[str, Any], report: Report, *, hash_data: bool = True) -> dict[str, Any]:
    if config.get("kind") == "walk_forward":
        return execute_walk_forward(config, report)
    if config.get("kind") == "holdout":
        return execute_holdout(config, report)
    strategy = build_strategy(config)
    source = StoreSource(CandleStore(settings.candles_dir), config["symbol"])
    engine_cfg = _engine_config(config)

    def on_bar(done: int, total: int) -> None:
        report({"phase": "index", "done": done, "total": total})

    result = run_backtest(strategy, source, engine_cfg, on_progress=on_bar)
    option = None
    vix_bars: list[dict[str, Any]] = []
    if config["mode"] == "options":
        nifty, vix_bars = _load_underlyings(config)
        model = _model(config)
        report({"phase": "overlay", "done": 0, "total": len(result.trades)})

        def on_trade(done: int, total: int) -> None:
            if done == 1 or done == total or done % 25 == 0:
                report({"phase": "overlay", "done": done, "total": total})

        option = overlay_options(result, nifty, vix_bars, config=model, on_trade=on_trade)
        report({"phase": "overlay", "done": len(result.trades), "total": len(result.trades)})

    view = _view(result, option, config)
    view["holdout"] = holdout_record(_peek_count())
    warnings = _warnings(result, option)
    return {
        "run_id": result.run_id,
        "overlay_run_id": None if option is None else option.run_id,
        "model_version": None if option is None else option.option.settings.get("model_version"),
        "cost_rows": [] if option is None else _cost_rows(option.option.trades),
        "data_hash": _data_hash(result, vix_bars, config) if hash_data else None,
        "warnings": warnings,
        "result": view,
    }


def execute_walk_forward(config: dict[str, Any], report: Report) -> dict[str, Any]:
    """One saved walk-forward. Inner runs skip the per-combo file hash; the research hash is once."""
    spec = dict(config)
    spec["start"] = date.fromisoformat(config["start"])
    spec["research_end"] = date.fromisoformat(config["research_end"])
    if config.get("include_forward"):
        _first, last = CandleStore(settings.candles_dir).time_range(config["symbol"])
        last_session = datetime.fromtimestamp(last, IST).date()
        period = resolve_period(last_session, include_forward=True)
        spec["forward_end"] = period.forward_end
    peeks = _peek_count()

    def evaluate(params: dict[str, Any], start: date, end: date) -> dict[str, Any]:
        child = child_backtest_config(config, params, start, end)
        payload = execute_run(child, report, hash_data=False)
        view = payload["result"]
        option = view["summary"]["option"]
        ordered = sorted(view["trades"], key=lambda row: (row["exit_time"], row["entry_time"]))
        pnls = [float(row["option"]["net_pnl"]) for row in ordered if row.get("option")]
        return {
            "net_pnl": float(option["net_pnl"]),
            "max_drawdown": float(option["max_drawdown"]),
            "trades": int(option["trades"]),
            "pnls": pnls,
        }

    result = run_walk_forward(spec, evaluate, peeks=peeks, report=report)
    research_end = date.fromisoformat(config["research_end"])
    return {
        "run_id": digest({
            "kind": "walk_forward",
            "config": {key: config[key] for key in ("strategy", "grid", "start", "research_end", "slippage_points")},
            "windows": result["windows"],
            "summary": result["summary"]["option"],
        }),
        "overlay_run_id": None,
        "model_version": _shipped_model_version(),
        "cost_rows": _range_cost_rows(date.fromisoformat(config["start"]), research_end),
        "data_hash": research_data_hash(config),
        "warnings": list(result["warnings"]),
        "result": result,
    }


def execute_holdout(config: dict[str, Any], report: Report) -> dict[str, Any]:
    """Peek first, then one frozen run. A failure still leaves the peek recorded."""
    start, end = load_holdout()
    RunStore(settings.data_dir / "backtests.sqlite").accept_holdout(start.isoformat(), end.isoformat())
    child = {key: value for key, value in config.items() if key != "kind"}
    child["start"] = start.isoformat()
    child["end"] = end.isoformat()
    payload = execute_run(child, report)
    payload["result"]["kind"] = "holdout"
    return payload


def _engine_config(config: dict[str, Any]) -> BacktestConfig:
    nifty = config["symbol"] == NIFTY_SYMBOL
    calendar = load_default_calendar()
    return BacktestConfig(
        timeframe=config["timeframe"],
        start=config["start"],
        end=config["end"],
        session_types=tuple(config["sessions"]),
        underlying="NIFTY" if nifty else None,
        lot_table=load_default_lot_table() if nifty else None,
        lot_size=None if nifty else 1,
        contract=(lambda day: (calendar.next_expiry(day, "weekly").date, "weekly")) if nifty else None,
        cost_model=get_cost_model("zero"),
    )


def _model(config: dict[str, Any]) -> OptionModelConfig:
    shipped = load_option_model()
    return OptionModelConfig(
        underlying=shipped.underlying,
        strike_offset=int(config["strike_offset"]),
        cycle=shipped.cycle,
        roll_on_expiry_day=shipped.roll_on_expiry_day,
        r=shipped.r,
        q=shipped.q,
        vix_scale=shipped.vix_scale,
        time_basis=shipped.time_basis,
        slippage_points=float(config["slippage_points"]),
        option_fill=canonical_option_fill(str(config.get("option_fill", "delta_adjusted"))),
        tick=shipped.tick,
        min_premium=shipped.min_premium,
        snap_tick=shipped.snap_tick,
        vix_stale_seconds=shipped.vix_stale_seconds,
        gap_pct=shipped.gap_pct,
        calibration_ref=shipped.calibration_ref,
        rates_note=shipped.rates_note,
        model_version=shipped.model_version,
        model_as_of=shipped.model_as_of,
        carry_basis=shipped.carry_basis,
        real_premiums=shipped.real_premiums,
        vix_scales=shipped.vix_scales,
    )


def _load_underlyings(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start = date.fromisoformat(config["start"])
    from_time = int(datetime(start.year, start.month, start.day, tzinfo=IST).timestamp()) - 2 * 86400
    to_time = None
    if config["end"]:
        end = date.fromisoformat(config["end"]) + timedelta(days=1)
        to_time = int(datetime(end.year, end.month, end.day, tzinfo=IST).timestamp())
    store = CandleStore(settings.candles_dir)
    nifty, _ = store.load(NIFTY_SYMBOL, from_time=from_time, to_time=to_time, session_types=config["sessions"])
    vix, _ = store.load(VIX_SYMBOL, from_time=from_time, to_time=to_time, session_types=config["sessions"])
    return list(nifty), list(vix)


def _view(result: BacktestResult, option: Any, config: dict[str, Any]) -> dict[str, Any]:
    opt_rows = [] if option is None else option.option.trades
    by_id = {row["index_trade_id"]: row for row in opt_rows}
    trades = [_trade(trade, by_id.get(trade.id)) for trade in result.trades]
    option_records = [TradeRecord(row["entry_time"], row["exit_time"], row["net_pnl"]) for row in opt_rows]
    index_records = [TradeRecord(t.entry_time, t.exit_time, t.net_pnl) for t in result.trades]
    summary_option = None
    if option is not None:
        metrics = option.option.metrics
        source = option.option.premium_source
        summary_option = {
            "net_pnl": metrics["net_pnl"],
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "max_drawdown": metrics["max_drawdown"],
            "gross_pnl": metrics.get("gross_pnl"),
            "total_charges": metrics.get("total_charges"),
            "total_slippage": metrics.get("total_slippage"),
            "real_fills": option.option.counters.get("real_fills", 0),
            "modelled_fills": option.option.counters.get("modelled_fills", 0),
            "real_pnl": source.get("real", {}).get("net_pnl"),
            "modelled_pnl": source.get("modelled", {}).get("net_pnl"),
            "real_trades": source.get("real", {}).get("trades"),
            "modelled_trades": source.get("modelled", {}).get("trades"),
            "option_fill": canonical_option_fill(str(config.get("option_fill", "delta_adjusted"))),
        }
    chosen = opt_rows if option is not None else [
        {"entry_time": t.entry_time, "net_pnl": t.net_pnl, "flags": []} for t in result.trades
    ]
    metrics_for_split = option.option.metrics if option is not None else result.metrics
    return {
        "summary": {"index": _index_summary(result), "option": summary_option},
        "equity": {
            "index": equity_and_drawdown(index_records),
            "option": equity_and_drawdown(option_records),
        },
        "trades": trades,
        "breakdowns": {
            "weekday": metrics_for_split["pnl_by_weekday"],
            "time_of_day": metrics_for_split["pnl_by_time_of_day"],
            "dte": dte_breakdown(opt_rows) if option is not None else {},
            "flags": flag_breakdown(chosen) if option is not None else {},
        },
        "mode": config["mode"],
    }


def _index_summary(result: BacktestResult) -> dict[str, Any]:
    metrics = result.metrics
    points = 0.0
    for trade in result.trades:
        sign = 1.0 if trade.direction == "LONG" else -1.0
        points += (trade.exit_price - trade.entry_price) * sign * trade.lots
    return {
        "net_pnl": metrics["net_pnl"],
        "points": round(points, 2),
        "trades": metrics["trades"],
        "win_rate": metrics["win_rate"],
        "max_drawdown": metrics["max_drawdown"],
        "gross_pnl": metrics.get("gross_pnl"),
        "total_charges": metrics.get("total_charges"),
        "total_slippage": metrics.get("total_slippage"),
    }


def _trade(trade: Trade, option: dict[str, Any] | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": trade.id,
        "direction": trade.direction,
        "entry_time": trade.entry_time,
        "exit_time": trade.exit_time,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "lots": trade.lots,
        "lot_size": trade.lot_size,
        "gross_pnl": trade.gross_pnl,
        "charges": trade.charges,
        "charges_total": trade.charges_total,
        "slippage_cost": trade.slippage_cost,
        "net_pnl": trade.net_pnl,
        "exit_reason": trade.exit_reason,
        "entry_tag": trade.entry_tag,
        "exit_tag": trade.exit_tag,
    }
    if option is not None:
        row["option"] = {
            "contract": option["contract"],
            "entry_premium": option["entry_premium"],
            "exit_premium": option["exit_premium"],
            "entry_fill": option["entry_fill"],
            "exit_fill": option["exit_fill"],
            "entry_source": option["entry_source"],
            "exit_source": option["exit_source"],
            "gross_pnl": option["gross_pnl"],
            "charges_buy": option["charges_buy"],
            "charges_sell": option["charges_sell"],
            "charges_total": option["charges_total"],
            "slippage_cost": option["slippage_cost"],
            "net_pnl": option["net_pnl"],
            "flags": option["flags"],
            "dte": option.get("dte"),
        }
    return row


def _warnings(result: BacktestResult, option: Any) -> list[str]:
    seen: list[str] = []
    for item in result.warnings:
        if item not in seen:
            seen.append(item)
    if option is not None:
        for item in option.option.warnings:
            if item not in seen:
                seen.append(item)
    return seen


def _cost_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trades:
        return []
    table = load_default_cost_table()
    days: set[date] = set()
    for trade in trades:
        days.add(date.fromisoformat(trade["entry_date"]))
        days.add(date.fromisoformat(trade["exit_date"]))
    used = {table.row_for(day).effective_from for day in days}
    return [row for row in table.to_dict()["rows"] if date.fromisoformat(row["effective_from"]) in used]


def _data_hash(result: BacktestResult, vix_bars: list[dict[str, Any]], config: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(str(result.data.get("sha256", "")).encode())
    if vix_bars:
        arr = np.array([[b["time"], b["close"]] for b in vix_bars], dtype=np.float64)
        digest.update(arr.tobytes())
    if config["mode"] == "options":
        for path in _option_files(config):
            digest.update(path.name.encode())
            if path.exists():
                digest.update(path.read_bytes())
            else:
                digest.update(b"missing")
    return digest.hexdigest()


def research_data_hash(config: dict[str, Any]) -> str:
    """Hash candles and option files inside the research span. Holdout files are not opened."""
    start = date.fromisoformat(config["start"])
    end = date.fromisoformat(config["research_end"])
    digest_ = hashlib.sha256()
    store = CandleStore(settings.candles_dir)
    from_time = int(datetime(start.year, start.month, start.day, tzinfo=IST).timestamp()) - 2 * 86400
    to_time = int(datetime(end.year, end.month, end.day, tzinfo=IST).timestamp()) + 86400
    for symbol in (NIFTY_SYMBOL, VIX_SYMBOL):
        bars, _minutes = store.load(symbol, from_time=from_time, to_time=to_time, session_types=config["sessions"])
        if bars:
            arr = np.array([[b["time"], b["close"]] for b in bars], dtype=np.float64)
            digest_.update(symbol.encode())
            digest_.update(arr.tobytes())
    for path in research_option_files(start, end):
        digest_.update(path.name.encode())
        if path.exists():
            digest_.update(path.read_bytes())
        else:
            digest_.update(b"missing")
    return digest_.hexdigest()


def research_option_files(start: date, end: date, root: Path | None = None) -> list[Path]:
    """Option files whose expiry falls in the research span. Nothing after `end`."""
    base = default_history_store().base_dir if root is None else root
    out: list[Path] = []
    if base.is_dir():
        for path in sorted(base.glob("NIFTY_*.parquet")):
            try:
                expiry = date.fromisoformat(path.stem[6:])
            except ValueError:
                continue
            if start - timedelta(days=7) <= expiry <= end:
                out.append(path)
    if root is None:
        missing = base / "NIFTY_2024-12-26.parquet"
        if start <= date(2024, 12, 26) <= end and missing not in out:
            out.append(missing)
    return out


def _peek_count() -> int:
    start, end = load_holdout()
    return RunStore(settings.data_dir / "backtests.sqlite").holdout_peeks(start.isoformat(), end.isoformat())


def _shipped_model_version() -> str | None:
    from app.options.model import load_option_model

    return load_option_model().model_version


def _range_cost_rows(start: date, end: date) -> list[dict[str, Any]]:
    rows = load_default_cost_table().to_dict()["rows"]
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        effective = date.fromisoformat(row["effective_from"])
        nxt = date.fromisoformat(rows[index + 1]["effective_from"]) if index + 1 < len(rows) else date.max
        if effective <= end and start < nxt:
            out.append(row)
    return out


def _option_files(config: dict[str, Any]) -> list[Path]:
    start = date.fromisoformat(config["start"])
    end = date.fromisoformat(config["end"]) if config["end"] else date.today()
    root = default_history_store().base_dir
    out: list[Path] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("NIFTY_*.parquet")):
        try:
            expiry = date.fromisoformat(path.stem[6:])
        except ValueError:
            continue
        if start - timedelta(days=7) <= expiry <= end + timedelta(days=14):
            out.append(path)
    missing = root / "NIFTY_2024-12-26.parquet"
    if start <= date(2024, 12, 26) <= end + timedelta(days=14) and missing not in out:
        out.append(missing)
    return out
