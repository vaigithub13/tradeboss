"""Plan maths: stop, target, quantity, rupee risk."""

from datetime import date

from app.scanner.config import ScannerSettings
from app.scanner.models import Hit
from app.scanner.plan import build_plan
from tests.scanner_bars import bar

DAY = date(2026, 1, 5)


def _hit() -> Hit:
    return Hit(setup="orb15", side="long", time=1, close=101.5, stop=100.5, range_high=101.0, range_low=100.0)


def test_quantity_is_capped_by_capital_and_floored_by_risk() -> None:
    settings = ScannerSettings(max_loss_per_trade=500, max_capital_per_trade=50_000)
    plan = build_plan(_hit(), settings, symbol="RELIANCE")
    # risk per share is 1.0 (101.5 - midpoint 100.5), so qty = 500, capital 500 * 101.5
    assert plan.stop == 100.5
    assert plan.entry == 101.5
    assert plan.qty == 492  # floor(50000/101.5) because 500 shares would need more than 50,000
    assert plan.rupee_risk == plan.qty * 1.0
    assert plan.rupee_risk <= 500
    assert plan.capital <= 50_000
    assert plan.target == 103.5
    assert plan.rr == 2


def test_opposite_orb_stop_and_three_r_target() -> None:
    settings = ScannerSettings(orb_stop="opposite", target_mode="3r", max_capital_per_trade=10_000_000)
    plan = build_plan(_hit(), settings, symbol="RELIANCE")
    assert plan.stop == 100.0
    assert plan.target == plan.entry + 3 * (plan.entry - plan.stop)


def test_next_open_uses_the_following_bar() -> None:
    settings = ScannerSettings(entry_mode="next_open", max_capital_per_trade=10_000_000)
    nxt = bar(DAY, 9, 35, 102.0, 102.4, 101.6, 102.2, 1000)
    plan = build_plan(_hit(), settings, symbol="RELIANCE", next_bar=nxt)
    assert plan.entry == 102.0
    assert plan.entry_time == nxt["time"]
    assert plan.status == "triggered"


def test_a_stop_on_the_wrong_side_has_no_quantity() -> None:
    hit = Hit(setup="flag", side="long", time=1, close=100, stop=101)
    plan = build_plan(hit, ScannerSettings(), symbol="INFY")
    assert plan.qty == 0
    assert plan.target is None


def test_waiting_range_is_not_a_trade() -> None:
    hit = Hit(setup="orb15", side="flat", time=1, close=100, forming=True, range_high=101, range_low=100)
    plan = build_plan(hit, ScannerSettings(), symbol="INFY")
    assert plan.status == "waiting"
    assert plan.qty == 0
