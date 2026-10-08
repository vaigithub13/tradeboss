"""Map each index trade onto an estimated long option (CE for LONG, PE for SHORT)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Protocol

from app.backtest.costs import CostModel, get_cost_model
from app.backtest.expiry import ExpiryCalendar, load_default_calendar
from app.backtest.lots import LotSizeTable, load_default_lot_table
from app.backtest.metrics import TradeRecord, compute_metrics
from app.backtest.result import BacktestResult, Trade
from app.backtest.sources import ist_date
from app.data.resampler import DAY_S, IST_OFFSET_S
from app.options.bs import PricingError, bs_forward_delta, bs_forward_price, snap_premium


class OptionOverlayError(ValueError):
    """The trade is not one entry fill and one exit fill, so it cannot be priced as one option."""
from app.options.contract import OptionContract, choose_contract
from app.options.events import EventCalendar, load_default_events
from app.options.history import default_history_store
from app.options.result import EstimatedResult, OptionEstimate, estimated_run_id
from app.options.strikes import StrikeStepTable, StrikeStepUnknown, load_default_step_table
from app.options.time import years_to_expiry
from app.options.vix import vix_asof

CENT = Decimal("0.01")
ESTIMATED = "ESTIMATED"
CLOSE_MIN = 15 * 60 + 30
OPEN_MIN = 9 * 60 + 15
HISTORY_START = date(2024, 10, 3)
MODEL_ONLY_WARNING = "model only: rough check, not proof"
MODELLED_FILL_WARNING = "more than 20% of fills are modelled"
DTE_BUCKETS = ("0", "1", "2", "3-4", "5+")
OPTION_FILLS = ("delta_adjusted", "optimistic", "adverse", "worst")
OPTIMISTIC_FILL_WARNING = (
    "option fill is optimistic: a stop is priced at the minute's option open, before the breakout"
)
DEFAULT_MODEL_FILE = Path(__file__).resolve().parent / "data" / "option_model_v1.json"


class PremiumTape(Protocol):
    def premium_at(self, expiry: date, strike: float, kind: str, time: int) -> float | None: ...


def canonical_option_fill(value: str) -> str:
    """`minute_open` is the old name of the optimistic fill."""
    if value == "minute_open":
        value = "optimistic"
    if value not in OPTION_FILLS:
        raise ValueError(f"option_fill must be one of {OPTION_FILLS}")
    return value


def delta_adjusted_premium(
    option_open: float, delta: float, index_trigger: float, index_open: float, low: float, high: float,
) -> float:
    """Option open plus delta times the index move since that open, held inside the minute's range."""
    raw = float(option_open) + float(delta) * (float(index_trigger) - float(index_open))
    return min(float(high), max(float(low), raw))


def _money(x: Decimal | float) -> float:
    return float(Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP))


def _tod(t: int) -> int:
    return ((int(t) + IST_OFFSET_S) % DAY_S) // 60


