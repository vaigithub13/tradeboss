"""Manual assist for one paper slot. Builds a trade ticket and logs what the user did by hand.

Nothing here talks to a broker. There is no order placement, no modify, and no cancel.
Paper slots keep running whether the assist is on or off. The journal lives under data/paper/assist/.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.costs import get_cost_model
from app.exits.rules import ExitRule, premium_levels
from app.options.bs import snap_premium
from app.paper.assist_book import (
    after_trade,
    dd_facts_at,
    format_dd_why,
    initial_stop,
    market_of,
    price_action,
    trails_on_time,
)
from app.paper.real import paper_trade_key, real_net

IST = timezone(timedelta(hours=5, minutes=30))
TICK = 0.05
PREMIUM_STOP = 0.20
PREMIUM_TARGET = 0.40
DEFAULT_SLOTS = ["5", "7"]
DEFAULT_SLOT = "5"
DEFAULT_LOTS = 1
DEFAULT_MAX_TRADES = 3
DEFAULT_DAY_LOSS = 4000.0
DEFAULT_ACCOUNT_PAUSE = 12000.0
ENTRY_WINDOW_S = 60
REMINDER_MINUTE = 15 * 60 + 10
STORY_MINUTE = 15 * 60 + 30
SKIP_REASONS = ("late", "missed", "afraid", "other")
CHART_BARS = 30


def default_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "slots": list(DEFAULT_SLOTS),
        "slot": DEFAULT_SLOT,
        "lots": DEFAULT_LOTS,
        "max_trades": DEFAULT_MAX_TRADES,
        "day_loss": DEFAULT_DAY_LOSS,
        "account_pause": DEFAULT_ACCOUNT_PAUSE,
        "start_balance": None,
    }


def normalise_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Fill gaps and reject a limit that is not a positive number. `start_balance` may be absent."""
    base = default_settings()
    data = raw or {}
    base["enabled"] = bool(data.get("enabled", True))
    raw_slots = data.get("slots")
    if isinstance(raw_slots, list) and raw_slots:
        slots = [str(item).strip() for item in raw_slots if str(item).strip() and str(item).strip() != "4"]
    else:
        slots = list(DEFAULT_SLOTS)
    if not slots:
        slots = list(DEFAULT_SLOTS)
    base["slots"] = slots
    base["slot"] = slots[0]
    base["lots"] = _positive_int(data.get("lots", DEFAULT_LOTS), "lots")
    base["max_trades"] = _positive_int(data.get("max_trades", DEFAULT_MAX_TRADES), "max_trades")
    base["day_loss"] = _positive_float(data.get("day_loss", DEFAULT_DAY_LOSS), "day_loss")
    base["account_pause"] = _positive_float(data.get("account_pause", DEFAULT_ACCOUNT_PAUSE), "account_pause")
    start = data.get("start_balance", None)
    if start is None or start == "":
        base["start_balance"] = None
    else:
        start_f = float(start)
        if start_f < 0:
            raise ValueError("start_balance must be zero or more")
        base["start_balance"] = start_f
    return base


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != float(value) or int(value) < 1:
        raise ValueError(f"{name} must be a whole number >= 1")
    return int(value)


def _positive_float(value: Any, name: str) -> float:
    number = float(value)
    if number <= 0:
        raise ValueError(f"{name} must be a positive number of rupees")
    return number


def suggested_limit(ask: float | None, fallback: float | None, tick: float = TICK) -> float | None:
    """The limit to show the user: the ask, snapped to the option tick. The paper fill if the ask is missing."""
    price = ask if ask is not None and ask > 0 else fallback
    if price is None or price <= 0:
        return None
    return snap_premium(float(price), tick)


def premium_bracket(price: float, stop: float = PREMIUM_STOP, target: float = PREMIUM_TARGET) -> tuple[float, float]:
    """Stop and target premium of a bought option. Same rounding as the paper exit rule."""
    return premium_levels(float(price), ExitRule("premium", float(stop), float(target)))


def option_charges(entry: float, exit_price: float, units: int, on: date) -> float:
    """Buy at `entry` and sell at `exit_price`, from the dated options cost table."""
    costs = get_cost_model("options")
    buy = costs.leg_cost("BUY", float(entry), int(units), on)
    sell = costs.leg_cost("SELL", float(exit_price), int(units), on)
    return round(float(buy.total + sell.total), 2)


