"""The model fallback price: positive, falls as the strike moves away from spot, and reads the VIX of the moment."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.options.contract import OptionContract
from app.paper.pricing import model_price_for

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 10, 5)


def contract(kind: str, strike: float) -> OptionContract:
    return OptionContract(kind=kind, strike=strike, expiry=date(2026, 10, 6), cycle="weekly", lot_size=75,
                          symbol=f"NIFTY {strike:g} {kind} 06 OCT 26", step=50, step_verification="verified")


def ms(h: int, m: int) -> int:
    return int(datetime(2026, 10, 5, h, m, tzinfo=IST).timestamp()) * 1000


def test_atm_call_is_positive_and_gets_cheaper_further_out_of_the_money() -> None:
    price = model_price_for(lambda when: 12.0)
    atm = price(contract("CE", 22500.0), 22500.0, ms(11, 0))
    otm = price(contract("CE", 22800.0), 22500.0, ms(11, 0))
    assert atm > 0 and otm > 0
    assert otm < atm


def test_the_price_reads_the_vix_of_that_moment() -> None:
    seen: list[int] = []

    def vix_at(when: int) -> float:
        seen.append(when)
        return 12.0

    price = model_price_for(vix_at)
    price(contract("PE", 22500.0), 22500.0, ms(11, 0))
    assert seen == [ms(11, 0) // 1000]


def test_higher_vix_means_a_higher_premium() -> None:
    low = model_price_for(lambda when: 10.0)(contract("CE", 22500.0), 22500.0, ms(11, 0))
    high = model_price_for(lambda when: 20.0)(contract("CE", 22500.0), 22500.0, ms(11, 0))
    assert high > low