@dataclass(frozen=True)
class OptionModelConfig:
    underlying: str = "NIFTY"
    strike_offset: int = 0
    cycle: str = "weekly"
    roll_on_expiry_day: bool = False
    r: float = 0.0
    q: float = 0.0
    vix_scale: float = 1.0
    time_basis: str = "trading"
    slippage_points: float = 0.5
    tick: float = 0.05
    min_premium: float = 0.05
    snap_tick: bool = True
    vix_stale_seconds: int = 300
    gap_pct: float = 0.5
    calibration_ref: str | None = None
    rates_note: str = "r=q=0 until calibration estimates net carry"
    model_version: str | None = None
    model_as_of: str | None = None
    carry_basis: str = "trading"
    real_premiums: bool = False
    vix_scales: tuple[tuple[str, float], ...] = ()
    option_fill: str = "delta_adjusted"  # delta_adjusted | optimistic | adverse | worst
    #: research exits on the option's own premium, as a fraction of the entry fill (0.30 = +30%); None = off
    premium_target_pct: float | None = None
    premium_stop_pct: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "option_fill", canonical_option_fill(self.option_fill))
        if self.strike_offset not in (-1, 0, 1):
            raise ValueError("strike_offset must be -1, 0 or +1")
        if self.time_basis not in ("trading", "calendar"):
            raise ValueError("time_basis must be 'trading' or 'calendar'")
        if self.carry_basis not in ("trading", "calendar"):
            raise ValueError("carry_basis must be 'trading' or 'calendar'")
        if self.slippage_points < 0 or self.vix_scale <= 0:
            raise ValueError("slippage_points must be >= 0 and vix_scale must be > 0")
        names = [name for name, _ in self.vix_scales]
        if names and set(names) != set(DTE_BUCKETS):
            raise ValueError(f"vix_scales must cover {DTE_BUCKETS}")
        for _, scale in self.vix_scales:
            if scale <= 0:
                raise ValueError("every VIX scale must be > 0")
        if self.premium_target_pct is not None and not self.premium_target_pct > 0:
            raise ValueError("premium_target_pct must be > 0")
        if self.premium_stop_pct is not None and not 0 < self.premium_stop_pct < 1:
            raise ValueError("premium_stop_pct must be between 0 and 1")

    def scale_for(self, dte: int) -> float:
        if not self.vix_scales:
            return self.vix_scale
        return float(dict(self.vix_scales)[_dte_bucket(dte)])

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["vix_scales"] = {name: scale for name, scale in self.vix_scales}
        for key in ("premium_target_pct", "premium_stop_pct"):  # off: left out, so earlier run ids stay the same
            if raw[key] is None:
                del raw[key]
        return raw


def load_option_model(path: Path | None = None) -> OptionModelConfig:
    """Shipped calibration. `overlay_options` uses this when no config is passed."""
    file = path or DEFAULT_MODEL_FILE
    data = json.loads(file.read_text())
    scales = data["vix_scales"]
    ordered = tuple((name, float(scales[name])) for name in DTE_BUCKETS)
    return OptionModelConfig(
        model_version=str(data["model_version"]),
        model_as_of=str(data["as_of"]),
        r=float(data["r"]),
        q=float(data["q"]),
        vix_scales=ordered,
        time_basis=str(data.get("time_basis", "trading")),
        carry_basis=str(data["carry_basis"]),
        calibration_ref=str(data["calibration_ref"]),
        real_premiums=bool(data.get("real_premiums", True)),
        slippage_points=float(data.get("slippage_points", 0.5)),
        rates_note=str(data["note"]),
    )


def _dte_bucket(dte: int) -> str:
    if dte <= 0:
        return "0"
    if dte == 1:
        return "1"
    if dte == 2:
        return "2"
    if dte <= 4:
        return "3-4"
    return "5+"


def _days_to_expiry(day: date, expiry: date, is_trading_day: Any) -> int:
    n = 0
    d = day
    while d < expiry:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def _session_map(index_bars: Sequence[dict[str, Any]]) -> tuple[dict[date, dict[str, float]], dict[date, date]]:
    """Per IST day: first open and last close, plus the previous session date."""
    days: dict[date, dict[str, float]] = {}
    order: list[date] = []
    for b in index_bars:
        d = ist_date(int(b["time"]))
        if d not in days:
            days[d] = {"open": float(b["open"]), "close": float(b["close"])}
            order.append(d)
        else:
            days[d]["close"] = float(b["close"])
    prev: dict[date, date] = {}
    for earlier, later in zip(order, order[1:]):
        prev[later] = earlier
    return days, prev


def _opening_gap(days: dict[date, dict[str, float]], prev: dict[date, date], d: date, threshold: float) -> bool:
    if d not in days or d not in prev:
        return False
    pc = days[prev[d]]["close"]
    if pc == 0:
        return False
    return abs(days[d]["open"] - pc) / abs(pc) >= threshold / 100.0