def max_loss(entry: float, stop: float, units: int, charges: float) -> float:
    """Premium lost to the stop, plus the round-trip charges of that exit."""
    return round(max(0.0, float(entry) - float(stop)) * int(units) + float(charges), 2)


def ist_hhmm(epoch_s: int) -> str:
    return datetime.fromtimestamp(int(epoch_s), IST).strftime("%H:%M")


def _vwap_at(bars: list[dict[str, Any]], index: int) -> float | None:
    """Session VWAP of typical price through `index`. A bar with no volume counts as 1."""
    if index < 0 or index >= len(bars):
        return None
    end_day = _ist_day(int(bars[index]["time"]))
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(index + 1):
        if _ist_day(int(bars[i]["time"])) != end_day:
            continue
        typical = (float(bars[i]["high"]) + float(bars[i]["low"]) + float(bars[i]["close"])) / 3.0
        vol = float(bars[i].get("volume") or 0)
        if vol <= 0:
            vol = 1.0
        cum_pv += typical * vol
        cum_v += vol
    if cum_v <= 0:
        return None
    return cum_pv / cum_v


def _ist_day(epoch_s: int) -> date:
    return datetime.fromtimestamp(int(epoch_s), IST).date()


def _window_levels(bars: list[dict[str, Any]], index: int, length: int) -> tuple[float, int, float, int]:
    """(channel high, bar time that set it, channel low, bar time that set it). The latest tie wins."""
    window = bars[index - length + 1: index + 1]
    high = max(float(b["high"]) for b in window)
    low = min(float(b["low"]) for b in window)
    high_at = max(int(b["time"]) for b in window if float(b["high"]) == high)
    low_at = max(int(b["time"]) for b in window if float(b["low"]) == low)
    return high, high_at, low, low_at


def _arm_index(bars: list[dict[str, Any]], *, length: int, side: str, order_price: float,
               fill_time: int, tick: float) -> int | None:
    """The last closed bar whose channel stop equals the filled order, at or before the fill bar."""
    found: int | None = None
    for i in range(length, len(bars)):
        if int(bars[i]["time"]) > fill_time:
            break
        high, _, low, _ = _window_levels(bars, i, length)
        level = round(high + tick, 2) if side == "BUY" else round(low - tick, 2)
        if abs(level - float(order_price)) < 0.001:
            found = i
    return found


def explain_channel(
    bars: list[dict[str, Any]],
    *,
    side: str,
    index_price: float,
    order_price: float | None,
    fill_time: int,
    length: int = 20,
    tick: float = TICK,
) -> dict[str, Any]:
    """Facts from the price-channel rule at the signal. No opinion."""
    facts: dict[str, Any] = {
        "length": length,
        "side": side,
        "index_price": round(float(index_price), 2),
        "order_price": None if order_price is None else round(float(order_price), 2),
        "channel_high": None,
        "channel_high_at": None,
        "channel_low": None,
        "channel_low_at": None,
        "vwap": None,
        "why": "",
        "chart": None,
    }
    if order_price is None or len(bars) <= length:
        facts["why"] = (
            f"5m price {float(index_price):,.2f} crossed the {side.lower()} stop"
            + ("" if order_price is None else f" at {float(order_price):,.2f}")
            + ". The channel values were not on the bars available."
        )
        return facts
    arm = _arm_index(bars, length=length, side=side, order_price=float(order_price), fill_time=fill_time, tick=tick)
    if arm is None:
        facts["why"] = (
            f"5m price {float(index_price):,.2f} crossed the {side.lower()} stop at {float(order_price):,.2f}. "
            "The channel values were not on the bars available."
        )
        return facts
    high, high_at, low, low_at = _window_levels(bars, arm, length)
    vwap = _vwap_at(bars, arm)
    facts.update(
        channel_high=round(high, 2),
        channel_high_at=high_at,
        channel_low=round(low, 2),
        channel_low_at=low_at,
        vwap=None if vwap is None else round(vwap, 2),
    )
    if side == "BUY":
        broke, broke_at, other_name, other = "high", high_at, "low", low
    else:
        broke, broke_at, other_name, other = "low", low_at, "high", high
    level = high if side == "BUY" else low
    text = (
        f"5m price {float(index_price):,.2f} crossed the {length}-bar channel {broke} {level:,.2f} "
        f"set at {ist_hhmm(broke_at)}; channel {other_name} {other:,.2f}"
    )
    if vwap is not None:
        if index_price > vwap:
            rel = "above"
        elif index_price < vwap:
            rel = "below"
        else:
            rel = "at"
        text += f"; Nifty {rel} VWAP {vwap:,.2f}"
    facts["why"] = text + "."
    facts["chart"] = chart_snapshot(
        bars, signal_time=fill_time, channel_high=round(high, 2), channel_low=round(low, 2),
    )
    return facts


