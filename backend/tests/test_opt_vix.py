"""IV from India VIX (cases 15–16)."""

from __future__ import annotations

from app.options.model import OptionModelConfig
from app.options.vix import vix_asof
from tests.opt_helpers import bar, estimate, ist, trade


def test_15_exact_minute_then_last_within_5_then_stale_then_unpriced() -> None:
    t = ist(2026, 10, 5, 10, 0)
    bars = [bar(t - 180, 13.5), bar(t, 14.0), bar(t + 60, 15.0)]
    q = vix_asof(bars, t)
    assert q is not None and q.value == 14.0 and not q.stale
    missing = [bar(t - 120, 13.2)]
    q2 = vix_asof(missing, t)
    assert q2 is not None and q2.value == 13.2 and q2.age_s == 120 and not q2.stale
    old = [bar(t - 600, 12.0)]
    q3 = vix_asof(old, t)
    assert q3 is not None and q3.stale and q3.value == 12.0
    assert vix_asof([], t) is None
    assert vix_asof([bar(t + 60, 14.0)], t) is None  # future bar is never used


def test_15_a_trade_with_no_vix_is_listed_unpriced() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    out = estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, [])
    assert out.option.counters["unpriced"] == 1 and out.option.trades == []
    assert out.option.unpriced[0]["reason"] == "no_vix"


def test_15_stale_vix_still_prices_and_flags() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill - 600, 14.0)]  # 10 minutes old, used for both legs
    out = estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix)
    assert out.option.trades and "vix_stale" in out.option.trades[0]["flags"]
    assert out.option.trades[0]["iv"] == 0.14


def test_16_default_scale_is_1_and_a_fitted_scale_must_be_written_in() -> None:
    fill, ex = ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 10, 30)
    index = [bar(fill - 60, 25010), bar(fill, 25010), bar(ex, 25060)]
    vix = [bar(fill, 14.0), bar(ex, 14.0)]
    a = estimate([trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix)
    assert a.option.settings["vix_scale"] == 1.0
    assert a.option.trades[0]["iv"] == 0.14
    b = estimate(
        [trade(entry=(fill, 25010), exit=(ex, 25060))], index, vix,
        config=OptionModelConfig(vix_scale=1.18),
    )
    assert b.option.settings["vix_scale"] == 1.18
    assert abs(b.option.trades[0]["iv"] - 0.1652) < 1e-12
    assert a.option.trades[0]["entry_premium"] != b.option.trades[0]["entry_premium"]