def _bar_on(index_bars: Sequence[dict[str, Any]], day: date, hour: int, minute: int) -> dict[str, Any] | None:
    from datetime import datetime, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    want = int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=ist).timestamp())
    for b in index_bars:
        if int(b["time"]) == want:
            return b
    last = None
    for b in index_bars:
        if ist_date(int(b["time"])) == day and _tod(int(b["time"])) < CLOSE_MIN:
            last = b
    return last


def _index_open(index_bars: Sequence[dict[str, Any]], when: int) -> float | None:
    for bar in index_bars:
        if int(bar["time"]) == int(when):
            return float(bar["open"])
    return None


def _model_delta(
    kind: str, S: float, K: float, when: int, expiry: date, vix: float, cfg: OptionModelConfig, cal: ExpiryCalendar
) -> float:
    """Option-model-v1 delta at the index open of this minute. Call positive, put negative."""
    day = ist_date(when)
    dte = _days_to_expiry(day, expiry, cal.is_trading_day)
    iv = cfg.scale_for(dte) * vix / 100.0
    t_vol = years_to_expiry(when, expiry, cal.is_trading_day, cfg.time_basis)
    t_carry = years_to_expiry(when, expiry, cal.is_trading_day, cfg.carry_basis)
    return bs_forward_delta(kind, S, K, t_vol, t_carry, iv, cfg.r, cfg.q)


def _model_premium(
    kind: str, S: float, K: float, when: int, expiry: date, vix: float, cfg: OptionModelConfig, cal: ExpiryCalendar
) -> tuple[float, float, float, float, int]:
    """Snapped model premium, iv, vol-time, carry-time, and trading days to expiry."""
    day = ist_date(when)
    dte = _days_to_expiry(day, expiry, cal.is_trading_day)
    iv = cfg.scale_for(dte) * vix / 100.0
    t_vol = years_to_expiry(when, expiry, cal.is_trading_day, cfg.time_basis)
    t_carry = years_to_expiry(when, expiry, cal.is_trading_day, cfg.carry_basis)
    raw = bs_forward_price(kind, S, K, t_vol, t_carry, iv, cfg.r, cfg.q)
    prem = snap_premium(raw, cfg.tick, cfg.min_premium) if cfg.snap_tick else raw
    return prem, iv, t_vol, t_carry, dte


def _quote_price(
    ohlc: tuple[float, float, float, float], side: str, mode: str, *, at_open: bool,
    delta: float | None = None, index_trigger: float | None = None, index_open: float | None = None,
) -> float:
    """Price a real option fill.

    A fill at the minute's open (a market order, or a stop gapped through) stays at the
    open in every mode. A stop inside the minute uses the setting: the open (optimistic),
    the open moved by the model's delta, the worse of open and close, or the extreme.
    """
    open_, high, low, close = ohlc
    if at_open or mode == "optimistic":
        return float(open_)
    if mode == "adverse":
        return float(max(open_, close) if side == "BUY" else min(open_, close))
    if mode == "worst":
        return float(high if side == "BUY" else low)
    if delta is None or index_trigger is None or index_open is None:
        return float(open_)
    return delta_adjusted_premium(open_, delta, index_trigger, index_open, low, high)


def _taken_premium(
    model: float, *, cfg: OptionModelConfig, tape: PremiumTape | None, expiry: date, strike: float, kind: str,
    when: int, side: str, at_open: bool, delta: float | None = None,
    index_trigger: float | None = None, index_open: float | None = None,
) -> tuple[float, str]:
    """Real premium for this minute when the tape has it; otherwise the model.

    `optimistic` uses the 1m open, so a stop that fires inside the minute buys the
    option at the price from before the breakout. `delta_adjusted` moves that open by
    the model delta times the index move, and holds the result inside the minute.
    Market fills stay at the open.
    """
    if cfg.real_premiums and tape is not None:
        ohlc = None
        bar_at = getattr(tape, "bar_at", None)
        if bar_at is not None:
            ohlc = bar_at(expiry, strike, kind, when)
        if ohlc is None:
            real = tape.premium_at(expiry, strike, kind, when)
            if real is not None:
                ohlc = (float(real), float(real), float(real), float(real))
        if ohlc is not None:
            return _quote_price(
                ohlc, side, cfg.option_fill, at_open=at_open, delta=delta,
                index_trigger=index_trigger, index_open=index_open,
            ), "real"
        return model, "modelled"
    return model, "model"


