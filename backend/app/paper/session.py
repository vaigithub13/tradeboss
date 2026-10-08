"""One paper-trading day: closed live bars in, strategy signals out, paper fills and the book updated.

The strategy is the same object a backtest runs (`Strategy.on_bar`), fed only closed bars in order.
The index signal picks the option: BUY -> ATM call, SELL -> ATM put, both on the nearest weekly. ATM is taken at
the fill moment, as the backtest does: the live index price for a market signal (and for a stop crossed while its
bar was being decided), the stop level for a stop the index crosses.
A signal decided at a closed bar is filled at the live quote at that moment (`now_ms`), or the model
if there is no fresh quote. A signal that cannot be filled is recorded as unfilled and opens nothing.
Open positions are squared off at 15:15, like the backtest's default square-off.

Exit rules (app/exits/rules.py, the backtest's levels and hit rule): `premium` checks every traded price of the
open contract against -stop / +target of the entry fill and sells at the bid; the index position stays, as in the
backtest's option overlay, so a same-direction entry afterwards is cancelled. `atr` checks every index price
against the bracket from the index fill (the strategy's signal carries the ATR distances) and sells at the bid;
the index position is then flat. Opposite signals and the 15:15 square-off still close trades.

A bar rebuilt after a feed gap (see `ClosedBars`) is decided when it completes, late; its signals say so in their
note ("late after gap") and are traded at the live price then, unless that is at or after 15:15.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import date
from typing import Any

from app.backtest.context import History, PastBars, PositionView
from app.backtest.contracts import Signal
from app.backtest.costs import CostTable
from app.backtest.engine import BacktestConfig
from app.exits.report import reason_counts
from app.exits.rules import ExitRule, first_hit, index_levels, premium_levels
from app.live.model import ist_ms_of_day
from app.live.spreads.decode import DepthQuote
from app.options.contract import OptionContract
from app.paper.bars import ClosedBars
from app.paper.book import Fill, PaperBook, Position, fill, summarise
from app.paper.pricing import MODEL_SLIPPAGE_POINTS
from app.paper.quotes import QuoteBook
from app.strategies.pine_common import minute_of

log = logging.getLogger("tradeboss.paper")

SQUARE_OFF_MIN = 15 * 60 + 15
LATE_NOTE = "late after gap: the bar was rebuilt from backfilled minutes and decided when it completed"
OHLCV = ("time", "open", "high", "low", "close", "volume")
TICK_MEMORY_MS = 10 * 60_000  # longer than any bar's decision lag
WARMUP_BARS = BacktestConfig().warmup_bars  # the backtest warms its strategy on this many bars before the day
TAG_REASON = {
    "LE": "log XZ crossed above 0: buy",
    "SE": "log XZ crossed below 0: sell",
}


def warmup_before(prior: list[dict[str, Any]], day_start: int) -> list[dict[str, Any]]:
    """The last WARMUP_BARS closed bars before `day_start`, exactly the window the backtest warms on."""
    before = [b for b in prior if int(b["time"]) < day_start]
    return before[-WARMUP_BARS:]


class _Ctx:
    """The fields a strategy's on_bar may read (see app/backtest/context.py StrategyContext)."""

    def __init__(self, timeframe: str, symbol: str, position: PositionView, history: History | None = None,
                 cancel: Callable[[str | None], int] | None = None) -> None:
        self.timeframe = timeframe
        self.symbol = symbol
        self.position = position
        self.base_minutes = 1
        self.cash = 0.0
        self.lot_size = 1
        self.open_orders: list[dict[str, Any]] = []
        self.bars = None if history is None else PastBars(history)
        self._cancel = cancel

    def cancel_working(self, tag: str | None = None) -> int:
        return 0 if self._cancel is None else self._cancel(tag)

    def indicator(self, itype: str, **params: Any) -> Any:
        raise NotImplementedError("paper trading takes no indicator views yet")


