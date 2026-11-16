"""Hand-made OHLC: one valid + one near-miss per price-action pattern."""

from __future__ import annotations

from app.strategies.price_action_patterns import (
    OHLC,
    atr_wilder,
    avg_body,
    confirmed,
    detect_dragonfly,
    detect_evening_star,
    detect_gravestone,
    detect_harami_bear,
    detect_harami_bull,
    detect_long_legged,
    detect_morning_star,
    detect_plus_doji,
    detect_tweezer_bottom,
    detect_tweezer_top,
    ema_series,
    invalidated,
    is_downtrend,
    is_uptrend,
)


def B(o: float, h: float, l: float, c: float) -> OHLC:
    return OHLC(o, h, l, c)


def test_tweezer_bottom_valid_and_near_miss():
    atr = 100.0
    # lows within 0.1*ATR=10
    valid = [B(100, 105, 90, 92), B(92, 102, 91, 100)]  # red then green
    assert detect_tweezer_bottom(valid, 1, atr) is not None
    # lows too far apart
    miss = [B(100, 105, 90, 92), B(92, 102, 75, 100)]
    assert detect_tweezer_bottom(miss, 1, atr) is None


def test_tweezer_top_valid_and_near_miss():
    atr = 100.0
    valid = [B(90, 110, 88, 108), B(108, 111, 95, 96)]  # green then red, highs close
    assert detect_tweezer_top(valid, 1, atr) is not None
    miss = [B(90, 110, 88, 108), B(108, 130, 95, 96)]  # highs far
    assert detect_tweezer_top(miss, 1, atr) is None


def test_harami_bull_valid_and_near_miss():
    avg = 10.0
    # c1 big red body 20 (>=12), c2 green inside, body 4 (<=10)
    valid = [B(120, 122, 98, 100), B(105, 108, 103, 107)]
    assert detect_harami_bull(valid, 1, avg) is not None
    # c2 body too large
    miss = [B(120, 122, 98, 100), B(101, 118, 100, 117)]
    assert detect_harami_bull(miss, 1, avg) is None


def test_harami_bear_valid_and_near_miss():
    avg = 10.0
    valid = [B(100, 122, 98, 120), B(112, 115, 108, 109)]
    assert detect_harami_bear(valid, 1, avg) is not None
    miss = [B(100, 122, 98, 120), B(101, 119, 100, 102)]  # body not opposite enough / too big span
    # actually miss: body2=1 ok but need body not inside properly - use oversized body2
    miss = [B(100, 122, 98, 120), B(102, 119, 101, 118)]
    assert detect_harami_bear(miss, 1, avg) is None


def test_morning_star_valid_and_near_miss():
    avg = 10.0
    # c1 big red, c2 small, c3 closes above c1 midpoint
    valid = [
        B(120, 122, 98, 100),  # body 20, mid 110
        B(101, 104, 99, 102),  # body 1 <= 6
        B(103, 118, 102, 116),  # close 116 > 110
    ]
    assert detect_morning_star(valid, 2, avg) is not None
    miss = [
        B(120, 122, 98, 100),
        B(101, 104, 99, 102),
        B(103, 109, 102, 108),  # close 108 <= mid 110
    ]
    assert detect_morning_star(miss, 2, avg) is None


def test_evening_star_valid_and_near_miss():
    avg = 10.0
    valid = [
        B(100, 122, 98, 120),  # mid 110
        B(119, 121, 117, 118),
        B(117, 118, 100, 102),  # close 102 < 110
    ]
    assert detect_evening_star(valid, 2, avg) is not None
    miss = [
        B(100, 122, 98, 120),
        B(119, 121, 117, 118),
        B(117, 118, 112, 114),  # close still above mid
    ]
    assert detect_evening_star(miss, 2, avg) is None


def test_dragonfly_requires_downtrend():
    # doji with long lower wick
    b = B(100, 101, 80, 100.5)  # body 0.5, range 21, upper~0.5, lower~19.5
    assert detect_dragonfly([b], 0, downtrend=True) is not None
    assert detect_dragonfly([b], 0, downtrend=False) is None
    # upper wick too big
    miss = B(100, 110, 80, 100.5)
    assert detect_dragonfly([miss], 0, downtrend=True) is None


def test_gravestone_requires_uptrend():
    b = B(100, 120, 99, 100.5)
    assert detect_gravestone([b], 0, uptrend=True) is not None
    assert detect_gravestone([b], 0, uptrend=False) is None


def test_plus_doji_and_long_legged():
    # balanced wicks, doji
    b = B(100, 110, 90, 100.2)  # range 20, body 0.2, uw~9.8, lw~9.8
    both = detect_plus_doji([b], 0)
    assert len(both) == 2
    assert both[0].next_bar_only and both[1].next_bar_only
    # unbalanced wicks
    miss = B(100, 115, 95, 100.2)
    assert detect_plus_doji([miss], 0) == []

    atr = 10.0
    tall = B(100, 120, 80, 100.1)  # range 40 >= 15, wicks large
    ll = detect_long_legged([tall], 0, atr)
    assert len(ll) == 2
    short_range = B(100, 105, 98, 100.1)
    assert detect_long_legged([short_range], 0, atr) == []


def test_confirm_and_invalid_and_next_bar_only():
    cand = detect_tweezer_bottom([B(100, 105, 90, 92), B(92, 102, 91, 100)], 1, 100.0)
    assert cand is not None
    assert confirmed(cand, B(103, 110, 102, 108), bars_since=1)
    assert not confirmed(cand, B(95, 100, 94, 96), bars_since=1)
    assert invalidated(cand, B(80, 85, 70, 75))

    plus = detect_plus_doji([B(100, 110, 90, 100.2)], 0)[0]
    assert confirmed(plus, B(111, 115, 110, 114), bars_since=1)
    assert not confirmed(plus, B(111, 115, 110, 114), bars_since=2)


def test_trend_helpers():
    closes = [100.0] * 10 + [90.0, 88.0, 86.0, 84.0, 82.0, 80.0, 78.0]
    ema = ema_series(closes, 20)
    # not enough for ema20 seed in short series — use length that works
    closes = list(range(100, 80, -1))  # 20 values declining
    assert len(closes) == 20
    ema = ema_series(closes, 5)
    i = 19
    atr = 2.0
    # manually: with falling prices, downtrend likely once ema ready
    assert ema[i] is not None
    # build a clear downtrend slice
    closes2 = [100.0] * 20 + [99, 98, 97, 96, 95, 94, 90]
    ema2 = ema_series(closes2, 20)
    i2 = len(closes2) - 1
    assert is_downtrend(closes2, ema2, 4.0, i2)
    closes3 = [80.0] * 20 + [81, 82, 83, 84, 85, 86, 92]
    ema3 = ema_series(closes3, 20)
    assert is_uptrend(closes3, ema3, 4.0, len(closes3) - 1)


def test_atr_and_avg_body():
    bars = [B(i, i + 2, i - 1, i + 1) for i in range(20)]
    assert avg_body(bars, 19, 10) is not None
    assert atr_wilder(bars, 19, 14) is not None
    assert atr_wilder(bars, 5, 14) is None