def _apply_slip(side: str, premium: float, points: float, floor: float) -> float:
    slipped = premium + points if side == "BUY" else premium - points
    return max(slipped, floor)


def overlay_options(
    result: BacktestResult,
    index_bars: Sequence[dict[str, Any]],
    vix_bars: Sequence[dict[str, Any]],
    *,
    config: OptionModelConfig | None = None,
    calendar: ExpiryCalendar | None = None,
    lots: LotSizeTable | None = None,
    steps: StrikeStepTable | None = None,
    costs: CostModel | None = None,
    events: EventCalendar | None = None,
    tape: PremiumTape | None = None,
    on_trade: Callable[[int, int], None] | None = None,
) -> EstimatedResult:
    """Estimate option P&L for each engine trade. Does not mutate `result`.

    No config means option model v1. With real premiums on, a missing tape uses `data/option_history`.
    """
    cfg = config if config is not None else load_option_model()
    if cfg.real_premiums and tape is None:
        tape = default_history_store()
    cal = calendar or load_default_calendar()
    lot_table = lots or load_default_lot_table()
    step_table = steps or load_default_step_table()
    cost_model = costs if costs is not None else get_cost_model("options")
    ev = events if events is not None else load_default_events()
    sessions, prev_session = _session_map(index_bars)
    vix_hash = _series_digest(vix_bars)

    priced: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []
    warnings: list[str] = []
    used_days: set[date] = set()
    expired_held = 0
    unverified_step = False
    before_history = False

    breaches: list[tuple[int, float, float]] = []
    for trade in result.trades:
        _require_single_fill(trade)
        if ist_date(trade.entry_time) < HISTORY_START or ist_date(trade.exit_time) < HISTORY_START:
            before_history = True
        row = _one_trade(
            trade, index_bars, vix_bars, cfg, cal, lot_table, step_table, cost_model, ev,
            sessions, prev_session, tape,
        )
        if row.get("unpriced"):
            unpriced.append(row)
        else:
            priced.append(row)
        if on_trade is not None:
            on_trade(len(priced) + len(unpriced), len(result.trades))
        if row.get("unpriced"):
            continue
        move = _index_move(trade, int(row["units"]))
        # A smaller loss is not a breach. The option exceeds the move only when it
        # makes more than the index made (including a profit the index did not).
        if float(row["net_pnl"]) > 0 and float(row["net_pnl"]) > move + 0.01:
            breaches.append((int(trade.id), float(row["net_pnl"]), move))
        used_days.add(date.fromisoformat(row["entry_date"]))
        used_days.add(date.fromisoformat(row["exit_date"]))
        if "expired_while_held" in row["flags"]:
            expired_held += 1
        if row["contract"]["step_verification"] != "verified":
            unverified_step = True

    if expired_held:
        warnings.append(
            f"{expired_held} contract(s) settled at intrinsic at expiry close: STT on exercise is not in the "
            f"cost table and is not modelled"
        )
    if unverified_step:
        warnings.append("strike step is UNVERIFIED (assumed 50 until real option lists confirm it)")
    if cfg.calibration_ref is None:
        warnings.append("uncalibrated premium model: vix_scale is the default 1.0; no calibration file is referenced")
    warnings.extend(cost_model.unverified_warnings(used_days))
    warnings.append(cfg.rates_note)
    if before_history:
        warnings.append(MODEL_ONLY_WARNING)
    premium_source, fill_counts = _premium_split(priced, cfg.real_premiums)
    if cfg.option_fill == "optimistic":
        warnings.append(OPTIMISTIC_FILL_WARNING)
    if breaches:
        trade_id, option_net, move = breaches[0]
        warnings.append(
            f"option P&L exceeds the index move on {len(breaches)} trade(s): "
            f"trade {trade_id} option net {option_net} is larger than the index move {round(move, 2)}"
        )
    if cfg.real_premiums:
        total_fills = fill_counts["real"] + fill_counts["modelled"]
        if total_fills and fill_counts["modelled"] / total_fills > 0.20:
            warnings.append(
                f"{MODELLED_FILL_WARNING} ({fill_counts['modelled']} of {total_fills})"
            )

    metrics = compute_metrics([TradeRecord(t["entry_time"], t["exit_time"], t["net_pnl"]) for t in priced])
    metrics["gross_pnl"] = _money(sum((Decimal(str(t["gross_pnl"])) for t in priced), Decimal("0")))
    metrics["total_charges"] = _money(sum((Decimal(str(t["charges_total"])) for t in priced), Decimal("0")))
    metrics["total_slippage"] = _money(sum((Decimal(str(t["slippage_cost"])) for t in priced), Decimal("0")))

    flags = ("expiry_day", "event_day", "reaction_day", "gap", "vix_stale", "expired_while_held", "modelled")
    weak = {
        name: _weak_block(priced, name)
        for name in flags
    }

    settings = cfg.to_dict()
    estimate = OptionEstimate(
        label=ESTIMATED,
        settings=settings,
        trades=priced,
        unpriced=unpriced,
        metrics=metrics,
        warnings=warnings,
        counters={
            "priced": len(priced),
            "unpriced": len(unpriced),
            "real_fills": fill_counts["real"],
            "modelled_fills": fill_counts["modelled"],
        },
        weak=weak,
        carry=_carry_block(cfg),
        data={"vix_sha256": vix_hash, "index_bars": len(index_bars), "vix_bars": len(vix_bars)},
        premium_source=premium_source,
    )
    run_id = estimated_run_id(result.run_id, settings, vix_hash)
    return EstimatedResult(label=ESTIMATED, index=result, option=estimate, run_id=run_id)


