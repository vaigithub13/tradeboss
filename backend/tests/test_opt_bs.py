"""Premium model (cases 9–13)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.options.bs import PricingError, bs_price, bs_price_array, ncdf, ncdf_array, snap_premium


def test_9_hull_textbook_and_atm_approx() -> None:
    call = bs_price("CE", 42, 40, 0.5, 0.20, 0.10, 0.0)
    put = bs_price("PE", 42, 40, 0.5, 0.20, 0.10, 0.0)
    assert abs(call - 4.7594) < 5e-4 and abs(put - 0.8086) < 5e-4
    atm = bs_price("CE", 25000, 25000, 5 / 250, 0.13)
    assert abs(atm - 183.36) < 0.01
    assert abs(atm - bs_price("PE", 25000, 25000, 5 / 250, 0.13)) < 1e-9
    approx = 25000 * 0.13 * math.sqrt(0.02) * 0.3989
    assert abs(atm - approx) < 0.05


def test_10_put_call_parity_holds_to_1e_9() -> None:
    for S in (20000.0, 25000.0, 26010.0):
        for K in (24900.0, 25000.0, 25100.0):
            for sig in (0.08, 0.13, 0.25):
                for T in (1 / 250, 5 / 250, 0.5):
                    for r, q in ((0.0, 0.0), (0.065, 0.012), (0.10, 0.0)):
                        c = bs_price("CE", S, K, T, sig, r, q)
                        p = bs_price("PE", S, K, T, sig, r, q)
                        rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
                        assert abs((c - p) - rhs) < 1e-9


def test_11_ordering_and_itm_otm_goldens() -> None:
    base = dict(K=25000.0, T=0.02, sigma=0.13, r=0.0, q=0.0)
    assert bs_price("CE", 25100, **base) > bs_price("CE", 25000, **base) > bs_price("CE", 24900, **base)
    assert bs_price("PE", 24900, **base) > bs_price("PE", 25000, **base) > bs_price("PE", 25100, **base)
    assert bs_price("CE", 25000, 25000, 0.02, 0.20) > bs_price("CE", 25000, 25000, 0.02, 0.13)
    assert bs_price("CE", 25000, 25000, 0.04, 0.13) > bs_price("CE", 25000, 25000, 0.02, 0.13)
    otm = round(bs_price("CE", 25000, 25050, 0.02, 0.13, 0.065, 0.012), 2)
    atm = round(bs_price("CE", 25000, 25000, 0.02, 0.13, 0.065, 0.012), 2)
    itm = round(bs_price("CE", 25000, 24950, 0.02, 0.13, 0.065, 0.012), 2)
    assert (otm, atm, itm) == (171.91, 196.76, 223.77)


def test_12_expiry_is_intrinsic_and_bad_inputs_raise() -> None:
    assert bs_price("CE", 25100, 25000, 0.0, 0.13) == 100.0
    assert bs_price("PE", 24900, 25000, 0.0, 0.13) == 100.0
    assert bs_price("CE", 24900, 25000, 0.0, 0.13) == 0.0
    with pytest.raises(PricingError):
        bs_price("CE", 25000, 25000, 0.02, 0.0)
    with pytest.raises(PricingError):
        bs_price("CE", 0.0, 25000, 0.02, 0.13)
    with pytest.raises(PricingError):
        bs_price("CE", 25000, 25000, -0.01, 0.13)
    with pytest.raises(PricingError):
        bs_price("XX", 25000, 25000, 0.02, 0.13)


def test_13_tick_snap_half_up_with_floor_and_can_be_switched_off() -> None:
    assert snap_premium(126.173) == 126.15
    assert snap_premium(151.028) == 151.05
    assert snap_premium(0.02) == 0.05
    assert snap_premium(0.074) == 0.05
    assert snap_premium(0.075) == 0.10
    raw = bs_price("CE", 25010, 25000, 705 / 93750, 0.14)
    assert snap_premium(raw) == 126.15
    assert raw != 126.15


def test_vectorised_bs_matches_scalar() -> None:
    xs = np.linspace(-4, 4, 21)
    assert np.max(np.abs(ncdf_array(xs) - np.array([ncdf(float(x)) for x in xs]))) < 1e-15
    kinds = np.array(["CE", "PE", "CE"])
    S = np.array([25000.0, 25000.0, 25100.0])
    K = np.array([25000.0, 25050.0, 25000.0])
    T = np.array([0.02, 0.01, 0.0])
    sig = np.array([0.13, 0.14, 0.13])
    got = bs_price_array(kinds, S, K, T, sig, 0.065, 0.012)
    want = [bs_price(k, s, k_, t, sg, 0.065, 0.012) for k, s, k_, t, sg in zip(kinds, S, K, T, sig)]
    assert np.max(np.abs(got - want)) < 1e-12
