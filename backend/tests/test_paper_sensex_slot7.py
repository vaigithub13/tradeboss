"""Paper slot 7: dd_five_confirm long on SENSEX (Friday weeklies, strike_premium 800, slip 0.65)."""

from __future__ import annotations

from datetime import date

from app.backtest.lots import lot_size_for_contract, load_default_lot_table
from app.options.strikes import load_default_step_table
from app.paper.desk import INDEX_KEY_FOR_SYMBOL, SLOTS, PaperDesk
from app.paper.live import SENSEX_SLIPPAGE_POINTS, choose_for_chart_symbol, slot_settings
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.backtest.costs import load_default_cost_table
from app.upstox.instruments import NIFTY_INDEX_KEY, SENSEX_INDEX_KEY
from tests import test_paper_session as tps
from tests.test_paper_live import DAY, ms
from tests.test_paper_option_keys import step


def test_sensex_chooser_is_friday_weekly_lot_20() -> None:
    choose = choose_for_chart_symbol("SENSEX")
    # 2026-11-16 Mon → Friday weekly 2026-11-20
    c = choose("LONG", 82_000.0, date(2026, 11, 16))
    assert c.expiry == date(2026, 11, 20)
    assert c.lot_size == 20
    assert c.symbol.startswith("SENSEX ")
    assert c.step == 100
    assert lot_size_for_contract(load_default_lot_table(), "SENSEX", c.expiry, "weekly") == 20
    assert load_default_step_table().step("SENSEX", c.expiry) == 100


def test_slot7_params_scale_and_symbol() -> None:
    rest, minutes, prem, symbol, slip = slot_settings({
        "mode": "long_only", "use_wr": True, "max_trades_per_day": 10, "last_entry": "15:10",
        "timeframe": "3m", "strike_premium": 800, "exit_rule": "premium_trail_10",
        "symbol": "SENSEX", "slippage_points": 0.65,
    })
    assert symbol == "SENSEX" and prem == 800.0 and slip == SENSEX_SLIPPAGE_POINTS == 0.65
    assert minutes == 3 and rest["mode"] == "long_only" and rest["exit_rule"] == "premium_trail_10"


def test_prepare_pre_subscribes_sensex_strike_premium_not_only_atm() -> None:
    """Same fix as slot 5: with strike_premium, prepare wants the premium-nearest CE/PE, not ATM."""

    def model(c, spot, now_ms):
        return max(5.0, abs(spot - c.strike) * 0.1 + 50.0)

    def key_for(symbol: str) -> str:
        return f"BSE_FO|{symbol}"

    s = PaperSession(
        day=DAY, strategy=tps.Scripted({}), choose=choose_for_chart_symbol("SENSEX"),
        key_for=key_for, quotes=QuoteBook(), cost_table=load_default_cost_table(),
        model_price=model, strike_premium=800.0, symbol="SENSEX", model_slippage=0.65,
    )
    step(s, 9, 15, 82_000.0)
    assert s.atm_keys()
    assert all(k.startswith("BSE_FO|SENSEX ") for k in s.atm_keys())
    assert s.wanted == s.atm_keys()


def test_desk_routes_sensex_bars_only_to_sensex_slots(tmp_path) -> None:
    from app.paper.live import PaperRunner

    def make(symbol: str):
        def _make(day, strategy, params):
            return PaperSession(
                day=day, strategy=tps.Scripted({0: "BUY"}),
                choose=choose_for_chart_symbol(symbol),
                key_for=lambda sym: f"KEY|{sym}",
                quotes=QuoteBook(), cost_table=load_default_cost_table(),
                model_price=lambda c, sp, ts: 95.0, symbol=symbol,
            )
        return _make

    runners = {
        slot: PaperRunner(PaperDesk.slot_dir(tmp_path, slot), make("NIFTY50" if slot != "7" else "SENSEX"))
        for slot in SLOTS
    }
    d = PaperDesk(runners)
    d.runner("5").start(DAY, "log_xz", {})
    d.runner("7").start(DAY, "log_xz", {"symbol": "SENSEX"})
    d.on_index_bar(tps.minute(9, 15, 22600.0), now_ms=ms(9, 15), index_key=NIFTY_INDEX_KEY)
    # on_index_minute stamps _index_minute even before a TF bar closes
    assert d.runner("5").session._index_minute is not None
    assert d.runner("7").session._index_minute is None
    d.on_index_bar(tps.minute(9, 16, 82000.0), now_ms=ms(9, 16), index_key=SENSEX_INDEX_KEY)
    assert d.runner("7").session._index_minute is not None
    assert INDEX_KEY_FOR_SYMBOL["SENSEX"] == SENSEX_INDEX_KEY
