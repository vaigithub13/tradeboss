"""Black-Scholes on the index, used only to estimate a premium. No look-up of real quotes here."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np

SQRT2 = math.sqrt(2.0)


class PricingError(ValueError):
    pass


def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def ncdf_array(x: Any) -> np.ndarray:
    """Same values as `ncdf`, on an array (math.erf per element so scalar and vector never drift)."""
    arr = np.asarray(x, dtype=float)
    flat = arr.ravel()
    out = np.empty(flat.shape, dtype=float)
    for i, v in enumerate(flat):
        out[i] = ncdf(float(v))
    return out.reshape(arr.shape)


def _check(S: float, K: float, T: float, sigma: float) -> None:
    if S <= 0 or K <= 0:
        raise PricingError(f"S and K must be > 0, got S={S} K={K}")
    if T < 0:
        raise PricingError(f"T must be >= 0, got {T}")
    if sigma <= 0:
        raise PricingError(f"sigma must be > 0, got {sigma}")


def bs_forward_price(
    kind: str, S: float, K: float, t_vol: float, t_carry: float, sigma: float, r: float = 0.0, q: float = 0.0
) -> float:
    """Black-Scholes with separate clocks. Variance uses `t_vol`; the forward and discount use `t_carry`.

    F = S · exp((r − q) · t_carry). When `t_vol == t_carry` this is `bs_price` with that one time.
    At t_vol = 0 the price is intrinsic.
    """
    if kind not in ("CE", "PE"):
        raise PricingError(f"kind must be CE or PE, got {kind!r}")
    _check(S, K, t_vol, sigma)
    if t_carry < 0:
        raise PricingError(f"t_carry must be >= 0, got {t_carry}")
    if t_vol == 0.0:
        return max(S - K, 0.0) if kind == "CE" else max(K - S, 0.0)
    vs = sigma * math.sqrt(t_vol)
    d1 = (math.log(S / K) + (r - q) * t_carry + 0.5 * sigma * sigma * t_vol) / vs
    d2 = d1 - vs
    df_q, df_r = math.exp(-q * t_carry), math.exp(-r * t_carry)
    if kind == "CE":
        return S * df_q * ncdf(d1) - K * df_r * ncdf(d2)
    return K * df_r * ncdf(-d2) - S * df_q * ncdf(-d1)


def bs_price(kind: str, S: float, K: float, T: float, sigma: float, r: float = 0.0, q: float = 0.0) -> float:
    """Undiscretised Black-Scholes. At T=0 this is exactly the intrinsic value."""
    if kind not in ("CE", "PE"):
        raise PricingError(f"kind must be CE or PE, got {kind!r}")
    _check(S, K, T, sigma)
    if T == 0.0:
        return max(S - K, 0.0) if kind == "CE" else max(K - S, 0.0)
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vs
    d2 = d1 - vs
    df_q, df_r = math.exp(-q * T), math.exp(-r * T)
    if kind == "CE":
        return S * df_q * ncdf(d1) - K * df_r * ncdf(d2)
    return K * df_r * ncdf(-d2) - S * df_q * ncdf(-d1)


def bs_price_array(
    kind: Any, S: Any, K: Any, T: Any, sigma: Any, r: float = 0.0, q: float = 0.0
) -> np.ndarray:
    """Vectorised `bs_price`. Invalid rows (σ≤0, S≤0, T<0) become NaN rather than raising."""
    kinds = np.asarray(kind)
    Ss, Ks, Ts, sig = (np.asarray(a, dtype=float) for a in (S, K, T, sigma))
    shape = np.broadcast_shapes(np.shape(kinds), Ss.shape, Ks.shape, Ts.shape, sig.shape)
    kinds, Ss, Ks, Ts, sig = (np.broadcast_to(a, shape) for a in (kinds, Ss, Ks, Ts, sig))
    out = np.full(shape, np.nan, dtype=float)
    ok = (Ss > 0) & (Ks > 0) & (Ts >= 0) & (sig > 0)
    zero_t = ok & (Ts == 0)
    pos_t = ok & (Ts > 0)
    ce = kinds == "CE"
    out[zero_t & ce] = np.maximum(Ss[zero_t & ce] - Ks[zero_t & ce], 0.0)
    out[zero_t & ~ce] = np.maximum(Ks[zero_t & ~ce] - Ss[zero_t & ~ce], 0.0)
    if not np.any(pos_t):
        return out
    vs = sig[pos_t] * np.sqrt(Ts[pos_t])
    d1 = (np.log(Ss[pos_t] / Ks[pos_t]) + (r - q + 0.5 * sig[pos_t] ** 2) * Ts[pos_t]) / vs
    d2 = d1 - vs
    df_q, df_r = np.exp(-q * Ts[pos_t]), np.exp(-r * Ts[pos_t])
    call = Ss[pos_t] * df_q * ncdf_array(d1) - Ks[pos_t] * df_r * ncdf_array(d2)
    put = Ks[pos_t] * df_r * ncdf_array(-d2) - Ss[pos_t] * df_q * ncdf_array(-d1)
    priced = np.where(ce[pos_t], call, put)
    out[pos_t] = priced
    return out


def snap_premium(price: float, tick: float = 0.05, floor: float = 0.05) -> float:
    """NSE option tick (default 0.05), half-up, never below `floor`."""
    if tick <= 0:
        return max(float(price), floor)
    q = Decimal(str(tick))
    n = (Decimal(str(price)) / q).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(max(n * q, Decimal(str(floor))))
