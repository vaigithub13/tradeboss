"""BacktestBroker: orders, intrabar matching, positions, trades (critical module).

Matching rules (approved, see tests/test_bt_fills.py, test_bt_intrabar.py):

* an order may fill from `min_t` on (the bar after the decision), never on the bar it was placed on;
* MARKET fills at the open of the first eligible bar; a stop (SL = stop-market) fills when price
  TOUCHES its level, at the level, or at the OPEN when the bar gaps through it; a LIMIT fills only
  when price trades STRICTLY through its level, at the level, or at the (better) open on a gap;
* inside one bar, orders that gap at the open go first (market first, then gaps, by order id), then
  orders triggered by the bar's range: a protective stop before anything else, then the level
  nearest the open. When more than one order is triggered in the same bar the fill is flagged
  `ambiguous` (stop-before-target is the pessimistic assumption);
* an entry that fills at the open lets its bracket children work in the SAME bar; an entry that
  triggers inside the bar lets its stop work (flagged ambiguous) but its target only from the next bar;
* a fill never changes the past: everything here is driven bar by bar;
* live timing (the engine's `live_timing`): an order works from one minute after its bar ended. A stop whose
  level the index touched in that lag minute fills at the next minute's open and is flagged `late` (paper learns
  the level then and fills at the live price). A cancel by the strategy takes effect at the same minute
  (`cancel_delay`), so the level it replaces still works through the lag minute, as in paper.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.backtest.contracts import Signal
from app.backtest.context import PositionView
from app.backtest.costs import CostModel, Slippage
from app.backtest.result import Trade
from app.backtest.sources import ist_date
from app.exits.rules import index_levels

CENT = Decimal("0.01")
PRICE_Q = Decimal("0.000001")
_REASON = {"MARKET": "market", "SL": "stop", "LIMIT": "limit"}


def _d(x: float) -> Decimal:
    return Decimal(str(x))


@dataclass
class Order:
    id: int
    side: str
    type: str
    price: float | None
    lots: int
    tag: str
    oco: str | None
    reduce_only: bool
    kind: str  # entry | exit | stop | target
    min_t: int
    stop: float | None = None
    target: float | None = None
    ambiguous_in: int | None = None
    stop_points: float | None = None
    target_points: float | None = None
    lag_from: int | None = None  # live timing: start of the lag minute(s) before min_t
    lag_crossed: bool = False  # the level was touched in the lag minute: fill at the open when it works
    cancel_at: int | None = None  # a deferred cancel by the strategy (live timing)
    placed_t: int | None = None  # the decision time (bar end) that placed it


@dataclass
class _Open:
    direction: int
    entry_time: int
    entry_tag: str
    lot_size: int
    entries: list[tuple[Decimal, int]] = field(default_factory=list)
    exits: list[tuple[Decimal, int]] = field(default_factory=list)
    charges: dict[str, Decimal] = field(default_factory=dict)
    slip: Decimal = Decimal("0")
    gap: bool = False
    ambiguous: bool = False
    optimistic: bool = False
    late: bool = False
    entry_at_open: bool = True
    exit_at_open: bool = True
    signal_time: int | None = None
    stop_level: float | None = None
    target_level: float | None = None


class BacktestBroker:
    """Implements the Broker contract (place / cancel / positions) for a backtest."""

    def __init__(self, *, cost_model: CostModel, slippage: Slippage, lot_resolver: Callable[[date], int],
                 events: list[dict[str, Any]], counters: dict[str, int]) -> None:
        self.cost_model, self.slippage, self.lot_resolver = cost_model, slippage, lot_resolver
        self.events, self.counters = events, counters
        self._working: dict[int, Order] = {}
        self._next_id = 1
        self.lots = 0  # signed
        self.lot_size = 1
        self._avg = Decimal("0")
        self._open: _Open | None = None
        self.trades: list[Trade] = []
        self._realized = Decimal("0")
        self.pyramiding: int | None = None  # 0: an opposite entry reverses, a same-side entry does not add
        self.cancel_delay = 0  # live timing: a strategy's cancel takes effect this many seconds after it is asked

    # ------------------------------------------------------------------ views (for ctx)
    def positions(self) -> PositionView:
        return self.position_view()

    def position_view(self) -> PositionView:
        side = 1 if self.lots > 0 else -1 if self.lots < 0 else 0
        return PositionView(side, abs(self.lots), abs(self.lots) * self.lot_size, float(self._avg) if self.lots else 0.0)

    def realized_net(self) -> float:
        return float(self._realized)

    def open_orders(self) -> list[dict[str, Any]]:
        return [
            {"id": o.id, "side": o.side, "type": o.type, "price": o.price, "lots": o.lots, "tag": o.tag,
             "reduce_only": o.reduce_only}
            for o in sorted(self._working.values(), key=lambda x: x.id)
            if o.cancel_at is None
        ]

    # ------------------------------------------------------------------ placing / cancelling
    def _event(self, kind: str, t: int, **kw: Any) -> None:
        self.events.append({"kind": kind, "t": int(t), **kw})

    def place(self, signal: Signal, *, t: int, ref_price: float, min_t: int,
              block: tuple[str, bool] | None = None, lag_from: int | None = None, **_: Any) -> int | None:
        """Register a signal as a working order. `block` = (reason, also_reduce_only): the order is
        recorded but can never fill (no next bar / after square-off) and is reported as `unfilled`."""
        pos = self.lots
        side = signal.side
        reduce_only = False
        lots = signal.qty
        if side == "EXIT":
            if pos == 0:
                self._reject(t, signal, "no_position")
                return None
            side = "SELL" if pos > 0 else "BUY"
            lots = min(signal.qty, abs(pos))
            reduce_only = True
        elif (
            self.pyramiding != 0
            and signal.type in ("LIMIT", "SL")
            and pos != 0
            and (side == "SELL") == (pos > 0)
        ):
            reduce_only = True  # a resting order against the position only ever reduces it
        if signal.stop is not None or signal.target is not None:
            ref = signal.price if signal.type != "MARKET" and signal.price is not None else ref_price
            buy = side == "BUY"
            ok = (signal.stop is None or (signal.stop < ref if buy else signal.stop > ref)) and (
                signal.target is None or (signal.target > ref if buy else signal.target < ref)
            )
            if not ok:
                self._reject(t, signal, "bad_bracket")
                return None
        order = Order(self._next_id, side, signal.type, signal.price, lots, signal.tag, signal.oco, reduce_only,
                      "exit" if reduce_only else "entry", min_t, signal.stop, signal.target,
                      stop_points=signal.stop_points, target_points=signal.target_points,
                      lag_from=lag_from if signal.type == "SL" else None, placed_t=t)
        self._next_id += 1
        self.counters["orders"] += 1
        self._event("order_placed", t, id=order.id, side=side, type=signal.type, price=signal.price, lots=lots,
                    tag=signal.tag, reduce_only=reduce_only)
        if block is not None and (block[1] or not reduce_only):
            self.counters["unfilled"] += 1
            self._event("unfilled", t, id=order.id, side=side, tag=signal.tag, reason=block[0])
            return None
        self._working[order.id] = order
        return order.id

    def _reject(self, t: int, signal: Signal, reason: str) -> None:
        self.counters["rejected"] += 1
        self._event("order_rejected", t, side=signal.side, type=signal.type, tag=signal.tag, reason=reason)
        return None

    def _drop(self, order: Order, t: int, reason: str) -> None:
        self._working.pop(order.id, None)
        self.counters["cancelled"] += 1
        self._event("order_cancelled", t, id=order.id, side=order.side, tag=order.tag, reason=reason)

    def cancel(self, order_id: int, *, t: int = 0, reason: str = "cancelled", **_: Any) -> bool:
        order = self._working.get(order_id)
        if order is None:
            return False
        self._drop(order, t, reason)
        return True

    def cancel_matching(self, tag: str | None, t: int) -> int:
        victims = [o for o in sorted(self._working.values(), key=lambda x: x.id)
                   if (tag is None or o.tag == tag) and o.cancel_at is None]
        for o in victims:
            if self.cancel_delay:
                o.cancel_at = t + self.cancel_delay  # still works until paper would have replaced it
            else:
                self._drop(o, t, "cancelled")
        return len(victims)

    def _expire_cancels(self, ts: int) -> None:
        for o in [o for o in sorted(self._working.values(), key=lambda x: x.id)
                  if o.cancel_at is not None and o.cancel_at <= ts]:
            self._drop(o, o.cancel_at or ts, "cancelled")

    def _note_lag_crosses(self, ts: int, hi: float, lo: float) -> None:
        """Live timing: a stop not working yet whose level this lag minute touched fills when it starts working."""
        for o in self._working.values():
            if o.lag_from is not None and o.lag_from <= ts < o.min_t and self._range_trigger(o, hi, lo) is not None:
                o.lag_crossed = True

    def cancel_all(self, t: int, reason: str | None) -> None:
        for o in sorted(self._working.values(), key=lambda x: x.id):
            if reason is None:
                self._working.pop(o.id, None)
            else:
                self._drop(o, t, reason)

    # ------------------------------------------------------------------ matching
    @staticmethod
    def _open_fill(o: Order, op: float) -> tuple[float, bool] | None:
        """Price (and 'gapped') if the order fills at the open of the bar."""
        if o.type == "MARKET":
            return op, False
        p = o.price
        assert p is not None
        if o.type == "SL":
            if o.side == "BUY" and op >= p:
                return op, op > p
            if o.side == "SELL" and op <= p:
                return op, op < p
        else:
            if o.side == "BUY" and op < p:
                return op, True
            if o.side == "SELL" and op > p:
                return op, True
        return None

    @staticmethod
    def _range_trigger(o: Order, hi: float, lo: float) -> float | None:
        p = o.price
        if p is None or o.type == "MARKET":
            return None
        if o.type == "SL":
            if (o.side == "BUY" and hi >= p) or (o.side == "SELL" and lo <= p):
                return p
        else:
            if (o.side == "BUY" and lo < p) or (o.side == "SELL" and hi > p):
                return p
        return None

    def _fill_opens(self, ts: int, op: float, base_s: int) -> None:
        """Market orders and gaps, in id order. Each fill can arm a bracket that itself gaps."""
        while True:
            for order in sorted(self._working.values(), key=lambda x: x.id):
                if order.min_t > ts:
                    continue
                late = order.lag_crossed
                hit = self._open_fill(order, op) or ((op, False) if late else None)
                if hit is None:
                    continue
                self._execute(order, hit[0], ts, gap=hit[1], ambiguous=False, at_open=True, base_s=base_s, late=late)
                break
            else:
                break

    def on_sub_bar(self, ts: int, op: float, hi: float, lo: float, base_s: int) -> None:
        if not self._working:
            return
        self._expire_cancels(ts)
        self._note_lag_crosses(ts, hi, lo)
        self._fill_opens(ts, op, base_s)
        # orders triggered inside the bar
        while self._working:
            cands: list[tuple[tuple[int, float, int], Order, float]] = []
            for order in self._working.values():
                if order.min_t > ts:
                    continue
                trig = self._range_trigger(order, hi, lo)
                if trig is not None:
                    protective = order.reduce_only and order.type == "SL"
                    cands.append(((0 if protective else 1, abs(trig - op), order.id), order, trig))
            if not cands:
                return
            cands.sort(key=lambda x: x[0])
            _, order, price = cands[0]
            amb = len(cands) > 1 or order.ambiguous_in == ts
            self._execute(order, price, ts, gap=False, ambiguous=amb, at_open=False, base_s=base_s)

    def on_pine_path(self, ts: int, op: float, hi: float, lo: float, cl: float) -> None:
        """Pine's bar path: green (close >= open) is open→high→low→close, red is open→low→high→close.

        The first resting level on that path fills. Nothing here is flagged ambiguous.
        """
        self._fill_opens(ts, op, 60)
        legs = [(op, hi), (hi, lo), (lo, cl)] if cl >= op else [(op, lo), (lo, hi), (hi, cl)]
        for start, end in legs:
            cursor = start
            for _ in range(32):
                hit = self._nearest_on_leg(ts, cursor, end)
                if hit is None:
                    break
                order, price = hit
                self._execute(order, price, ts, gap=False, ambiguous=False, at_open=False, base_s=60, same_bar=True)
                if price == cursor or price == end:
                    break
                cursor = price

    def _nearest_on_leg(self, ts: int, cursor: float, end: float) -> tuple[Order, float] | None:
        if end == cursor:
            return None
        rising = end > cursor
        best: tuple[tuple[float, int], Order, float] | None = None
        for order in self._working.values():
            if order.min_t > ts or order.price is None or order.type == "MARKET":
                continue
            level = order.price
            if order.type == "SL":
                reached = (rising and cursor < level <= end) or (not rising and end <= level < cursor)
            elif order.side == "BUY":
                reached = (not rising) and end < level < cursor
            else:
                reached = rising and cursor < level < end
            if not reached:
                continue
            key = (abs(level - cursor), order.id)
            if best is None or key < best[0]:
                best = (key, order, level)
        if best is None:
            return None
        return best[1], best[2]

    def fill_now(self, order_id: int, price: float, t: int, children_from: int) -> None:
        """`same_bar_close` mode: fill a MARKET order at the signal bar's close (optimistic)."""
        order = self._working.get(order_id)
        if order is not None:
            self._execute(order, price, t, gap=False, ambiguous=False, at_open=True, base_s=60, optimistic=True,
                          children_from=children_from)

    def force_exit(self, t: int, price: float, reason: str, cancel_reason: str | None) -> None:
        """Square-off / session end / end of data: drop working orders, close the position at `price`."""
        self.cancel_all(t, cancel_reason)
        if self.lots == 0:
            return
        order = Order(self._next_id, "SELL" if self.lots > 0 else "BUY", "MARKET", None, abs(self.lots), reason, None,
                      True, "exit", t)
        self._next_id += 1
        self._working[order.id] = order
        if reason == "end_of_data":
            self.counters["end_of_data_exits"] += 1
        # Square-off fills at the 15:15 open. Session-end and end-of-data fill at a close.
        self._execute(
            order, price, t, gap=False, ambiguous=False, at_open=(reason == "square_off"), base_s=60, reason=reason,
        )

    # ------------------------------------------------------------------ executing a fill
    def _execute(self, order: Order, raw: float, t: int, *, gap: bool, ambiguous: bool, at_open: bool, base_s: int,
                 reason: str | None = None, optimistic: bool = False, children_from: int | None = None,
                 same_bar: bool = False, late: bool = False) -> None:
        self._working.pop(order.id, None)
        pos = self.lots
        lots = order.lots
        if order.reduce_only:
            if pos == 0 or (order.side == "BUY") == (pos > 0):
                self.counters["cancelled"] += 1
                self._event("order_cancelled", t, id=order.id, side=order.side, tag=order.tag, reason="position_closed")
                return
            lots = min(lots, abs(pos))
        sign = 1 if order.side == "BUY" else -1
        opposite = pos != 0 and (sign > 0) != (pos > 0)
        if self.pyramiding == 0 and not order.reduce_only and pos != 0 and not opposite:
            self.counters["cancelled"] += 1
            self._event("order_cancelled", t, id=order.id, side=order.side, tag=order.tag, reason="pyramiding")
            return
        price = raw if order.type == "LIMIT" else self.slippage.apply(order.side, raw)
        if self.pyramiding == 0 and not order.reduce_only and opposite:
            closing = abs(pos)
            opening = order.lots
            lots = closing + opening
        else:
            closing = min(lots, abs(pos)) if opposite else 0
            opening = lots - closing
        day = ist_date(t)
        exit_reason = reason or _REASON[order.type]
        exit_tag = reason if reason else order.tag
        if gap:
            self.counters["gaps"] += 1
        if ambiguous:
            self.counters["ambiguous"] += 1
        if optimistic:
            self.counters["optimistic_fills"] = self.counters.get("optimistic_fills", 0) + 1
        if late:
            self.counters["late_fills"] = self.counters.get("late_fills", 0) + 1
        self.counters["fills"] += 1
        self._event("fill", t, id=order.id, side=order.side, type=order.type, price=float(price), raw_price=float(raw),
                    lots=lots, gap=gap, ambiguous=ambiguous, optimistic=optimistic, reason=exit_reason, tag=order.tag,
                    late=late)
        dp = _d(price)

        def flag(tr: _Open) -> None:
            tr.gap |= gap
            tr.ambiguous |= ambiguous
            tr.optimistic |= optimistic

        def charge(tr: _Open, units: int) -> None:
            leg = self.cost_model.leg_cost(order.side, float(price), units, day)
            for k, v in leg.components.items():
                tr.charges[k] = tr.charges.get(k, Decimal("0")) + v
            tr.slip += abs(dp - _d(raw)) * units if order.type != "LIMIT" else Decimal("0")

        if closing and opening:
            # a reversal: the closed position's bracket must not work against the new one
            for other in [o for o in sorted(self._working.values(), key=lambda x: x.id) if o.kind in ("stop", "target")]:
                self._drop(other, t, "reversed")
        if closing:
            tr = self._open
            assert tr is not None
            units = closing * self.lot_size
            tr.exits.append((dp, units))
            charge(tr, units)
            flag(tr)
            self.lots += sign * closing
            tr.exit_at_open = tr.exit_at_open and at_open
            if self.lots == 0:
                self._finish(tr, t, exit_reason, exit_tag)
        if opening:
            if self.lots == 0:
                self.lot_size = self.lot_resolver(day)
                self._open = _Open(sign, t, order.tag, self.lot_size, entry_at_open=at_open, late=late,
                                   signal_time=order.placed_t)
                self._avg = Decimal("0")
            tr = self._open
            assert tr is not None
            units = opening * self.lot_size
            tr.entries.append((dp, units))
            have = abs(self.lots)
            self._avg = (self._avg * have + dp * opening) / (have + opening)
            charge(tr, units)
            flag(tr)
            self.lots += sign * opening

        if order.oco:
            for other in [o for o in self._working.values() if o.oco == order.oco]:
                self._drop(other, t, "oco")
        if opening and (
            order.stop is not None or order.target is not None
            or order.stop_points is not None or order.target_points is not None
        ):
            self._bracket(order, opening, t, at_open, base_s, children_from, raw, same_bar)
        if self.lots == 0:
            for other in [o for o in sorted(self._working.values(), key=lambda x: x.id) if o.reduce_only]:
                self._drop(other, t, "position_closed")

    def _bracket(self, order: Order, lots: int, t: int, at_open: bool, base_s: int, children_from: int | None,
                 fill_price: float, same_bar: bool = False) -> None:
        side = "SELL" if order.side == "BUY" else "BUY"
        label = f"bracket:{order.id}"
        long = order.side == "BUY"
        stop = order.stop
        target = order.target
        direction = "LONG" if long else "SHORT"
        if order.stop_points is not None:
            stop = index_levels(direction, fill_price, order.stop_points, 0.0)[0]
        if order.target_points is not None:
            target = index_levels(direction, fill_price, 0.0, order.target_points)[1]
        if self._open is not None:
            self._open.stop_level, self._open.target_level = stop, target
        for kind, level in (("stop", stop), ("target", target)):
            if level is None:
                continue
            if children_from is not None:
                min_t, amb_in = children_from, None
            elif same_bar or at_open:
                min_t, amb_in = t, None
            elif kind == "stop":
                min_t, amb_in = t, t  # works in the entry bar, but the order of events inside it is unknown
            else:
                min_t, amb_in = t + base_s, None
            child = Order(self._next_id, side, "SL" if kind == "stop" else "LIMIT", level, lots, f"{order.tag}:{kind}",
                          label, True, kind, min_t, ambiguous_in=amb_in)
            self._next_id += 1
            self._working[child.id] = child
            self.counters["orders"] += 1
            self._event("order_placed", t, id=child.id, side=side, type=child.type, price=level, lots=lots,
                        tag=child.tag, reduce_only=True)

    # ------------------------------------------------------------------ trades
    def _finish(self, tr: _Open, t: int, reason: str, exit_tag: str) -> None:
        e_units = sum(u for _, u in tr.entries)
        x_units = sum(u for _, u in tr.exits)
        e_val = sum((p * u for p, u in tr.entries), Decimal("0"))
        x_val = sum((p * u for p, u in tr.exits), Decimal("0"))
        gross = ((x_val - e_val) * tr.direction).quantize(CENT, rounding=ROUND_HALF_UP)
        total = sum(tr.charges.values(), Decimal("0.00"))
        net = gross - total
        self._realized += net
        lots_in = e_units // tr.lot_size
        self.trades.append(Trade(
            id=len(self.trades) + 1,
            direction="LONG" if tr.direction > 0 else "SHORT",
            entry_time=int(tr.entry_time),
            entry_price=float((e_val / e_units).quantize(PRICE_Q)),
            exit_time=int(t),
            exit_price=float((x_val / x_units).quantize(PRICE_Q)),
            lots=int(lots_in),
            units=int(e_units),
            lot_size=int(tr.lot_size),
            gross_pnl=float(gross),
            charges={k: float(v) for k, v in tr.charges.items()},
            charges_total=float(total),
            slippage_cost=float(tr.slip.quantize(CENT, rounding=ROUND_HALF_UP)),
            net_pnl=float(net),
            exit_reason=reason,
            entry_tag=tr.entry_tag,
            exit_tag=exit_tag,
            gap=tr.gap,
            ambiguous=tr.ambiguous,
            optimistic=tr.optimistic,
            late=tr.late,
            signal_time=tr.signal_time,
            stop_level=tr.stop_level,
            target_level=tr.target_level,
            entry_at_open=tr.entry_at_open,
            exit_at_open=tr.exit_at_open,
            entry_fills=len(tr.entries),
            exit_fills=len(tr.exits),
        ))
        self._open = None
        self._avg = Decimal("0")