def _weak_block(trades: list[dict[str, Any]], flag: str) -> dict[str, Any]:
    flagged = [t for t in trades if flag in t["flags"]]
    clean = [t for t in trades if flag not in t["flags"]]

    def block(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "trades": len(rows),
            "net_pnl": _money(sum((Decimal(str(t["net_pnl"])) for t in rows), Decimal("0"))),
        }

    return {"flagged": block(flagged), "unflagged": block(clean)}


def _carry_block(cfg: OptionModelConfig) -> dict[str, Any]:
    block: dict[str, Any] = {"r": cfg.r, "q": cfg.q, "note": cfg.rates_note}
    if cfg.model_version:
        block["model_version"] = cfg.model_version
        block["as_of"] = cfg.model_as_of
        block["calibration_ref"] = cfg.calibration_ref
        block["carry_basis"] = cfg.carry_basis
        block["time_basis"] = cfg.time_basis
    return block


def _premium_split(trades: list[dict[str, Any]], real_mode: bool) -> tuple[dict[str, Any], dict[str, int]]:
    real_fills = modelled_fills = 0
    real_rows: list[dict[str, Any]] = []
    modelled_rows: list[dict[str, Any]] = []
    for row in trades:
        for src in (row.get("entry_source"), row.get("exit_source")):
            if src == "real":
                real_fills += 1
            elif src == "modelled":
                modelled_fills += 1
        if "modelled" in row.get("flags", []):
            modelled_rows.append(row)
        else:
            real_rows.append(row)

    def block(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "trades": len(rows),
            "net_pnl": _money(sum((Decimal(str(t["net_pnl"])) for t in rows), Decimal("0"))),
        }

    if not real_mode:
        return {}, {"real": 0, "modelled": 0}
    return {
        "real": {**block(real_rows), "fills": real_fills},
        "modelled": {**block(modelled_rows), "fills": modelled_fills},
    }, {"real": real_fills, "modelled": modelled_fills}


