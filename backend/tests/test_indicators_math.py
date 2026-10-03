"""Indicator math spec (tests written BEFORE the implementation).

Formulas follow TradingView / Pine v5 exactly:

  sma(src, n)        mean of the last n values.                     NaN for the first n-1 bars.
  ema(src, n)        alpha = 2/(n+1); first value = src[0];         defined from bar 0 (no NaN),
                     ema = alpha*src + (1-alpha)*ema[1]             but biased until ~8*(n+1) bars.
  rma(src, n)        alpha = 1/n (Wilder); SEEDED with sma(src, n)  NaN for the first n-1 bars.
  stdev(src, n)      POPULATION std-dev (Pine default biased=true). NaN for the first n-1 bars.
  bb(src, n, m)      basis = sma, dev = m * stdev                   NaN for the first n-1 bars.
  rsi(src, n)        rma(max(change,0),n) / rma(max(-change,0),n)   change is NaN on bar 0, so the
                     down == 0 -> 100; up == 0 -> 0; else 100-100/(1+up/down)   first RSI is bar n.
  tr                 bar 0: high-low; later max(h-l, |h-pc|, |l-pc|)  defined from bar 0.
  atr(n)             rma(tr, n)                                     first ATR is bar n-1.
  macd(f, s, g)      ema(src,f) - ema(src,s); signal = ema(macd, g); hist = macd - signal.
                     Defined from bar 0 (all EMAs are), biased until ~8*(s+1)+8*(g+1) bars.
  supertrend(n, m)   Pine's ta.supertrend: hl2 +/- m*atr(n), bands ratchet, direction: -1 = UPTREND
                     (line = lower band), +1 = DOWNTREND (line = upper band).
                     First value at the first bar with an ATR (bar n-1), direction +1 there.
  vwap(src)          cumulative(src*volume)/cumulative(volume), reset every IST calendar day.
                     NaN until the day's cumulative volume > 0. Zero-volume data -> VolumeRequired.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicators.basic import bollinger, ema, rma, sma, source_series, stdev
from app.indicators.momentum import macd, rsi
from app.indicators.volatility import atr, supertrend, true_range
from app.indicators.volume import VolumeRequired, vwap
from tests.conftest import ist_ts

NAN = float("nan")


def check(actual: pd.Series | np.ndarray, expected: list[float], tol: float = 1e-4) -> None:
    """NaN-aware comparison with a readable failure."""
    a = np.asarray(actual, dtype=float)
    e = np.asarray(expected, dtype=float)
    assert a.shape == e.shape, f"length {a.shape} != {e.shape}"
    assert np.array_equal(np.isnan(a), np.isnan(e)), f"NaN pattern differs:\n{a}\nvs\n{e}"
    mask = ~np.isnan(e)
    np.testing.assert_allclose(a[mask], e[mask], atol=tol, rtol=0)


def S(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def bars(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """rows of (high, low, close)."""
    return pd.DataFrame(
        {
            "open": [r[2] for r in rows],
            "high": [r[0] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[2] for r in rows],
            "volume": [1.0] * len(rows),
        }
    )


# --------------------------------------------------------------------------- sources
def test_sources() -> None:
    df = pd.DataFrame({"open": [10.0], "high": [14.0], "low": [6.0], "close": [12.0]})
    assert source_series(df, "open")[0] == 10
    assert source_series(df, "high")[0] == 14
    assert source_series(df, "low")[0] == 6
    assert source_series(df, "close")[0] == 12
    assert source_series(df, "hl2")[0] == 10  # (14+6)/2
    assert source_series(df, "hlc3")[0] == pytest.approx(32 / 3)  # (14+6+12)/3
    assert source_series(df, "ohlc4")[0] == 10.5  # (10+14+6+12)/4
    with pytest.raises(ValueError):
        source_series(df, "volume")


# --------------------------------------------------------------------------- SMA / EMA / RMA
def test_sma() -> None:
    check(sma(S([1, 2, 3, 4, 5, 6]), 3), [NAN, NAN, 2, 3, 4, 5])
    check(sma(S([4, 8]), 1), [4, 8])


def test_ema_first_value_is_the_source_then_recursive() -> None:
    # n=3 -> alpha 0.5
    # e0=2, e1=.5*4+.5*2=3, e2=.5*6+.5*3=4.5, e3=.5*8+.5*4.5=6.25, e4=.5*10+.5*6.25=8.125
    check(ema(S([2, 4, 6, 8, 10]), 3), [2, 3, 4.5, 6.25, 8.125])


def test_ema_seeds_at_first_valid_value_when_series_starts_with_nan() -> None:
    check(ema(S([NAN, NAN, 2, 4]), 3), [NAN, NAN, 2, 3])


def test_rma_is_seeded_with_sma_then_wilder_smoothing() -> None:
    # n=3, alpha=1/3. idx2 = sma(3,6,9)=6; idx3 = 12/3 + 2/3*6 = 8; idx4 = 15/3 + 2/3*8 = 10.3333
    check(rma(S([3, 6, 9, 12, 15]), 3), [NAN, NAN, 6, 8, 10.333333])


# --------------------------------------------------------------------------- stdev / Bollinger
def test_stdev_is_population_not_sample() -> None:
    # window [1,2,3]: mean 2, population var 2/3 -> 0.816497 (sample would be 1.0)
    check(stdev(S([1, 2, 3, 4]), 3), [NAN, NAN, 0.816497, 0.816497])


def test_bollinger() -> None:
    bb = bollinger(S([1, 2, 3, 4]), length=3, mult=2.0)
    assert list(bb.columns) == ["basis", "upper", "lower"]
    check(bb["basis"], [NAN, NAN, 2, 3])
    check(bb["upper"], [NAN, NAN, 3.632993, 4.632993])  # basis + 2*0.816497
    check(bb["lower"], [NAN, NAN, 0.367007, 1.367007])


# --------------------------------------------------------------------------- RSI
def test_rsi_wilder() -> None:
    # closes 10 11 12 11 13 12 -> up [-,1,1,0,2,0] down [-,0,0,1,0,1]
    # rma3(up):   idx3 = (1+1+0)/3 = 2/3 ; idx4 = 2/3 + 4/9 = 10/9 ; idx5 = 0 + 2/3*10/9 = 20/27
    # rma3(down): idx3 = 1/3 ; idx4 = 2/9 ; idx5 = 1/3 + 4/27 = 13/27
    # RSI idx3 = 100-100/(1+2)      = 66.6667
    # RSI idx4 = 100-100/(1+5)      = 83.3333
    # RSI idx5 = 100-100/(1+20/13)  = 60.6061
    check(rsi(S([10, 11, 12, 11, 13, 12]), 3), [NAN, NAN, NAN, 66.666667, 83.333333, 60.606061])


def test_rsi_extremes_follow_tradingview_rules() -> None:
    check(rsi(S([1, 2, 3, 4, 5]), 3), [NAN, NAN, NAN, 100, 100])  # no losses -> 100
    check(rsi(S([5, 4, 3, 2, 1]), 3), [NAN, NAN, NAN, 0, 0])  # no gains  -> 0
    check(rsi(S([5, 5, 5, 5, 5]), 3), [NAN, NAN, NAN, 100, 100])  # flat: down == 0 -> 100


# --------------------------------------------------------------------------- TR / ATR
def test_true_range() -> None:
    # (high, low, close): bar0 TR = h-l = 2 ; later max(h-l, |h-pc|, |l-pc|)
    df = bars([(10, 8, 9), (11, 9, 10), (12, 9, 11), (13, 11, 12), (15, 12, 14)])
    check(true_range(df), [2, 2, 3, 2, 3])


def test_true_range_uses_gaps() -> None:
    gap_up = bars([(10, 9, 10), (12, 11, 11.5)])  # |h-pc| = 2 beats h-l = 1
    check(true_range(gap_up), [1, 2])
    gap_down = bars([(10, 9, 9), (8.5, 8, 8.2)])  # |l-pc| = 1 beats h-l = 0.5
    check(true_range(gap_down), [1, 1])


def test_atr_is_rma_of_true_range() -> None:
    # TR = [2,2,3,2,3], n=3 (alpha 1/3):
    #   idx2 = (2+2+3)/3 = 7/3 = 2.3333 (SMA seed)
    #   idx3 = 2/3 + 2/3*7/3 = 20/9 = 2.2222
    #   idx4 = 3/3 + 2/3*20/9 = 67/27 = 2.4815
    df = bars([(10, 8, 9), (11, 9, 10), (12, 9, 11), (13, 11, 12), (15, 12, 14)])
    check(atr(df, 3), [NAN, NAN, 2.333333, 2.222222, 2.481481])


# --------------------------------------------------------------------------- MACD
def test_macd_hand_computed() -> None:
    # closes 10 11 12 13, fast=2 (a=2/3), slow=3 (a=1/2), signal=2 (a=2/3)
    # ema2: 10, 32/3, 104/9, 338/27       ema3: 10, 10.5, 11.25, 12.125
    # macd: 0, 1/6, 11/36, 85/216         signal: 0, 1/9, 13/54, 37/108
    # hist = macd - signal: 0, 1/18, 7/108, 11/216
    m = macd(S([10, 11, 12, 13]), fast=2, slow=3, signal=2)
    assert list(m.columns) == ["macd", "signal", "hist"]
    check(m["macd"], [0, 0.166667, 0.305556, 0.393519])
    check(m["signal"], [0, 0.111111, 0.240741, 0.342593])
    check(m["hist"], [0, 0.055556, 0.064815, 0.050926])


# --------------------------------------------------------------------------- Supertrend
SUPERTREND_BARS = [(10, 8, 9), (11, 9, 10), (12, 10, 11), (15, 12, 14), (16, 13, 15), (14, 9, 10)]


def test_supertrend_hand_computed_with_both_flips() -> None:
    # atr(2): [-, 2, 2, 3, 3, 4.5]   hl2: 9 10 11 13.5 14.5 11.5   factor 1
    # bar1: first ATR -> upper 12, lower 8, direction +1 (down) -> line = upper = 12
    # bar2: lower 9, upper stays 12 (ratchet), close 11 !> 12 -> still down, line 12
    # bar3: lower 10.5, upper 12, close 14 > 12 -> FLIP UP (-1), line = lower = 10.5
    # bar4: lower 11.5, upper 17.5, close 15 !< 11.5 -> up, line 11.5
    # bar5: lower stays 11.5, upper 16, close 10 < 11.5 -> FLIP DOWN (+1), line = upper = 16
    st = supertrend(bars(SUPERTREND_BARS), atr_length=2, multiplier=1.0)
    assert list(st.columns) == ["supertrend", "direction"]
    check(st["supertrend"], [NAN, 12, 12, 10.5, 11.5, 16])
    check(st["direction"], [NAN, 1, 1, -1, -1, 1])


def test_supertrend_bands_only_ratchet_in_trend_direction() -> None:
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 1, 600))
    df = pd.DataFrame(
        {"open": close, "high": close + rng.uniform(0.1, 1.5, 600),
         "low": close - rng.uniform(0.1, 1.5, 600), "close": close, "volume": 1.0}
    )  # fmt: skip
    st = supertrend(df, atr_length=10, multiplier=3.0)
    line = st["supertrend"].to_numpy()
    direction = st["direction"].to_numpy()
    for i in range(11, 600):
        if direction[i] == direction[i - 1]:
            if direction[i] == -1:  # uptrend: line never falls
                assert line[i] >= line[i - 1] - 1e-9
            else:  # downtrend: line never rises
                assert line[i] <= line[i - 1] + 1e-9
    assert {-1.0, 1.0} <= set(direction[~np.isnan(direction)])  # the sample flips both ways


# --------------------------------------------------------------------------- VWAP
def vwap_frame(rows: list[tuple[int, float, float, float, float, float]]) -> pd.DataFrame:
    """rows of (time, open, high, low, close, volume)."""
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


DAY1 = ist_ts(2024, 1, 1, 9, 15)
DAY2 = ist_ts(2024, 1, 2, 9, 15)


def test_vwap_resets_every_ist_day() -> None:
    # hlc3: a=(12+8+10)/3=10, b=(14+10+12)/3=12, c=(22+18+20)/3=20, d=(24+20+22)/3=22
    df = vwap_frame(
        [
            (DAY1, 9, 12, 8, 10, 100),
            (DAY1 + 300, 11, 14, 10, 12, 300),
            (DAY2, 19, 22, 18, 20, 50),
            (DAY2 + 300, 21, 24, 20, 22, 50),
        ]
    )
    # day1: 10 ; (10*100+12*300)/400 = 11.5   day2 (reset): 20 ; (20*50+22*50)/100 = 21
    check(vwap(df), [10, 11.5, 20, 21])


def test_vwap_source_parameter() -> None:
    df = vwap_frame(
        [(DAY1, 9, 12, 8, 10, 100), (DAY1 + 300, 11, 14, 10, 12, 300)]
    )
    check(vwap(df, source="open"), [9, 10.5])  # (9*100 + 11*300)/400


def test_vwap_zero_volume_bars() -> None:
    df = vwap_frame(
        [
            (DAY1, 9, 12, 8, 10, 100),
            (DAY1 + 300, 99, 99, 99, 99, 0),  # zero-volume bar: carries the previous VWAP
            (DAY2, 5, 6, 4, 5, 0),  # first bar of a day with no volume yet -> NaN
            (DAY2 + 300, 8, 10, 6, 8, 20),  # hlc3 = 8
        ]
    )
    check(vwap(df), [10, 10, NAN, 8])


def test_vwap_day_boundary_is_ist_not_utc() -> None:
    # 23:45 IST = 18:15 UTC and 00:15 IST (next day) = 18:45 UTC: same UTC date, different IST dates
    t1 = ist_ts(2024, 1, 1, 23, 45)
    t2 = ist_ts(2024, 1, 2, 0, 15)
    df = vwap_frame([(t1, 10, 10, 10, 10, 100), (t2, 20, 20, 20, 20, 100)])
    check(vwap(df), [10, 20])  # NOT 15


def test_vwap_refuses_zero_volume_data() -> None:
    df = vwap_frame([(DAY1, 9, 12, 8, 10, 0), (DAY1 + 300, 11, 14, 10, 12, 0)])
    with pytest.raises(VolumeRequired) as exc:
        vwap(df)
    assert "volume" in str(exc.value).lower()
