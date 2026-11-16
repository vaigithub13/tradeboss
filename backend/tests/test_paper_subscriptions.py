"""Every slot's held and ATM (or strike-by-premium) contracts must be on the feed before the spread set."""

from __future__ import annotations

from datetime import date

from app.backtest.costs import load_default_cost_table
from app.live.spreads.keys import compose_keys, order_option_keys
from app.options.contract import OptionContract
from app.paper.desk import SLOTS, PaperDesk
from app.paper.live import PaperRunner
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from tests import test_paper_session as tps
from tests.test_paper_live import DAY, ms
from tests.test_paper_option_keys import choose_atm, k, step


def test_five_slots_held_and_atm_stay_ahead_of_the_spread_cap() -> None:
    """With 5 slots each holding one contract and advertising an ATM pair, none are refused for the spread set."""
    held = [f"NSE_FO|H{i}" for i in range(5)]
    atm = [f"NSE_FO|A{i}" for i in range(10)]  # 5 slots × CE+PE
    spread = [f"NSE_FO|S{i}" for i in range(40)]
    options = order_option_keys(held, atm, spread)
    keys = compose_keys(["NSE_INDEX|Nifty 50"], options, enabled=True)
    for key in held + atm:
        assert key in keys
    assert keys.index(held[0]) < keys.index(spread[0])


def test_desk_of_five_slots_exposes_every_held_and_atm_key(tmp_path) -> None:
    def make_for(slot: str):
        return lambda day, strategy, params: tps.session(
            tps.Scripted({0: "BUY"} if slot != "2" else {0: "SELL"}),
            model_price=lambda c, sp, ts: 95.0,
        )

    runners = {slot: PaperRunner(PaperDesk.slot_dir(tmp_path, slot), make_for(slot)) for slot in SLOTS}
    d = PaperDesk(runners)
    for slot in ("1", "2", "3", "4", "5"):
        d.runner(slot).start(DAY, "log_xz", {})
    for h, m, close in [(9, m, 22600.0) for m in range(15, 22)]:
        d.on_index_bar(tps.minute(h, m, close), now_ms=ms(h, m))
    assert sum(1 for s in ("1", "2", "3", "4", "5") if d.runner(s).session and d.runner(s).session.book.position) == 5
    held, atm = d.held_keys(), d.atm_keys()
    assert held and atm
    options = order_option_keys(held, atm, [f"NSE_FO|S{i}" for i in range(30)])
    keys = compose_keys(["NSE_INDEX|Nifty 50"], options, enabled=True)
    for key in held | atm:
        assert key in keys


def test_prepare_subscribes_the_strike_premium_contract_not_only_atm() -> None:
    """Slot 5 (Quantman) buys by premium ~250, not ATM. That contract must be wanted before the fill."""

    def model(c: OptionContract, spot: float, now_ms: int) -> float:
        # deeper ITM CE → higher premium; 22400 ≈ 250 when spot 22600
        return max(5.0, (spot - c.strike) + 50.0) if c.kind == "CE" else max(5.0, (c.strike - spot) + 50.0)

    s = PaperSession(
        day=DAY, strategy=tps.Scripted({}), choose=choose_atm, key_for=tps.key_for, quotes=QuoteBook(),
        cost_table=load_default_cost_table(), model_price=model, strike_premium=250.0,
    )
    step(s, 9, 15, 22600.0)
    # ATM would be 22600 CE/PE; premium 250 picks the 22400 CE (model: spot - strike + 50)
    assert k(22400, "CE") in s.atm_keys()
    assert k(22400, "CE") in s.wanted
    assert k(22600, "CE") not in s.atm_keys()
