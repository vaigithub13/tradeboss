"""The 15:15 square-off when the contract cannot be priced (M3, review 2026-10-11).

Before: with no quote and no model price, every feed frame after 15:15 added another unfilled EXIT row and the
position stayed open. Now: one row, then the model price at the last stored VIX close (flagged), or, without even
that, a quiet retry on every frame until a price exists.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.paper.pricing import last_stored_vix
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.backtest.costs import load_default_cost_table
from tests.test_paper_session import DAY, Scripted, choose, feed, ist_ms, key_for, quote_both, run_minutes


def _open_long(*, model_price: Any, stale_model_price: Any) -> PaperSession:
    s = PaperSession(day=DAY, strategy=Scripted({0: "BUY"}), choose=choose, key_for=key_for, quotes=QuoteBook(),
                     cost_table=load_default_cost_table(), model_price=model_price,
                     stale_model_price=stale_model_price)
    # quotes only in the first minutes: by 15:15 the last quote is hours old, so there is none
    feed(s, run_minutes((9, 15), (9, 21)), quotes_at=lambda sess, now: quote_both(
        sess, now, ce_bid=99.0, ce_ask=100.0, pe_bid=50.0, pe_ask=51.0))
    feed(s, run_minutes((9, 22), (15, 13)))
    assert s.book.position is not None and s.book.position.entry.source == "quote"
    return s


def _exits(s: PaperSession) -> list[dict[str, Any]]:
    return [r for r in s.signals if r["side"] == "EXIT"]


def test_unpriced_square_off_is_one_row_then_the_model_at_the_last_stored_vix_flagged() -> None:
    s = _open_long(model_price=lambda c, spot, ts: None, stale_model_price=lambda c, spot, ts: 80.0)
    assert s.on_clock(ist_ms(15, 15)) is True
    for second in range(1, 11):
        assert s.on_clock(ist_ms(15, 15) + second * 1000) is False
    rows = _exits(s)
    assert len(rows) == 1
    assert rows[0]["status"] == "unfilled" and "no quote and no model price" in rows[0]["note"]
    assert s.book.position is None
    t = s.book.trades[0]
    assert t["exit_reason"] == "square_off" and t["exit_source"] == "modelled"
    assert t["exit_price"] == 79.5  # the model, with the modelled-fill slippage against us
    assert t["exit_at_ms"] == ist_ms(15, 15)
    assert "last stored VIX" in t["note"]


def test_without_any_price_it_records_one_row_and_retries_quietly_until_a_quote() -> None:
    s = _open_long(model_price=lambda c, spot, ts: None, stale_model_price=None)
    for second in range(0, 10):
        assert s.on_clock(ist_ms(15, 15) + second * 1000) is False
    assert len(_exits(s)) == 1
    assert s.book.position is not None
    late = ist_ms(15, 15) + 10_000
    quote_both(s, late, ce_bid=97.0, ce_ask=97.5, pe_bid=50.0, pe_ask=51.0)
    assert s.on_clock(late) is True
    t = s.book.trades[0]
    assert t["exit_source"] == "quote" and t["exit_price"] == 97.0 and t["exit_at_ms"] == late
    assert "square-off delayed" in t["note"] and "15:15:00" in t["note"]
    assert len(_exits(s)) == 1


def test_a_live_model_price_is_used_before_the_stored_vix() -> None:
    s = _open_long(model_price=lambda c, spot, ts: 90.0, stale_model_price=lambda c, spot, ts: 80.0)
    assert s.on_clock(ist_ms(15, 15)) is True
    t = s.book.trades[0]
    assert t["exit_source"] == "modelled" and t["exit_price"] == 89.5
    assert "note" not in t
    assert _exits(s) == []


def test_last_stored_vix_is_the_close_before_the_day() -> None:
    class Store:
        def __init__(self, bars: list[dict[str, Any]]) -> None:
            self.bars, self.asked = bars, {}

        def load(self, symbol: str, *, from_time: int, to_time: int, session_types: Any = None) -> tuple[list, int]:
            self.asked = {"symbol": symbol, "to_time": to_time}
            return [b for b in self.bars if from_time <= b["time"] <= to_time], 1

    day_start = ist_ms(0, 0) // 1000
    store = Store([{"time": day_start - 3600, "close": 11.5}, {"time": day_start - 60, "close": 12.25},
                   {"time": day_start + 9 * 3600, "close": 99.0}])
    assert last_stored_vix(store, date(2026, 10, 5)) == 12.25
    assert store.asked["symbol"] == "NSE_INDEX_India_VIX" and store.asked["to_time"] < day_start
    assert last_stored_vix(Store([]), date(2026, 10, 5)) is None