def _require_single_fill(trade: Trade) -> None:
    entries = int(getattr(trade, "entry_fills", 1) or 1)
    exits = int(getattr(trade, "exit_fills", 1) or 1)
    if entries == 1 and exits == 1:
        return
    raise OptionOverlayError(
        f"trade {trade.id} has {entries} entry fills and {exits} exit fills; "
        "the option overlay prices one entry and one exit, not an averaged position"
    )


def _index_move(trade: Trade, units: int) -> float:
    """Rupees the index itself made on this position. An option cannot honestly exceed it."""
    sign = 1.0 if trade.direction == "LONG" else -1.0
    return (float(trade.exit_price) - float(trade.entry_price)) * sign * units


def _one_trade(
    trade: Trade,
    index_bars: Sequence[dict[str, Any]],
    vix_bars: Sequence[dict[str, Any]],
    cfg: OptionModelConfig,
    cal: ExpiryCalendar,
    lots: LotSizeTable,
    steps: StrikeStepTable,
    costs: CostModel,
    events: EventCalendar,
    sessions: dict[date, dict[str, float]],
    prev_session: dict[date, date],
    tape: PremiumTape | None,
) -> dict[str, Any]:
    entry_d, exit_d = ist_date(trade.entry_time), ist_date(trade.exit_time)
    # ATM of the index price at the fill moment, as paper picks it: the stop level for a stop fill, the minute's
    # open for a market fill (or for a stop that gapped or filled late), the bar close for same_bar_close.
    spot = float(trade.entry_price)
    try:
        contract = choose_contract(
            trade.direction, spot, entry_d,
            calendar=cal, lots=lots, steps=steps, underlying=cfg.underlying,
            offset=cfg.strike_offset, cycle=cfg.cycle, roll_on_expiry_day=cfg.roll_on_expiry_day,
        )
    except (StrikeStepUnknown, LookupError, ValueError) as exc:
        return {"label": ESTIMATED, "index_trade_id": trade.id, "unpriced": True, "reason": str(exc)}

    units = int(trade.lots) * int(contract.lot_size)
    held_past = trade.exit_time > _expiry_close_ts(contract.expiry)
    exit_time = _expiry_close_ts(contract.expiry) if held_past else trade.exit_time
    exit_spot = float(trade.exit_price)
    if held_past:
        bar = _bar_on(index_bars, contract.expiry, 15, 29)
        exit_spot = float(bar["close"]) if bar is not None else float(trade.exit_price)
        exit_d = contract.expiry

    q_in = vix_asof(vix_bars, trade.entry_time, stale_s=cfg.vix_stale_seconds)
    q_out = None if held_past else vix_asof(vix_bars, exit_time, stale_s=cfg.vix_stale_seconds)
    if q_in is None or (q_out is None and not held_past):
        return {
            "label": ESTIMATED, "index_trade_id": trade.id, "unpriced": True, "reason": "no_vix",
            "contract": contract.to_dict(),
        }

    entry_index_open = _index_open(index_bars, trade.entry_time)
    exit_index_open = _index_open(index_bars, exit_time)
    try:
        entry_model, iv_in, t_in, t_carry_in, _dte_in = _model_premium(
            contract.kind, float(trade.entry_price), contract.strike, trade.entry_time, contract.expiry, q_in.value, cfg, cal,
        )
        entry_delta = None
        if entry_index_open is not None:
            entry_delta = _model_delta(
                contract.kind, entry_index_open, contract.strike, trade.entry_time, contract.expiry, q_in.value, cfg, cal,
            )
        entry_prem, entry_source = _taken_premium(
            entry_model, cfg=cfg, tape=tape, expiry=contract.expiry, strike=contract.strike,
            kind=contract.kind, when=trade.entry_time, side="BUY", at_open=trade.entry_at_open,
            delta=entry_delta, index_trigger=float(trade.entry_price), index_open=entry_index_open,
        )
        if held_past:
            intrinsic = max(exit_spot - contract.strike, 0.0) if contract.kind == "CE" else max(contract.strike - exit_spot, 0.0)
            exit_prem = snap_premium(intrinsic, cfg.tick, cfg.min_premium) if cfg.snap_tick else intrinsic
            exit_source = "intrinsic"
            iv_out = 0.0
            t_out = 0.0
            t_carry_out = 0.0
        else:
            exit_model, iv_out, t_out, t_carry_out, _dte_out = _model_premium(
                contract.kind, exit_spot, contract.strike, exit_time, contract.expiry, q_out.value, cfg, cal,  # type: ignore[union-attr]
            )
            exit_delta = None
            if exit_index_open is not None:
                exit_delta = _model_delta(
                    contract.kind, exit_index_open, contract.strike, exit_time, contract.expiry, q_out.value, cfg, cal,  # type: ignore[union-attr]
                )
            exit_prem, exit_source = _taken_premium(
                exit_model, cfg=cfg, tape=tape, expiry=contract.expiry, strike=contract.strike,
                kind=contract.kind, when=exit_time, side="SELL", at_open=trade.exit_at_open,
                delta=exit_delta, index_trigger=float(exit_spot), index_open=exit_index_open,
            )
    except PricingError as exc:
        return {
            "label": ESTIMATED, "index_trade_id": trade.id, "unpriced": True, "reason": str(exc),
            "contract": contract.to_dict(),
        }

    entry_fill = _apply_slip("BUY", entry_prem, cfg.slippage_points, cfg.min_premium)
    walk = _premium_walk(
        tape if cfg.real_premiums else None, contract.expiry, contract.strike, contract.kind,
        start=trade.entry_time if trade.entry_at_open else trade.entry_time + 60, end=exit_time,
        entry_fill=entry_fill, target_pct=cfg.premium_target_pct, stop_pct=cfg.premium_stop_pct,
    )
    exit_reason = "index"
    if walk.exit is not None:
        exit_time, exit_prem, exit_reason = walk.exit
        exit_source, held_past = "real", False
        exit_d = ist_date(exit_time)
    exit_fill = exit_prem if held_past else _apply_slip("SELL", exit_prem, cfg.slippage_points, cfg.min_premium)
    buy = costs.leg_cost("BUY", entry_fill, units, entry_d)
    if held_past:
        sell_total = Decimal("0.00")
        sell_comp: dict[str, float] = {}
    else:
        sell = costs.leg_cost("SELL", exit_fill, units, exit_d)
        sell_total = sell.total
        sell_comp = {k: float(v) for k, v in sell.components.items()}
    charges_total = buy.total + sell_total
    gross = (Decimal(str(exit_fill)) - Decimal(str(entry_fill))) * Decimal(units)
    slip_cost = (Decimal(str(entry_fill)) - Decimal(str(entry_prem)) + Decimal(str(exit_prem)) - Decimal(str(exit_fill))) * Decimal(units)
    net = gross - charges_total

    flags: list[str] = []
    if entry_d == contract.expiry or exit_d == contract.expiry:
        flags.append("expiry_day")
    if events.spans(entry_d, exit_d):
        flags.append("event_day")
    if events.spans_reaction(entry_d, exit_d):
        flags.append("reaction_day")
    overnight = entry_d != exit_d
    gapped = (
        trade.gap
        or overnight
        or _opening_gap(sessions, prev_session, entry_d, cfg.gap_pct)
        or _opening_gap(sessions, prev_session, exit_d, cfg.gap_pct)
    )
    if gapped:
        flags.append("gap")
    if q_in.stale or (q_out is not None and q_out.stale):
        flags.append("vix_stale")
    if held_past:
        flags.append("expired_while_held")
    if entry_source == "modelled" or exit_source == "modelled":
        flags.append("modelled")
    premium_exit = cfg.premium_target_pct is not None or cfg.premium_stop_pct is not None
    if premium_exit and not walk.checked:
        flags.append("premium_exit_unchecked")
    max_open = None if walk.max_high is None else _money(
        (Decimal(str(max(walk.max_high, exit_prem))) - Decimal(str(entry_fill))) * Decimal(units))

    return {
        "label": ESTIMATED,
        "index_trade_id": trade.id,
        "direction": trade.direction,
        "contract": contract.to_dict(),
        "entry_time": trade.entry_time,
        "exit_time": exit_time,
        "entry_date": entry_d.isoformat(),
        "exit_date": exit_d.isoformat(),
        "index_entry": float(trade.entry_price),
        "index_exit": exit_spot,
        "iv": iv_in,
        "iv_exit": iv_out,
        "dte": _dte_in,
        "T": t_in,
        "T_exit": t_out,
        "T_carry": t_carry_in,
        "T_carry_exit": t_carry_out,
        "time_basis": cfg.time_basis,
        "carry_basis": cfg.carry_basis,
        "model_version": cfg.model_version,
        "entry_source": entry_source,
        "exit_source": exit_source,
        "entry_premium": entry_prem,
        "exit_premium": exit_prem,
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "lots": int(trade.lots),
        "lot_size": contract.lot_size,
        "units": units,
        "gross_pnl": _money(gross),
        "charges_buy": {k: float(v) for k, v in buy.components.items()},
        "charges_sell": sell_comp,
        "charges_total": _money(charges_total),
        "slippage_points": cfg.slippage_points,
        "slippage_cost": _money(slip_cost),
        "net_pnl": _money(net),
        "exit_reason": exit_reason,
        #: best open profit (premium high over the holding, before slippage and charges) and how much of it was
        #: not kept; None when the contract has no real minute bars for the trade
        "max_open_gross": max_open,
        "given_back": None if max_open is None else _money(Decimal(str(max_open)) - gross),
        "flags": flags,
        "unpriced": False,
    }