class PaperSession:
    def __init__(
        self,
        *,
        day: date,
        strategy: Any,
        choose: Callable[[str, float, date], OptionContract],
        key_for: Callable[[str], str | None],
        quotes: QuoteBook,
        cost_table: CostTable,
        model_price: Callable[[OptionContract, float, int], float | None] | None = None,
        model_slippage: float = MODEL_SLIPPAGE_POINTS,
        bar_minutes: int = 5,
        lots: int = 1,
        symbol: str = "NIFTY50",
        exit_rule: ExitRule | None = None,
    ) -> None:
        if lots < 1:
            raise ValueError("lots must be >= 1")
        self.day = day
        self.strategy = strategy
        self.choose = choose
        self.key_for = key_for
        self.quotes = quotes
        self.cost_table = cost_table
        self.model_price = model_price
        self.model_slippage = model_slippage
        self.wanted: set[str] = set()  # option keys this session needs quoted (the live feed subscribes them)
        self.lots = lots
        self.symbol = symbol
        self.exit_rule = exit_rule
        self.timeframe = f"{bar_minutes}m"
        self.bars = ClosedBars(bar_minutes)
        self.book = PaperBook()
        self.signals: list[dict[str, Any]] = []
        self.shown: list[dict[str, Any]] = []  # the closed bars the strategy saw, live
        self.warm_bars = 0
        self._bar_s = bar_minutes * 60
        self._side = 0  # the index position as the strategy sees it: +1 long, -1 short
        self._spot: float | None = None
        self._contract_of: dict[str, OptionContract] = {}
        self.resume_after: int | None = None  # bars at or before this were decided before a restart (see restore)
        self.last_bar_time: int | None = None
        self.history = History()  # warm-up and live closed bars: the strategy's ctx.bars
        #: stop orders armed by the strategy, by tag: {side, price, tag, active_ms}; filled by on_index_tick
        self.working: dict[str, dict[str, Any]] = {}
        self.uses_stops = False  # the strategy arms stop orders: the end-of-day check compares fills
        #: recent index prices (ms, price): a level crossed while a bar was being decided fills on arming
        self._ticks: deque[tuple[int, float]] = deque()
        self._fresh: set[str] = set()  # stops armed by the bar being decided now
        self._given_up_seen = 0
        #: where the signals and trades being made now come from: "live", or "replay" while the runner catches up
        #: from the recording (and for a whole replay run)
        self.source = "live"
        #: the sources the day's totals count: live only, unless the whole run is a replay
        self.counted_sources: tuple[str, ...] = ("live",)
        self._last_index: float | None = None  # the newest index price (the Nifty level recorded at an exit)

    # ---------------------------------------------------------------- input
    def warm(self, bars: Iterable[dict[str, Any]]) -> None:
        """Run earlier closed bars through the strategy so its state is built, exactly as a backtest does
        before its first tradable bar: the position is flat and no signal is acted on."""
        for bar in bars:
            self.history.append(dict(bar))  # type: ignore[arg-type]
            ctx = _Ctx(self.timeframe, self.symbol, PositionView(side=0, lots=0, units=0, avg_price=0.0),
                       self.history, cancel=lambda _tag: 0)
            self.strategy.on_bar(dict(bar), ctx)
            self.warm_bars += 1

    def resume_state(self, bars: Iterable[dict[str, Any]]) -> None:
        """Rebuild the strategy's state and position side from bars already decided before a restart,
        without trading them. Used when the day's recording is not available to replay."""
        for bar in bars:
            self._step(bar)

    def restore(self, saved: dict[str, Any]) -> None:
        """Take the state of a day file written by an earlier run of this session: signals, trades, the open
        position, the closed bars and the last bar that was decided. The strategy is not run here."""
        self.signals = list(saved.get("signals", []))
        self.book.trades = list(saved.get("trades", []))
        self.shown = list(saved.get("bars", []))
        self.warm_bars = int(saved.get("warm_bars", 0))
        self.uses_stops = bool(saved.get("uses_stops", False))
        self.bars.incomplete = set(saved.get("incomplete_bars", []))
        self.bars.late = set(saved.get("late_bars", []))
        self.bars.given_up = list(saved.get("given_up_bars", []))
        self.resume_after = saved.get("last_bar_time")
        self.last_bar_time = self.resume_after
        o = saved.get("open")
        if o is not None:
            entry = Fill(**{**o["entry"], "charges": dict(o["entry"]["charges"])})
            pos = Position(
                direction=o["direction"], key=o["key"], symbol=o["symbol"], kind=o["kind"], strike=o["strike"],
                expiry=date.fromisoformat(o["expiry"]), lots=o["lots"], units=o["units"], lot_size=o["lot_size"],
                entry=entry, index_entry_time=o["index_entry_time"], reason=o["reason"],
                levels=o.get("levels"), signal_time=o.get("signal_time"), trigger_index=o.get("trigger_index"),
                index_entry=o.get("index_entry"), delta=o.get("delta"), source=o.get("source", "live"),
            )
            self.book.position = pos
            # The contract is only needed to price an exit by the model: its kind, strike and expiry are kept.
            self._contract_of[pos.key] = OptionContract(
                kind=pos.kind, strike=pos.strike, expiry=pos.expiry, cycle="weekly", lot_size=pos.lot_size,
                symbol=pos.symbol, step=0, step_verification="restored",
            )
            self.wanted.add(pos.key)

    def on_depth(self, quotes: Iterable[DepthQuote]) -> None:
        self.quotes.on_depth(quotes)

    def on_index_minute(self, minute: dict[str, Any], *, now_ms: int) -> list[dict[str, Any]]:
        """Feed one exchange-final 1-minute index bar (source i1 or official). `now_ms` is the feed's current time.

        Returns the signal records made by the bars this minute closed."""
        out: list[dict[str, Any]] = []
        self._spot = float(minute["close"])
        for bar in self.bars.on_minute(minute):
            out += self._on_closed_bar(bar, now_ms)
        for t in self.bars.given_up[self._given_up_seen:]:
            log.warning("paper: the %s bar at %s never completed after a feed gap: not decided (incomplete)",
                        self.timeframe, _hhmm(t))
        self._given_up_seen = len(self.bars.given_up)
        return out

    def on_clock(self, now_ms: int) -> bool:
        """The feed's exchange time, every frame. At 15:15:00 the open position is squared off at that moment's
        quote, as the backtest squares off at 15:15; it does not wait for an exchange-final minute.
        True when this call closed the position."""
        if ist_ms_of_day(now_ms) >= SQUARE_OFF_MIN * 60_000 and self.working:
            self.working.clear()  # nothing fills after the square-off
        if self.book.position is None or ist_ms_of_day(now_ms) < SQUARE_OFF_MIN * 60_000:
            return False
        if self.resume_after is not None and now_ms < (self.resume_after + self._bar_s) * 1000:
            return False  # a catch-up frame from before the restart: that time was already decided
        self._square_off(now_ms)
        return self.book.position is None

    def end_of_day(self) -> None:
        """The session has ended. A bar still short of its minutes is marked incomplete, never traded."""
        self.bars.end_of_day()

    # ------------------------------------------------------------ decisions
    def _on_closed_bar(self, bar: dict[str, Any], now_ms: int) -> list[dict[str, Any]]:
        t = int(bar["time"])
        if self.resume_after is not None and t <= self.resume_after:
            self._step(bar)  # decided before the restart: the state and side follow, nothing is recorded again
            return []
        self.last_bar_time = t
        self.shown.append(bar)
        clean = {k: bar[k] for k in OHLCV if k in bar}  # the strategy sees the bar, not the gap flag
        out = []
        before = self._side
        for sig in self._step(clean):
            if sig.type == "MARKET":
                out.append(self._act(sig, clean, now_ms, side_before=before))
        out += self._fill_crossed_on_arming(now_ms)
        if bar.get("late_after_gap"):
            for rec in out:
                rec["note"] = LATE_NOTE + (f"; {rec['note']}" if rec["note"] else "")
        return out

    def _fill_crossed_on_arming(self, now_ms: int) -> list[dict[str, Any]]:
        """A stop is live in the backtest from its bar's end; paper learns it when the bar is decided, about a
        minute later. If the index traded through the level in between, fill now at the live price."""
        fresh, self._fresh = self._fresh, set()
        if not fresh or not self._tradable(now_ms):
            return []
        for tag in sorted(fresh):
            order = self.working.get(tag)
            if order is None:
                continue
            seen = [p for t, p in self._ticks if order["active_ms"] <= t <= now_ms]
            crossed = any(p >= order["price"] for p in seen) if order["side"] == "BUY" else any(
                p <= order["price"] for p in seen)
            if crossed and self._ticks:
                note = "the level was crossed before the order was armed (bar decision lag): filled when armed"
                return self._trigger(tag, order, self._ticks[-1][1], now_ms, note=note)
        return []

    def _tradable(self, now_ms: int) -> bool:
        if ist_ms_of_day(now_ms) >= SQUARE_OFF_MIN * 60_000:
            return False
        return self.resume_after is None or now_ms >= (self.resume_after + self._bar_s) * 1000

    def _step(self, bar: dict[str, Any]) -> list[Signal]:
        """The strategy decides on a closed bar. Market signals move its side now; a stop is armed (by tag) and
        moves the side only when on_index_tick fills it."""
        self.history.append(dict(bar))  # type: ignore[arg-type]
        ctx = _Ctx(self.timeframe, self.symbol,
                   PositionView(side=self._side, lots=1 if self._side else 0, units=0, avg_price=0.0),
                   self.history, cancel=self._cancel_working)
        signals = list(self.strategy.on_bar(bar, ctx))
        for sig in signals:
            if sig.type == "MARKET":
                self._side = {"BUY": 1, "SELL": -1, "EXIT": 0}[sig.side]
            else:
                self._arm(sig, bar)
        return signals

    def _cancel_working(self, tag: str | None) -> int:
        if tag is None:
            n = len(self.working)
            self.working.clear()
            return n
        return 1 if self.working.pop(tag, None) is not None else 0

    def _arm(self, sig: Signal, bar: dict[str, Any]) -> None:
        """A stop works from the end of the bar that placed it, as in the backtest. A fill that could only come at
        or after 15:15 is not placed (the backtest blocks it as after_square_off)."""
        if sig.price is None or sig.type != "SL":
            return
        end = int(bar["time"]) + self._bar_s
        if minute_of(end) >= SQUARE_OFF_MIN:
            return
        self.uses_stops = True
        tag = sig.tag or sig.side
        self.working[tag] = {"side": sig.side, "price": float(sig.price), "tag": tag, "active_ms": end * 1000,
                             "signal_time": int(bar["time"]), "stop_points": sig.stop_points,
                             "target_points": sig.target_points}
        self._fresh.add(tag)

    def on_index_tick(self, price: float, *, now_ms: int) -> list[dict[str, Any]]:
        """The live index price (exchange time). Fills the first working stop it crosses: the option is bought at
        the live ask now. Returns the signal records it made."""
        self._ticks.append((now_ms, float(price)))
        self._last_index = float(price)
        while self._ticks and self._ticks[0][0] < now_ms - TICK_MEMORY_MS:
            self._ticks.popleft()
        if self.exit_rule is not None:
            self._spot = float(price)  # the model fallback for an exit prices at the live index
        self._check_index_exit(float(price), now_ms)  # a protective level first, as the broker does
        if not self.working or not self._tradable(now_ms):
            return []  # (before a restart's last decided bar: that time was already decided)
        for tag, order in sorted(self.working.items(), key=lambda kv: kv[1]["active_ms"]):
            if now_ms < order["active_ms"]:
                continue
            crossed = price >= order["price"] if order["side"] == "BUY" else price <= order["price"]
            if crossed:
                return self._trigger(tag, order, float(price), now_ms)
        return []

    def _trigger(self, tag: str, order: dict[str, Any], price: float, now_ms: int, note: str = "") -> list[dict[str, Any]]:
        del self.working[tag]
        direction = "LONG" if order["side"] == "BUY" else "SHORT"
        if self._holds(direction):
            return []  # already in that position: the broker cancels it (pyramiding 0)
        bar_time = (now_ms // 1000) - ((now_ms // 1000) - _session_anchor(now_ms)) % self._bar_s
        rec: dict[str, Any] = {
            "time": bar_time, "decided_at_ms": now_ms, "side": order["side"], "index_price": price,
            "order_price": order["price"], "reason": f"{order['side'].lower()} stop {tag} at {order['price']:.2f}",
            "symbol": None, "key": None, "status": "unfilled", "fill_source": None, "fill_price": None, "note": note,
            "source": self.source,
        }
        self.signals.append(rec)
        spot = price if note else float(order["price"])  # a late fill is at the live price, a stop fill at its level
        self._enter(rec, direction, spot, now_ms, index_time=now_ms // 1000, close_reason="reverse",
                    signal_time=order.get("signal_time"), trigger=float(order["price"]),
                    points=(order.get("stop_points"), order.get("target_points")))
        if rec["status"] == "filled":
            self._side = 1 if direction == "LONG" else -1
        return [rec]

    def _fill_time_spot(self, bar: dict[str, Any]) -> float:
        """The live index price now, for a market fill; the bar's close when no tick has come since the bar ended."""
        end_ms = (int(bar["time"]) + self._bar_s) * 1000
        if self._ticks and self._ticks[-1][0] >= end_ms:
            return self._ticks[-1][1]
        return float(bar["close"])

    def _holds(self, direction: str, side: int | None = None) -> bool:
        """Already in that position? With an exit rule the index position decides (after a premium exit the
        option is sold but the index trade is open, as in the backtest); without one, the paper book does."""
        if self.exit_rule is None:
            return self.book.position is not None and self.book.position.direction == direction
        held = self._side if side is None else side
        return held == (1 if direction == "LONG" else -1)

    def _act(self, sig: Signal, bar: dict[str, Any], now_ms: int, side_before: int | None = None) -> dict[str, Any]:
        spot = self._fill_time_spot(bar)
        rec: dict[str, Any] = {
            "time": int(bar["time"]),
            "decided_at_ms": now_ms,
            "side": sig.side,
            "index_price": spot,
            "reason": TAG_REASON.get(sig.tag, sig.tag),
            "symbol": None,
            "key": None,
            "status": "unfilled",
            "fill_source": None,
            "fill_price": None,
            "note": "",
            "source": self.source,
        }
        self.signals.append(rec)
        if sig.side == "EXIT":
            rec["status"] = "exit"
            if self.book.position is not None:
                self._square_off(now_ms, reason="exit")
                rec["status"] = "filled"
            return rec
        if minute_of(int(bar["time"]) + self._bar_s) >= SQUARE_OFF_MIN or not self._tradable(now_ms):
            # the fill would come on the next bar, at or after 15:15: the backtest blocks it too (after_square_off);
            # a bar decided late after a gap can also be decided after 15:15
            rec["note"] = "after the 15:15 square-off"
            return rec
        direction = "LONG" if sig.side == "BUY" else "SHORT"
        if self._holds(direction, side_before):
            rec["status"] = "skipped"
            rec["note"] = "already in that position"
            return rec

        self._enter(rec, direction, spot, now_ms, index_time=int(bar["time"]), close_reason="signal",
                    signal_time=int(bar["time"]), trigger=spot, points=(sig.stop_points, sig.target_points))
        return rec

    def _enter(self, rec: dict[str, Any], direction: str, spot: float, now_ms: int, *, index_time: int,
               close_reason: str, signal_time: int | None = None, trigger: float | None = None,
               points: tuple[float | None, float | None] = (None, None)) -> None:
        """Buy the ATM option for `direction` at the live ask (closing an open one at the bid first)."""
        contract = self.choose(direction, spot, self.day)
        key = self.key_for(contract.symbol)
        if key is None:
            rec["note"] = "contract is not in the instrument master"
            return
        units = self.lots * contract.lot_size
        entry = self._fill("BUY", key, contract, units, spot, now_ms)
        if entry is None:
            rec["note"] = "no quote and no model price for the contract"
            return
        if self.book.position is not None:
            exit_fill = self._fill("SELL", self.book.position.key, self._contract_of[self.book.position.key],
                                   self.book.position.units, spot, now_ms)
            if exit_fill is None:
                rec["note"] = "could not close the open position: no quote and no model price"
                return
            self.book.close(exit_fill, index_exit_time=index_time, reason=close_reason, index_exit=spot)
        self._contract_of[key] = contract
        self.wanted.add(key)
        self.book.open(Position(
            direction=direction, key=key, symbol=contract.symbol, kind=contract.kind, strike=contract.strike,
            expiry=contract.expiry, lots=self.lots, units=units, lot_size=contract.lot_size, entry=entry,
            index_entry_time=index_time, reason=rec["reason"],
            levels=self._levels(direction, spot, entry.price, points), signal_time=signal_time,
            trigger_index=trigger, index_entry=spot,
            delta=self._delta(contract, spot, now_ms) if self.exit_rule is not None else None,
            source=self.source,
        ))
        rec.update(status="filled", symbol=contract.symbol, key=key, fill_source=entry.source,
                   fill_price=entry.price)

    # ------------------------------------------------------------ exit rules
    def _levels(self, direction: str, index_fill: float, premium_fill: float,
                points: tuple[float | None, float | None]) -> dict[str, float | None] | None:
        rule = self.exit_rule
        if rule is None:
            return None
        levels: dict[str, float | None] = {"index_stop": None, "index_target": None,
                                           "premium_stop": None, "premium_target": None}
        if rule.kind == "premium":
            levels["premium_stop"], levels["premium_target"] = premium_levels(premium_fill, rule)
        elif points[0] is not None and points[1] is not None:
            levels["index_stop"], levels["index_target"] = index_levels(direction, index_fill, points[0], points[1])
        return levels

    def _delta(self, contract: OptionContract, spot: float, now_ms: int) -> float | None:
        """The model's delta at entry (for the report's estimated levels), by a small difference in the index."""
        if self.model_price is None:
            return None
        up, down = self.model_price(contract, spot + 5.0, now_ms), self.model_price(contract, spot - 5.0, now_ms)
        if up is None or down is None:
            return None
        return round((float(up) - float(down)) / 10.0, 4)

    def on_option_tick(self, key: str, price: float, *, now_ms: int) -> bool:
        """A traded price of an option (exchange time). Under a premium rule it is checked against the open
        contract's stop and target; a hit sells at the bid. True when this closed the position."""
        p = self.book.position
        if (self.exit_rule is None or self.exit_rule.kind != "premium" or p is None or p.key != key
                or p.levels is None or not self._tradable(now_ms)):
            return False
        hit = first_hit(p.levels.get("premium_stop"), p.levels.get("premium_target"), price, price, price, long=True)
        if hit is None:
            return False
        return self._exit_on_rule(f"premium_{hit[0]}", now_ms)

    def _check_index_exit(self, price: float, now_ms: int) -> bool:
        p = self.book.position
        if (self.exit_rule is None or self.exit_rule.kind != "atr" or p is None or p.levels is None
                or p.levels.get("index_stop") is None or not self._tradable(now_ms)):
            return False
        hit = first_hit(p.levels["index_stop"], p.levels.get("index_target"), price, price, price,
                        long=p.direction == "LONG")
        if hit is None or not self._exit_on_rule(f"index_{hit[0]}", now_ms):
            return False
        self._side = 0  # the bracket closed the index position, as the broker's does
        return True

    def _exit_on_rule(self, reason: str, now_ms: int) -> bool:
        p = self.book.position
        assert p is not None
        exit_fill = self._fill("SELL", p.key, self._contract_of[p.key], p.units, self._spot or p.entry.price, now_ms)
        if exit_fill is None:
            log.warning("paper: %s on %s but no quote and no model price: still open, checked again", reason, p.symbol)
            return False
        self.book.close(exit_fill, index_exit_time=now_ms // 1000, reason=reason, index_exit=self._index_now())
        return True

    def _index_now(self) -> float | None:
        """The Nifty level now: the newest index tick, else the newest final minute's close."""
        return self._last_index if self._last_index is not None else self._spot

    def report_rows(self) -> list[dict[str, Any]]:
        """The trade report (app/exits/report.py): one row per closed trade."""
        from app.paper.trades import trade_report_row

        return [trade_report_row(t, self.exit_rule) for t in self.book.trades]

    def prepare(self, spot: float) -> None:
        """Ask for the ATM call and put of the nearest weekly, so a signal finds its quote already subscribed."""
        for direction in ("LONG", "SHORT"):
            key = self.key_for(self.choose(direction, spot, self.day).symbol)
            if key is not None:
                self.wanted.add(key)

    def close_now(self, now_ms: int, reason: str) -> None:
        """Square off the open position at the current quote (used when the user stops the strategy)."""
        self._square_off(now_ms, reason=reason)

    def _square_off(self, now_ms: int, reason: str = "square_off") -> None:
        p = self.book.position
        if p is None:
            return
        contract = self._contract_of[p.key]
        exit_fill = self._fill("SELL", p.key, contract, p.units, self._spot or p.entry.price, now_ms)
        if exit_fill is None:
            self.signals.append({
                "time": None, "decided_at_ms": now_ms, "side": "EXIT", "index_price": self._spot,
                "reason": "square-off", "symbol": p.symbol, "key": p.key, "status": "unfilled",
                "fill_source": None, "fill_price": None, "note": "square-off: no quote and no model price",
                "source": self.source,
            })
            return
        self.book.close(exit_fill, index_exit_time=now_ms // 1000, reason=reason, index_exit=self._index_now())

    def _fill(self, side: str, key: str, contract: OptionContract, units: int, spot: float,
              now_ms: int) -> Fill | None:
        model = None
        if self.model_price is not None:
            model = lambda: self.model_price(contract, spot, now_ms)  # noqa: E731
        return fill(side, key=key, quotes=self.quotes, now_ms=now_ms, units=units, day=self.day,
                    cost_table=self.cost_table, model_price=model, model_slippage=self.model_slippage)

    # ---------------------------------------------------------------- output
    def mark(self, now_ms: int) -> dict[str, Any] | None:
        """Live P&L of the open paper position, marked at the live bid. None when there is no position or no bid."""
        p = self.book.position
        if p is None:
            return None
        q = self.quotes.at(p.key, now_ms)
        if q is None or q.bid is None:
            return {"symbol": p.symbol, "source": None, "gross": None, "net": None}
        m = self.book.mark(bid=q.bid, cost_table=self.cost_table, day=self.day)
        return {"symbol": p.symbol, "source": "quote", **m}

    def live_bars(self) -> dict[int, dict[str, Any]]:
        return {int(b["time"]): b for b in self.shown}

    def snapshot(self) -> dict[str, Any]:
        counted = self.counted_sources
        entries = [s for s in self.signals if s["side"] in ("BUY", "SELL") and s.get("source", "live") in counted]
        report = self.report_rows()
        live_trades = [t for t in self.book.trades if t.get("source", "live") in counted]
        other_trades = [t for t in self.book.trades if t.get("source", "live") not in counted]
        unfilled = sum(1 for s in self.signals if s["status"] == "unfilled" and s.get("source", "live") in self.counted_sources)
        p = self.book.position
        return {
            "strategy": {"name": getattr(self.strategy, "name", "?"), "params": dict(getattr(self.strategy, "params", {}))},
            "timeframe": self.timeframe,
            "lots": self.lots,
            "signals": self.signals,
            "trades": self.book.trades,
            "open": None if p is None else {**asdict(p), "expiry": p.expiry.isoformat(), "entry": asdict(p.entry)},
            "summary": {**summarise(live_trades, signals=len(entries), unfilled=unfilled),
                        "exits": reason_counts([r for r in report if r["source"] in counted]),
                        **({"replay": summarise(other_trades, signals=0, unfilled=0)} if other_trades else {})},
            "source": self.source if self.counted_sources != ("live",) else "live",
            "report": report,
            "exit_rule": None if self.exit_rule is None else self.exit_rule.to_dict(),
            "closed_bars": len(self.shown),
            "bars": self.shown,
            "last_bar_time": self.last_bar_time,
            "working": sorted(self.working.values(), key=lambda o: o["tag"]),
            "uses_stops": self.uses_stops,
            "warm_bars": self.warm_bars,
            "incomplete_bars": sorted(self.bars.incomplete),
            "late_bars": sorted(self.bars.late),
            "given_up_bars": list(self.bars.given_up),
            "pre_open_ignored": self.bars.pre_open_ignored,
        }


def _hhmm(t: int) -> str:
    return f"{minute_of(t) // 60:02d}:{minute_of(t) % 60:02d}"


def _session_anchor(now_ms: int) -> int:
    """09:15 IST of `now_ms`'s day, in unix seconds (the bars are anchored there)."""
    t = now_ms // 1000
    return t - (t + 19_800) % 86_400 + 9 * 3600 + 15 * 60
