"""Backtest instrument modes (Phase B).

Modes:
  index_option   — existing Nifty/Sensex options overlay (`options` alias kept)
  equity_intraday — cash equity MIS, 1 share lot, equity cost table
  stock_future    — single-stock futures (lot multiples), futures cost table
  index_future    — index futures, futures cost table
  index           — legacy index-only (zero costs; unchanged)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.backtest.catalog import NIFTY_SYMBOL, OPTIONS_SYMBOLS, SENSEX_SYMBOL
from app.backtest.costs import CostModel, CostTable, get_cost_model
from app.backtest.sizing import RiskSizing

INSTRUMENT_MODES = (
    "index_option",
    "options",  # alias of index_option
    "index",
    "equity_intraday",
    "stock_future",
    "index_future",
)

EQUITY_FUTURE_MODES = frozenset({"equity_intraday", "stock_future", "index_future"})
OPTIONS_MODES = frozenset({"index_option", "options"})

DATA_DIR = Path(__file__).resolve().parent / "data"
EQUITY_INTRADAY_COST_FILE = DATA_DIR / "cost_rates_equity_intraday.json"
FUTURES_COST_FILE = DATA_DIR / "cost_rates_futures.json"

DEFAULT_RISK_RUPEES = 300.0
DEFAULT_NOTIONAL_CAP = 75_000.0
DEFAULT_SLIPPAGE_TICKS = 1.0


def normalize_mode(mode: str) -> str:
    if mode == "options":
        return "index_option"
    return mode


def is_options_mode(mode: str) -> bool:
    return normalize_mode(mode) == "index_option"


def is_cash_or_future_mode(mode: str) -> bool:
    return normalize_mode(mode) in EQUITY_FUTURE_MODES


def cost_model_for(mode: str) -> CostModel:
    m = normalize_mode(mode)
    if m == "index_option":
        return get_cost_model("options")
    if m == "equity_intraday":
        return CostModel("equity_intraday", CostTable.load(EQUITY_INTRADAY_COST_FILE))
    if m in ("stock_future", "index_future"):
        return CostModel("futures", CostTable.load(FUTURES_COST_FILE))
    if m == "index":
        return CostModel.zero()
    raise ValueError(f"unknown instrument mode {mode!r}")


def default_tick(mode: str, symbol: str) -> float:
    """NSE tick used for slippage-in-ticks → points."""
    m = normalize_mode(mode)
    if m == "index_future" or symbol in (NIFTY_SYMBOL, "BANKNIFTY", SENSEX_SYMBOL):
        return 0.05 if symbol != SENSEX_SYMBOL else 0.05
    if m == "stock_future" or m == "equity_intraday":
        return 0.05  # most large-cap NSE ticks; override via config when needed
    return 0.05


def risk_sizing_for(mode: str, *, risk_rupees: float, notional_cap: float, lot_size: int) -> RiskSizing | None:
    if not is_cash_or_future_mode(mode):
        return None
    return RiskSizing(risk_rupees=risk_rupees, notional_cap=notional_cap, lot_size=lot_size)


def resolve_candle_symbol(trading_symbol: str, *, candles_dir: Path, resolved_universe: dict[str, Any] | None = None) -> str:
    """Map a trading symbol (HDFCBANK) or alias (BANKNIFTY, NIFTY50) to a candle folder name."""
    if (candles_dir / trading_symbol).is_dir():
        return trading_symbol
    if resolved_universe:
        for row in resolved_universe.get("resolved") or []:
            if row.get("symbol") == trading_symbol or row.get("folder") == trading_symbol:
                folder = str(row["folder"])
                if (candles_dir / folder).is_dir():
                    return folder
    # research universe file
    from app.research.universe import DATA_FILE, load_universe_spec, resolve_universe
    from app.upstox.instruments import current_index
    from app.config import settings

    # Prefer committed resolved snapshot when present
    snap = DATA_FILE.with_name("research_universe_v1_resolved.json")
    if snap.is_file():
        import json
        body = json.loads(snap.read_text(encoding="utf-8"))
        for row in body.get("resolved") or []:
            if row.get("symbol") == trading_symbol:
                return str(row["folder"])
    idx = current_index(settings.instruments_dir)
    if idx is not None:
        res = resolve_universe(idx, spec=load_universe_spec())
        for m in res.resolved:
            if m.symbol == trading_symbol:
                return m.folder
    raise KeyError(f"no candle folder for symbol {trading_symbol!r}")