@dataclass(frozen=True)
class _Walk:
    exit: tuple[int, float, str] | None  # (minute, premium, "premium_target" | "premium_stop")
    max_high: float | None  # highest premium seen up to the exit; None when no real bar was found
    checked: bool  # at least one real bar was walked


def _premium_walk(
    tape: PremiumTape | None, expiry: date, strike: float, kind: str, *, start: int, end: int,
    entry_fill: float, target_pct: float | None, stop_pct: float | None,
) -> _Walk:
    """Walk the contract's real 1m bars over [start, end): the first premium target or stop, and the high."""
    bar_at = getattr(tape, "bar_at", None) if tape is not None else None
    if bar_at is None:
        return _Walk(None, None, False)
    target = None if target_pct is None else round(entry_fill * (1 + target_pct), 2)
    stop = None if stop_pct is None else round(entry_fill * (1 - stop_pct), 2)
    high: float | None = None
    t = start - start % 60
    first = True
    while t < end:
        ohlc = bar_at(expiry, strike, kind, t)
        if ohlc is not None:
            o, h, lo, _c = (float(x) for x in ohlc)
            if not first:  # the entry minute's open is the entry itself
                if stop is not None and o <= stop:
                    return _Walk((t, o, "premium_stop"), o if high is None else max(high, o), True)
                if target is not None and o >= target:
                    return _Walk((t, o, "premium_target"), o if high is None else max(high, o), True)
            hit_stop = stop is not None and lo <= stop
            hit_target = target is not None and h >= target
            if hit_stop:  # both in one minute: the stop first, as the engine does
                # the minute's high may come after the stop: only its open counts toward the best open profit
                return _Walk((t, stop, "premium_stop"), o if high is None else max(high, o), True)  # type: ignore[arg-type]
            if hit_target:
                return _Walk((t, target, "premium_target"), target if high is None else max(high, target), True)  # type: ignore[arg-type]
            high = h if high is None else max(high, h)
        first = False
        t += 60
    return _Walk(None, high, high is not None)


def _expiry_close_ts(expiry: date) -> int:
    from datetime import datetime, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    return int(datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=ist).timestamp())


def _series_digest(bars: Sequence[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{int(b['time'])},{b.get('open')},{b.get('close')}\n".encode("ascii"))
    return h.hexdigest()