def chart_snapshot(
    bars: list[dict[str, Any]],
    *,
    signal_time: int,
    channel_high: float | None,
    channel_low: float | None,
    entry_time: int | None = None,
    exit_time: int | None = None,
    n: int = CHART_BARS,
) -> dict[str, Any]:
    """The last `n` closed bars up to the signal, or from the entry through the exit when both are set."""
    if entry_time is not None and exit_time is not None:
        chosen = [b for b in bars if entry_time <= int(b["time"]) <= exit_time]
        if len(chosen) > n:
            chosen = chosen[-n:]
    else:
        chosen = [b for b in bars if int(b["time"]) <= signal_time][-n:]
    candles = [
        {
            "time": int(b["time"]),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
        }
        for b in chosen
    ]
    return {
        "candles": candles,
        "channel_high": channel_high,
        "channel_low": channel_low,
        "signal_time": signal_time,
        "entry_time": entry_time,
        "exit_time": exit_time,
    }


def build_ticket(
    *,
    slot: str,
    day: str,
    signal: dict[str, Any],
    bars: list[dict[str, Any]],
    quote: dict[str, float | None] | None,
    ltp: float | None,
    lot_size: int,
    lots: int,
    length: int = 20,
    tick: float = TICK,
    stop_frac: float | None = PREMIUM_STOP,
    target_frac: float | None = PREMIUM_TARGET,
    on: date | None = None,
    trailing: bool = False,
    market: str | None = None,
    why: str | None = None,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One entry ticket. The suggested limit is the ask. A trail has a stop and no target."""
    decided = int(signal["decided_at_ms"])
    fill_time = decided // 1000
    key = paper_trade_key(slot=slot, day=day, fill_time=fill_time)
    side = str(signal.get("side") or "")
    option_side = "CE" if side == "BUY" else "PE" if side == "SELL" else ""
    ask = None if quote is None else quote.get("ask")
    bid = None if quote is None else quote.get("bid")
    paper_fill = signal.get("fill_price")
    limit = suggested_limit(ask if isinstance(ask, (int, float)) else None,
                            float(paper_fill) if isinstance(paper_fill, (int, float)) else None, tick)
    stop = target = None
    loss = charges = None
    units = int(lots) * int(lot_size)
    if limit is not None and units >= 1 and stop_frac is not None and (trailing or target_frac is not None):
        if trailing:
            stop, target = initial_stop(limit, stop_frac), None
        else:
            stop, target = premium_bracket(limit, stop_frac, float(target_frac))
        when = on or date.fromisoformat(day)
        charges = option_charges(limit, stop, units, when)
        loss = max_loss(limit, stop, units, charges)
    index_price = float(signal.get("index_price") or 0)
    order_price = signal.get("order_price")
    bar_time = int(signal["time"]) if signal.get("time") is not None else fill_time
    if why is None:
        explained = explain_channel(
            bars, side=side, index_price=index_price,
            order_price=float(order_price) if isinstance(order_price, (int, float)) else None,
            fill_time=bar_time, length=length, tick=tick,
        )
        why = explained["why"]
        chart = explained["chart"]
    else:
        chart = chart_snapshot(bars, signal_time=bar_time, channel_high=None, channel_low=None)
    return {
        "key": key,
        "slot": slot,
        "day": day,
        "fill_time": fill_time,
        "decided_at_ms": decided,
        "time": ist_hhmm(fill_time),
        "action": "BUY",
        "option_side": option_side,
        "signal_side": side,
        "contract": signal.get("symbol"),
        "bid": None if not isinstance(bid, (int, float)) else float(bid),
        "ask": None if not isinstance(ask, (int, float)) else float(ask),
        "ltp": None if ltp is None else float(ltp),
        "suggested_limit": limit,
        "lots": int(lots),
        "lot_size": int(lot_size),
        "quantity": units,
        "stop": stop,
        "target": target,
        "stop_frac": stop_frac,
        "target_frac": None if trailing else target_frac,
        "trailing": trailing,
        "exit_note": "exit by trailing stop" if trailing else None,
        "market": market or market_of(slot, signal.get("symbol") if isinstance(signal.get("symbol"), str) else None),
        "charges": charges,
        "max_loss": loss,
        "paper_fill": None if paper_fill is None else float(paper_fill),
        "why": why,
        "chart": chart,
        "price_action": facts,
        "index_price": round(index_price, 2),
    }


def ticket_from_session(
    session: Any,
    *,
    decided_at_ms: int,
    settings: dict[str, Any],
    now_ms: int,
    slot: str,
    day: str,
) -> dict[str, Any] | None:
    """The entry ticket for one filled signal, read off the paper session. No order is sent."""
    if session is None:
        return None
    signal = next(
        (
            s for s in session.signals
            if int(s.get("decided_at_ms") or 0) == int(decided_at_ms)
            and s.get("status") == "filled"
            and s.get("side") in ("BUY", "SELL")
        ),
        None,
    )
    if signal is None:
        return None
    key = signal.get("key")
    quote = None
    ltp = None
    if key:
        q = session.quotes.at(key, now_ms)
        if q is not None:
            quote = {"bid": q.bid, "ask": q.ask}
        ltp = session.last_trade(key)
    rule = session.exit_rule
    trailing = rule is not None and rule.kind == "premium" and rule.trailing
    stop_frac = rule.stop if rule is not None and rule.kind == "premium" else None
    target_frac = None if trailing or rule is None or rule.kind != "premium" else rule.target
    length = int(getattr(session.strategy, "length", 20) or 20)
    tick = float(getattr(session.strategy, "tick", TICK) or TICK)
    bars = list(session.shown)
    bar_time = int(signal["time"]) if signal.get("time") is not None else int(signal["decided_at_ms"]) // 1000
    why, facts = _dd_context(session, bars, bar_time)
    return build_ticket(
        slot=slot,
        day=day,
        signal=signal,
        bars=bars,
        quote=quote,
        ltp=ltp,
        lot_size=_lot_size(session, signal),
        lots=int(settings["lots"]),
        length=length,
        tick=tick,
        stop_frac=stop_frac,
        target_frac=target_frac,
        on=date.fromisoformat(day),
        trailing=trailing,
        market=market_of(slot, getattr(session, "symbol", None)),
        why=why,
        facts=facts,
    )


def _dd_context(session: Any, bars: list[dict[str, Any]], bar_time: int) -> tuple[str | None, dict[str, Any] | None]:
    """The DD sentence and the price-action block when this session is that strategy."""
    if getattr(session.strategy, "name", "") != "dd_five_confirm":
        return None, None
    index = max((i for i, bar in enumerate(bars) if int(bar["time"]) <= bar_time), default=None)
    facts = price_action(bars, signal_index=index) if bars else None
    why = "DD confirmations were not on the bars available."
    if index is not None:
        prev = _prev_day_hlc(bars, int(bars[index]["time"]))
        raw = dd_facts_at(bars, index, prev_day=prev, use_wr=bool(getattr(session.strategy, "use_wr", True)))
        if raw is not None:
            why = format_dd_why(raw)
    return why, facts


def _prev_day_hlc(bars: list[dict[str, Any]], now: int) -> tuple[float, float, float] | None:
    today = datetime.fromtimestamp(now, IST).date()
    days: dict[Any, list[dict[str, Any]]] = {}
    for bar in bars:
        days.setdefault(datetime.fromtimestamp(int(bar["time"]), IST).date(), []).append(bar)
    prior = [day for day in sorted(days) if day < today]
    if not prior:
        return None
    prev = days[prior[-1]]
    return (
        max(float(b["high"]) for b in prev),
        min(float(b["low"]) for b in prev),
        float(prev[-1]["close"]),
    )


def _lot_size(session: Any, signal: dict[str, Any]) -> int:
    pos = session.book.position
    if pos is not None and pos.symbol == signal.get("symbol"):
        return int(pos.lot_size)
    fill_time = int(signal["decided_at_ms"]) // 1000
    for trade in reversed(session.book.trades):
        if trade.get("symbol") == signal.get("symbol") and int(trade.get("entry_at_ms") or 0) // 1000 == fill_time:
            return int(trade.get("lot_size") or 0)
    return 0


def chart_for_close(record: dict[str, Any], bars: list[dict[str, Any]], exit_time: int) -> dict[str, Any] | None:
    """Candles from the signal bar through the real exit, keeping the channel lines from the entry ticket."""
    chart = record.get("chart") or {}
    signal = chart.get("signal_time")
    if signal is None:
        return chart or None
    return chart_snapshot(
        bars,
        signal_time=int(signal),
        channel_high=chart.get("channel_high"),
        channel_low=chart.get("channel_low"),
        entry_time=int(signal),
        exit_time=int(exit_time),
    )


def reprice(fill_price: float, *, stop_frac: float = PREMIUM_STOP, target_frac: float = PREMIUM_TARGET,
            units: int, on: date, trailing: bool = False) -> dict[str, float | None]:
    """Stop, target, charges, and max loss from the fill the user actually got."""
    if trailing:
        stop, target = initial_stop(fill_price, stop_frac), None
    else:
        stop, target = premium_bracket(fill_price, stop_frac, target_frac)
    charges = option_charges(fill_price, stop, units, on)
    return {
        "stop": stop,
        "target": target,
        "charges_at_stop": charges,
        "max_loss": max_loss(fill_price, stop, units, charges),
    }


def rule_followed(record: dict[str, Any]) -> dict[str, Any]:
    """Entered within a minute, the initial stop set, each trail confirmed within 2 minutes, no early exit."""
    paper_fill = int(record.get("fill_time") or 0)
    entry_time = record.get("entry_time")
    delay = None if entry_time is None else int(entry_time) - paper_fill
    on_time = delay is not None and 0 <= delay <= ENTRY_WINDOW_S
    trailing = bool(record.get("trailing"))
    levels_set = bool(record.get("sl_set")) and (trailing or bool(record.get("tp_set")))
    trail_ok = trails_on_time(record)
    paper_exit = record.get("paper_exit_time")
    real_exit = record.get("exit_time")
    if real_exit is None:
        early = False
        no_early = True
    elif paper_exit is None:
        early = True
        no_early = False
    else:
        early = int(real_exit) < int(paper_exit)
        no_early = not early
    return {
        "entry_delay_s": delay,
        "entered_on_time": on_time,
        "sl_tp_set": levels_set,
        "trails_on_time": trail_ok,
        "no_early_exit": no_early,
        "followed": bool(on_time and levels_set and trail_ok and no_early and real_exit is not None),
    }


def guard_banner(
    records: list[dict[str, Any]],
    *,
    today: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Counts and banners. A banner never stops a paper slot."""
    taken = [r for r in records if r.get("kind") == "taken"]
    today_rows = [r for r in taken if r.get("day") == today]
    closed_today = [r for r in today_rows if r.get("net") is not None]
    pnl_today = round(sum(float(r["net"]) for r in closed_today), 2)
    running = round(sum(float(r["net"]) for r in taken if r.get("net") is not None), 2)
    day_loss = float(settings["day_loss"])
    pause_at = float(settings["account_pause"])
    start = settings.get("start_balance")
    pause = start is not None and running <= -pause_at + 1e-9
    return {
        "trades_today": len(today_rows),
        "max_trades": int(settings["max_trades"]),
        "at_trade_cap": len(today_rows) >= int(settings["max_trades"]),
        "pnl_today": pnl_today,
        "day_loss": day_loss,
        "stop_for_today": pnl_today <= -day_loss + 1e-9,
        "open_trades": sum(1 for r in today_rows if r.get("exit_time") is None),
        "start_balance": start,
        "account_pnl": running,
        "equity": None if start is None else round(float(start) + running, 2),
        "account_pause": pause_at,
        "pause": pause,
        "needs_start_balance": start is None,
    }


def reminder_due(minute_of_day: int, open_trades: int, already: bool) -> bool:
    """15:10 IST, once, while a real trade is still open. Stops at 15:30, when the story takes over."""
    return (not already) and open_trades > 0 and REMINDER_MINUTE <= minute_of_day < STORY_MINUTE


def story_due(minute_of_day: int) -> bool:
    return minute_of_day >= STORY_MINUTE


def running_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real versus paper for every taken trade, oldest first."""
    rows = []
    for rec in sorted((r for r in records if r.get("kind") == "taken"), key=lambda r: (r["day"], r["fill_time"])):
        paper_entry = rec.get("paper_fill")
        real_entry = rec.get("fill_price")
        paper_exit = rec.get("paper_exit_price")
        real_exit = rec.get("exit_price")
        rows.append({
            "key": rec["key"],
            "day": rec["day"],
            "slot": rec.get("slot"),
            "market": rec.get("market") or market_of(str(rec.get("slot") or ""), rec.get("contract") if isinstance(rec.get("contract"), str) else None),
            "contract": rec.get("contract"),
            "option_side": rec.get("option_side"),
            "why": rec.get("why"),
            "price_action": rec.get("price_action"),
            "after": rec.get("after"),
            "chart": rec.get("chart"),
            "paper_entry": paper_entry,
            "real_entry": real_entry,
            "entry_slip": None if paper_entry is None or real_entry is None else round(float(real_entry) - float(paper_entry), 2),
            "paper_exit": paper_exit,
            "real_exit": real_exit,
            "exit_slip": None if paper_exit is None or real_exit is None else round(float(real_exit) - float(paper_exit), 2),
            "paper_net": rec.get("paper_net"),
            "real_net": rec.get("net"),
            "exit_reason": rec.get("paper_exit_reason"),
            "skipped": False,
            "rule": rule_followed(rec),
        })
    return rows


def _atomic_write(path: Any, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class AssistJournal:
    """settings.json and journal.json. Taken, skipped, and closed. Never a paper position."""

    def __init__(self, directory: Any) -> None:
        self.directory = directory

    @property
    def settings_path(self) -> Any:
        return self.directory / "settings.json"

    @property
    def journal_path(self) -> Any:
        return self.directory / "journal.json"

    def load_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return default_settings()
        return normalise_settings(json.loads(self.settings_path.read_text(encoding="utf-8")))

    def save_settings(self, raw: dict[str, Any]) -> dict[str, Any]:
        body = normalise_settings(raw)
        _atomic_write(self.settings_path, body)
        return body

    def load(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        data = json.loads(self.journal_path.read_text(encoding="utf-8"))
        return list(data.get("records") or [])

    def _save(self, records: list[dict[str, Any]]) -> None:
        _atomic_write(self.journal_path, {"records": records})

    def _by_key(self, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(r["key"]): r for r in records}

    def take(self, body: dict[str, Any], *, on: date | None = None) -> dict[str, Any]:
        """Log the fill the user got. Recomputes the stop and target from that fill."""
        settings = self.load_settings()
        key = str(body["key"])
        slot = str(body["slot"])
        day = str(body["day"])
        fill_time = int(body["fill_time"])
        if key != paper_trade_key(slot=slot, day=day, fill_time=fill_time):
            raise ValueError("key does not match slot/day/fill_time")
        fill_price = float(body["fill_price"])
        if fill_price <= 0:
            raise ValueError("fill_price must be positive")
        entry_time = int(body["entry_time"])
        lot_size = int(body["lot_size"])
        if lot_size < 1:
            raise ValueError("lot_size must be >= 1")
        units = int(settings["lots"]) * lot_size
        when = on or date.fromisoformat(day)
        trailing = bool(body.get("trailing"))
        stop_frac = float(body.get("stop_frac") or (0.10 if trailing else PREMIUM_STOP))
        if trailing:
            if not 0 < stop_frac < 1:
                raise ValueError("stop fraction is not a premium stop")
            priced = reprice(fill_price, stop_frac=stop_frac, units=units, on=when, trailing=True)
        else:
            target_frac = float(body.get("target_frac") or PREMIUM_TARGET)
            if not 0 < stop_frac < 1 or target_frac <= 0:
                raise ValueError("stop and target fractions are not a premium bracket")
            priced = reprice(fill_price, stop_frac=stop_frac, target_frac=target_frac, units=units, on=when)
        records = self.load()
        previous = self._by_key(records).get(key)
        if previous is not None and previous.get("kind") == "skipped":
            raise ValueError("this signal was skipped")
        if previous is not None and previous.get("exit_time") is not None:
            raise ValueError("this real trade is already closed")
        saved = {
            "key": key,
            "kind": "taken",
            "slot": slot,
            "day": day,
            "fill_time": fill_time,
            "decided_at_ms": int(body.get("decided_at_ms") or fill_time * 1000),
            "contract": body.get("contract"),
            "option_side": body.get("option_side"),
            "signal_side": body.get("signal_side"),
            "paper_fill": body.get("paper_fill"),
            "suggested_limit": body.get("suggested_limit"),
            "fill_price": fill_price,
            "entry_time": entry_time,
            "stop": priced["stop"],
            "initial_stop": priced["stop"],
            "target": priced["target"],
            "trailing": trailing,
            "market": body.get("market") or market_of(slot, body.get("contract") if isinstance(body.get("contract"), str) else None),
            "price_action": body.get("price_action"),
            "trail_seen": [] if previous is None else list(previous.get("trail_seen") or []),
            "trails": [] if previous is None else list(previous.get("trails") or []),
            "max_loss": priced["max_loss"],
            "sl_set": bool(body.get("sl_set")),
            "tp_set": bool(body.get("tp_set")),
            "lots": int(settings["lots"]),
            "lot_size": lot_size,
            "units": units,
            "why": body.get("why") or "",
            "chart": body.get("chart"),
            "exit_time": None if previous is None else previous.get("exit_time"),
            "exit_price": None if previous is None else previous.get("exit_price"),
            "net": None if previous is None else previous.get("net"),
            "charges": None if previous is None else previous.get("charges"),
            "paper_exit_price": None if previous is None else previous.get("paper_exit_price"),
            "paper_exit_time": None if previous is None else previous.get("paper_exit_time"),
            "paper_exit_reason": None if previous is None else previous.get("paper_exit_reason"),
            "paper_net": None if previous is None else previous.get("paper_net"),
            "logged_at": datetime.now(tz=IST).isoformat(timespec="seconds"),
        }
        records = [r for r in records if r.get("key") != key]
        records.append(saved)
        self._save(records)
        return saved

    def skip(self, body: dict[str, Any]) -> dict[str, Any]:
        reason = str(body.get("reason") or "")
        if reason not in SKIP_REASONS:
            raise ValueError(f"reason must be one of {SKIP_REASONS}")
        key = str(body["key"])
        slot = str(body["slot"])
        day = str(body["day"])
        fill_time = int(body["fill_time"])
        if key != paper_trade_key(slot=slot, day=day, fill_time=fill_time):
            raise ValueError("key does not match slot/day/fill_time")
        records = self.load()
        previous = self._by_key(records).get(key)
        if previous is not None and previous.get("kind") == "taken":
            raise ValueError("this signal was taken")
        saved = {
            "key": key,
            "kind": "skipped",
            "slot": slot,
            "day": day,
            "fill_time": fill_time,
            "reason": reason,
            "contract": body.get("contract"),
            "logged_at": datetime.now(tz=IST).isoformat(timespec="seconds"),
        }
        records = [r for r in records if r.get("key") != key]
        records.append(saved)
        self._save(records)
        return saved

    def close(self, body: dict[str, Any], *, on: date | None = None) -> dict[str, Any]:
        """Finish a taken trade at the price the user closed. Charges use that exit, not the stop."""
        key = str(body["key"])
        records = self.load()
        found = self._by_key(records).get(key)
        if found is None or found.get("kind") != "taken":
            raise ValueError("no open real trade for this signal")
        exit_price = float(body["exit_price"])
        if exit_price <= 0:
            raise ValueError("exit_price must be positive")
        exit_time = int(body["exit_time"])
        if exit_time < int(found["entry_time"]):
            raise ValueError("exit_time must be at or after entry_time")
        when = on or date.fromisoformat(str(found["day"]))
        chart = body.get("chart") or found.get("chart")
        candles = list((chart or {}).get("candles") or [])
        review = after_trade(candles, entry_time=int(candles[0]["time"]), exit_time=int(candles[-1]["time"])) if len(candles) >= 2 else None
        charges = option_charges(float(found["fill_price"]), exit_price, int(found["units"]), when)
        net = real_net(entry_price=float(found["fill_price"]), exit_price=exit_price,
                       units=int(found["units"]), charges=charges)
        found = {
            **found,
            "exit_price": exit_price,
            "exit_time": exit_time,
            "charges": charges,
            "net": net,
            "paper_exit_price": body.get("paper_exit_price"),
            "paper_exit_time": body.get("paper_exit_time"),
            "paper_exit_reason": body.get("paper_exit_reason"),
            "paper_net": body.get("paper_net"),
            "chart": chart,
            "after": review,
            "closed_at": datetime.now(tz=IST).isoformat(timespec="seconds"),
        }
        records = [r for r in records if r.get("key") != key]
        records.append(found)
        self._save(records)
        return found

    def note_trail_seen(self, key: str, paper_stop: float, moved_at: int) -> None:
        records = self.load()
        found = self._by_key(records).get(key)
        if found is None:
            return
        seen = list(found.get("trail_seen") or [])
        if any(row.get("paper_stop") == paper_stop for row in seen):
            return
        seen.append({"paper_stop": float(paper_stop), "moved_at": int(moved_at)})
        found["trail_seen"] = seen
        records = [row for row in records if row.get("key") != key]
        records.append(found)
        self._save(records)

    def log_trail(self, body: dict[str, Any]) -> dict[str, Any]:
        """The user moved the real stop. `updated_at` is when they confirmed it."""
        key = str(body["key"])
        records = self.load()
        found = self._by_key(records).get(key)
        if found is None or found.get("kind") != "taken" or found.get("exit_time") is not None:
            raise ValueError("no open real trade")
        paper_stop = float(body["paper_stop"])
        row = {
            "paper_stop": paper_stop,
            "real_stop": float(body["real_stop"]),
            "moved_at": int(body["moved_at"]),
            "updated_at": int(body["updated_at"]),
        }
        trails = [item for item in (found.get("trails") or []) if item.get("paper_stop") != paper_stop]
        trails.append(row)
        found["trails"] = trails
        found["stop"] = row["real_stop"]
        records = [item for item in records if item.get("key") != key]
        records.append(found)
        self._save(records)
        return found

    def _practice_path(self) -> Any:
        return self.directory / "practice.json"

    def _practice(self) -> dict[str, Any]:
        path = self._practice_path()
        if not path.exists():
            return {"reads": [], "puzzle": None}
        return json.loads(path.read_text(encoding="utf-8"))

    def open_practice(self, bars: list[dict[str, Any]]) -> dict[str, Any]:
        """Hide the last 6 closed bars until the read is saved. The reply does not include them."""
        from app.paper.assist_book import PRACTICE_BARS

        if len(bars) < PRACTICE_BARS + 6:
            raise ValueError("need at least 12 closed bars")
        hidden = bars[-PRACTICE_BARS:]
        shown = bars[-(PRACTICE_BARS + 50):-PRACTICE_BARS]
        puzzle = {
            "id": str(int(shown[-1]["time"])),
            "shown": shown,
            "signal_close": float(shown[-1]["close"]),
            "hidden": hidden,
            "read": None,
        }
        data = self._practice()
        data["puzzle"] = puzzle
        _atomic_write(self._practice_path(), data)
        return {"id": puzzle["id"], "shown": shown, "signal_close": puzzle["signal_close"]}

    def save_practice_read(self, body: dict[str, Any]) -> dict[str, Any]:
        from app.paper.assist_book import PRACTICE_TAGS

        data = self._practice()
        puzzle = data.get("puzzle")
        if puzzle is None or str(puzzle.get("id")) != str(body.get("id")):
            raise ValueError("no practice window is open")
        direction = str(body.get("direction") or "")
        if direction not in ("up", "down"):
            raise ValueError("direction must be up or down")
        tag = str(body.get("tag") or "")
        if tag not in PRACTICE_TAGS:
            raise ValueError(f"tag must be one of {PRACTICE_TAGS}")
        reason = str(body.get("reason") or "").strip()
        if not reason:
            raise ValueError("reason is required")
        level = body.get("level")
        puzzle["read"] = {
            "direction": direction,
            "reason": reason,
            "level": None if level is None or level == "" else float(level),
            "tag": tag,
            "saved_at": datetime.now(tz=IST).isoformat(timespec="seconds"),
        }
        data["puzzle"] = puzzle
        _atomic_write(self._practice_path(), data)
        return {"saved": True, "id": puzzle["id"]}

    def reveal_practice(self) -> dict[str, Any]:
        from app.paper.assist_book import score_practice, tag_accuracy

        data = self._practice()
        puzzle = data.get("puzzle")
        if puzzle is None or not puzzle.get("read"):
            raise ValueError("save the read before the reveal")
        read = puzzle["read"]
        score = score_practice(float(puzzle["signal_close"]), puzzle["hidden"], direction=read["direction"], level=read.get("level"))
        row = {**read, "id": puzzle["id"], "signal_close": puzzle["signal_close"], **score}
        reads = list(data.get("reads") or [])
        reads.append(row)
        data["reads"] = reads
        data["puzzle"] = None
        _atomic_write(self._practice_path(), data)
        return {"candles": puzzle["hidden"], "score": score, "accuracy": tag_accuracy(reads)}

    def state(self, *, today: str) -> dict[str, Any]:
        from app.paper.assist_book import tag_accuracy

        settings = self.load_settings()
        records = self.load()
        return {
            "settings": settings,
            "guard": guard_banner(records, today=today, settings=settings),
            "open": [r for r in records if r.get("kind") == "taken" and r.get("exit_time") is None],
            "records": records,
            "running": running_rows(records),
            "practice": tag_accuracy(self._practice().get("reads") or []),
            "today": today,
            "trails": [],
        }
