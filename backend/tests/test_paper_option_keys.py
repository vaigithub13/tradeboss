"""The option keys a paper session asks the feed for: the contracts it holds, then the current ATM call and put.
A contract that is neither is released, so a wide day cannot fill the feed's option list with old strikes."""

from __future__ import annotations

from datetime import date

from app.backtest.costs import load_default_cost_table
from app.options.contract import OptionContract
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from tests.test_paper_session import DAY, Scripted, ist_ms, key_for, minute


def choose_atm(direction: str, spot: float, on: date) -> OptionContract:
    kind = "CE" if direction == "LONG" else "PE"
    strike = round(spot / 50) * 50
    return OptionContract(kind=kind, strike=float(strike), expiry=date(2026, 10, 6), cycle="weekly", lot_size=75,
                          symbol=f"NIFTY {strike} {kind} 06 OCT 26", step=50, step_verification="verified")


def make(script: dict[int, str]) -> PaperSession:
    return PaperSession(day=DAY, strategy=Scripted(script), choose=choose_atm, key_for=key_for, quotes=QuoteBook(),
                        cost_table=load_default_cost_table(), model_price=lambda c, sp, ts: 95.0)


def step(s: PaperSession, h: int, m: int, close: float) -> None:
    """One exchange-final index minute, then the ATM pair at its close (as the live runner does)."""
    s.on_index_minute(minute(h, m, close), now_ms=ist_ms(h, m))
    s.prepare(close)


def k(strike: int, kind: str) -> str:
    return f"KEY|NIFTY {strike} {kind} 06 OCT 26"


def test_a_new_atm_replaces_the_previous_pair() -> None:
    s = make({})
    step(s, 9, 15, 22600.0)
    assert s.atm_keys() == {k(22600, "CE"), k(22600, "PE")}
    step(s, 9, 16, 22700.0)
    assert s.atm_keys() == {k(22700, "CE"), k(22700, "PE")}
    assert s.held_keys() == set()
    assert s.wanted == {k(22700, "CE"), k(22700, "PE")}


def test_the_open_position_stays_wanted_after_the_atm_moves() -> None:
    s = make({0: "BUY"})
    for m in range(15, 20):
        step(s, 9, m, 22600.0)  # the 09:15 bar closes with 09:19: long the 22600 CE
    for m in (20, 21):
        step(s, 9, m, 22800.0)
    assert s.book.position is not None and s.book.position.key == k(22600, "CE")
    assert s.held_keys() == {k(22600, "CE")}
    assert s.atm_keys() == {k(22800, "CE"), k(22800, "PE")}
    assert s.wanted == {k(22600, "CE"), k(22800, "CE"), k(22800, "PE")}


def test_a_closed_contract_is_released_one_index_minute_after_its_exit_minute() -> None:
    """Its exchange-final minutes can still arrive after the exit (a shadow exit on the closed trade)."""
    s = make({0: "BUY", 1: "SELL"})
    for m in range(15, 20):
        step(s, 9, m, 22600.0)
    for m in range(20, 25):
        step(s, 9, m, 22800.0)  # the 09:20 bar closes with 09:24: the CE closes, a 22800 PE opens
    assert s.book.trades and s.book.trades[-1]["key"] == k(22600, "CE")
    assert s.held_keys() == {k(22600, "CE"), k(22800, "PE")}
    step(s, 9, 25, 22800.0)
    assert s.held_keys() == {k(22800, "PE")}
    assert k(22600, "CE") not in s.wanted
