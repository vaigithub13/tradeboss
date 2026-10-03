"""Refit the option overlay with 7.2% net carry. Does not write model config.

Forward: F = S · exp(0.072 · T_calendar), q = 0. Volatility time stays trading minutes.
VIX scale is refit inside each DTE bucket. The shipped model (r = q = 0, scale 1.0)
is left untouched.

    uv run python -m scripts.option_carry_refit
"""

from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from app.backtest.expiry import load_default_calendar
from app.backtest.sources import ist_date
from app.options.bs import bs_price
from app.options.strikes import atm_strike
from app.options.time import calendar_minutes_to_expiry, trading_minutes_to_expiry

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
IST = timezone(timedelta(hours=5, minutes=30))
IST_S = 5 * 3600 + 30 * 60
EPOCH_ORD = date(1970, 1, 1).toordinal()
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
SESSION_MIN = CLOSE_MIN - OPEN_MIN
TRADING_MIN_PER_YEAR = SESSION_MIN * 250
CALENDAR_MIN_PER_YEAR = 365 * 24 * 60
CARRY = 0.072
OLD_SCALE = 0.8875056964110934
PUBLISHED = DATA / "validation" / "option_model_calibration_2026-10-03.json"
STEP = 50
DTE_NAMES = ("0", "1", "2", "3-4", "5+")
MONEY_NAMES = (
    "CE:ATM", "CE:ITM1", "CE:OTM1", "CE:other",
    "PE:ATM", "PE:ITM1", "PE:OTM1", "PE:other",
)
TOD_NAMES = ("morning", "midday", "afternoon")
HORIZONS = ("15m", "30m", "60m", "EOD")
GRID = np.arange(9 * 60 + 30, 15 * 60 + 15 + 1, 15)
PHI = (math.sqrt(5.0) - 1.0) / 2.0
SQRT2 = math.sqrt(2.0)


def log(msg: str) -> None:
    print(msg, flush=True)


def erf_approx(x: np.ndarray) -> np.ndarray:
    """Hart approximation, max abs error about 1.5e-7. Enough for a sub-paisa premium."""
    sign = np.sign(x)
    ax = np.abs(x)
    a1, a2, a3, a4, a5, p = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429, 0.3275911
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-ax * ax)
    return np.clip(sign * y, -1.0, 1.0)


def ncdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + erf_approx(np.asarray(x, dtype=float) / SQRT2))


def bs_forward(
    is_ce: np.ndarray,
    S: np.ndarray,
    K: np.ndarray,
    T_vol: np.ndarray,
    T_carry: np.ndarray,
    sigma: np.ndarray,
    r: float,
) -> np.ndarray:
    """Black-Scholes with q=0. Discount/forward uses T_carry; variance uses T_vol.

    Put-call parity is S − K exp(−r T_carry), whatever T_vol is.
    """
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T_vol = np.asarray(T_vol, dtype=float)
    T_carry = np.asarray(T_carry, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    is_ce = np.asarray(is_ce, dtype=bool)
    out = np.full(S.shape, np.nan, dtype=float)
    ok = (S > 0) & (K > 0) & (T_vol >= 0) & (sigma > 0) & np.isfinite(T_carry)
    zero = ok & (T_vol == 0)
    pos = ok & (T_vol > 0)
    out[zero & is_ce] = np.maximum(S[zero & is_ce] - K[zero & is_ce], 0.0)
    out[zero & ~is_ce] = np.maximum(K[zero & ~is_ce] - S[zero & ~is_ce], 0.0)
    if not np.any(pos):
        return out
    sig, tv, tc = sigma[pos], T_vol[pos], T_carry[pos]
    vs = sig * np.sqrt(tv)
    d1 = (np.log(S[pos] / K[pos]) + r * tc + 0.5 * sig * sig * tv) / vs
    d2 = d1 - vs
    df = np.exp(-r * tc)
    call = S[pos] * ncdf(d1) - K[pos] * df * ncdf(d2)
    put = K[pos] * df * ncdf(-d2) - S[pos] * ncdf(-d1)
    out[pos] = np.where(is_ce[pos], call, put)
    return out


def huber(real: np.ndarray, model: np.ndarray, delta: float = 0.15) -> float:
    ok = np.isfinite(real) & np.isfinite(model) & (real != 0)
    if not np.any(ok):
        return float("inf")
    rel = (model[ok] - real[ok]) / real[ok]
    a = np.abs(rel)
    loss = np.where(a <= delta, 0.5 * rel * rel, delta * (a - 0.5 * delta))
    return float(np.mean(loss))


def fit_scale(loss, lo: float = 0.3, hi: float = 3.0, tol: float = 1e-4) -> float:
    a, b = lo, hi
    c = b - PHI * (b - a)
    d = a + PHI * (b - a)
    fc, fd = loss(c), loss(d)
    while (b - a) > tol:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - PHI * (b - a)
            fc = loss(c)
        else:
            a, c, fc = c, d, fd
            d = a + PHI * (b - a)
            fd = loss(d)
    return (a + b) / 2.0


def error_stats(real: np.ndarray, model: np.ndarray) -> dict[str, float]:
    ok = np.isfinite(real) & np.isfinite(model) & (real != 0)
    if not np.any(ok):
        return {"n": 0, "bias": math.nan, "mape": math.nan, "median_ape": math.nan, "rmse": math.nan}
    rel = (model[ok] - real[ok]) / real[ok]
    ape = np.abs(rel)
    return {
        "n": int(ok.sum()),
        "bias": float(np.mean(rel)),
        "mape": float(np.mean(ape)),
        "median_ape": float(np.median(ape)),
        "rmse": float(np.sqrt(np.mean(rel * rel))),
    }


def trading_calendar(calendar) -> np.ndarray:
    """cum[ordinal] = number of trading days with toordinal() <= ordinal."""
    last = date(2028, 1, 1).toordinal()
    flag = np.zeros(last + 1, dtype=np.int32)
    d = date(2022, 1, 1)
    end = date(2027, 12, 31)
    while d <= end:
        if calendar.is_trading_day(d):
            flag[d.toordinal()] = 1
        d += timedelta(days=1)
    return np.cumsum(flag)


def build_times(time: np.ndarray, exp_ord: np.ndarray, cum: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    minute = ((time + IST_S) % 86400) // 60
    day_ord = ((time + IST_S) // 86400 + EPOCH_ORD).astype(np.int32)
    # A trading day is one where the cumulative count steps up. Same predicate as the expiry calendar.
    td = cum[day_ord] > cum[day_ord - 1]
    left = np.where(minute < OPEN_MIN, SESSION_MIN, np.where(minute >= CLOSE_MIN, 0, CLOSE_MIN - minute)).astype(np.int32)
    left = np.where(td, left, 0)
    n_after = cum[exp_ord] - cum[day_ord]
    same = day_ord == exp_ord
    past = day_ord > exp_ord
    t_min = np.where(same, left, left + SESSION_MIN * n_after).astype(np.int32)
    t_min = np.where(past, 0, t_min)
    uniq_exp = np.unique(exp_ord)
    close_of = np.zeros(int(uniq_exp.max()) + 1, dtype=np.int64)
    for o in uniq_exp:
        d = date.fromordinal(int(o))
        close_of[int(o)] = int(datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST).timestamp())
    c_min = np.maximum(close_of[exp_ord] - time, 0) // 60
    return t_min, c_min.astype(np.int64), minute.astype(np.int32)


def check_clock_and_prices(time: np.ndarray, exp_ord: np.ndarray, t_min: np.ndarray, c_min: np.ndarray, calendar) -> None:
    rng = np.random.default_rng(1)
    take = rng.choice(len(time), size=min(1500, len(time)), replace=False)
    for i in take:
        t = int(time[i])
        exp = date.fromordinal(int(exp_ord[i]))
        got_t = trading_minutes_to_expiry(t, exp, calendar.is_trading_day)
        got_c = calendar_minutes_to_expiry(t, exp)
        if got_t != int(t_min[i]) or got_c != int(c_min[i]):
            raise SystemExit(f"time basis mismatch at {t} exp {exp}: trading {got_t} vs {int(t_min[i])}, calendar {got_c} vs {int(c_min[i])}")
        if ist_date(t) != date.fromordinal(int((t + IST_S) // 86400 + EPOCH_ORD)):
            raise SystemExit(f"IST date mismatch at {t}")
    hull_c = bs_forward(np.array([True]), np.array([42.0]), np.array([40.0]), np.array([0.5]), np.array([0.5]), np.array([0.20]), 0.10)[0]
    hull_p = bs_forward(np.array([False]), np.array([42.0]), np.array([40.0]), np.array([0.5]), np.array([0.5]), np.array([0.20]), 0.10)[0]
    if abs(hull_c - bs_price("CE", 42, 40, 0.5, 0.20, 0.10, 0.0)) > 1e-4:
        raise SystemExit(f"forward pricer drifted from bs_price on the Hull call: {hull_c}")
    if abs(hull_p - bs_price("PE", 42, 40, 0.5, 0.20, 0.10, 0.0)) > 1e-4:
        raise SystemExit(f"forward pricer drifted from bs_price on the Hull put: {hull_p}")
    # split clocks still obey put-call parity at the carry time
    S, K, tv, tc, sig, r = 25000.0, 25050.0, 3 / 250, 5 / 365, 0.14, CARRY
    c = bs_forward(np.array([True]), np.array([S]), np.array([K]), np.array([tv]), np.array([tc]), np.array([sig]), r)[0]
    p = bs_forward(np.array([False]), np.array([S]), np.array([K]), np.array([tv]), np.array([tc]), np.array([sig]), r)[0]
    parity = S - K * math.exp(-r * tc)
    if abs((c - p) - parity) > 1e-6:
        raise SystemExit(f"carry parity failed: C-P={c - p} vs {parity}")


def load_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    opt_glob = str(DATA / "option_history" / "NIFTY_*.parquet")
    nifty = str(DATA / "candles" / "NIFTY50" / "1m.parquet")
    vix = str(DATA / "candles" / "NSE_INDEX_India_VIX" / "1m.parquet")
    log("reading option parquets")
    con.execute(
        f"""
        CREATE TABLE opt AS
        SELECT
          time,
          CAST(regexp_extract(filename, 'NIFTY_([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})', 1) AS DATE) AS expiry,
          strike,
          kind,
          open AS prem,
          volume
        FROM read_parquet('{opt_glob}', filename=true)
        """
    )
    dust = con.execute("SELECT max(abs(strike - round(strike / 50.0) * 50.0)) FROM opt").fetchone()[0]
    if dust is None or float(dust) > 1e-4:
        raise SystemExit(f"strikes are not on a 50-point grid (max dust {dust})")
    con.execute(
        f"""
        CREATE TABLE idx AS
        SELECT time, open AS spot FROM read_parquet('{nifty}')
        WHERE session_type IN ('normal', 'weekend_full')
        """
    )
    con.execute(
        f"""
        CREATE TABLE vix AS
        SELECT time, open AS vix FROM read_parquet('{vix}')
        WHERE session_type IN ('normal', 'weekend_full')
        """
    )
    dup_i = con.execute("SELECT count(*) - count(DISTINCT time) FROM idx").fetchone()[0]
    dup_v = con.execute("SELECT count(*) - count(DISTINCT time) FROM vix").fetchone()[0]
    if int(dup_i) or int(dup_v):
        raise SystemExit(f"duplicate index/vix minutes: nifty {dup_i}, vix {dup_v}")
    counts = con.execute(
        f"""
        SELECT
          count(*) AS paired,
          count(*) FILTER (WHERE o.volume <= 0) AS zero_volume,
          count(*) FILTER (WHERE o.volume > 0 AND o.prem < 5) AS cheap,
          count(*) FILTER (
            WHERE o.volume > 0 AND o.prem >= 5
              AND ((o.time + {IST_S}) % 86400) // 60 BETWEEN {OPEN_MIN} AND {OPEN_MIN + 2}
          ) AS open_minutes,
          count(*) FILTER (
            WHERE o.volume > 0 AND o.prem >= 5
              AND ((o.time + {IST_S}) % 86400) // 60 NOT BETWEEN {OPEN_MIN} AND {OPEN_MIN + 2}
          ) AS kept
        FROM opt o
        JOIN idx i USING (time)
        JOIN vix v USING (time)
        """
    ).fetchone()
    log(f"paired={counts[0]} zero_volume={counts[1]} cheap={counts[2]} open_minutes={counts[3]} kept={counts[4]}")
    published_kept = 8_695_739
    if int(counts[4]) != published_kept:
        raise SystemExit(f"kept {counts[4]} != published {published_kept}; refusing to refit a different sample")
    log("loading kept points")
    df = con.execute(
        f"""
        SELECT o.time, o.expiry, o.strike, o.kind, o.prem, i.spot, v.vix
        FROM opt o
        JOIN idx i USING (time)
        JOIN vix v USING (time)
        WHERE o.volume > 0 AND o.prem >= 5
          AND ((o.time + {IST_S}) % 86400) // 60 NOT BETWEEN {OPEN_MIN} AND {OPEN_MIN + 2}
        """
    ).df()
    if len(df) != published_kept:
        raise SystemExit(f"downloaded {len(df)} rows, expected {published_kept}")
    return df


def arrays(df: pd.DataFrame, calendar) -> dict[str, np.ndarray]:
    time = df["time"].to_numpy(dtype=np.int64)
    exp = df["expiry"].to_numpy()
    if not np.issubdtype(exp.dtype, np.datetime64):
        raise SystemExit(f"unexpected expiry dtype {exp.dtype}")
    exp_ord = exp.astype("datetime64[D]").astype(np.int64) + EPOCH_ORD
    spot = df["spot"].to_numpy(dtype=float)
    uniq, inv = np.unique(spot, return_inverse=True)
    log(f"ATM on {len(uniq)} distinct spots")
    atm_u = np.array([atm_strike(float(s), STEP) for s in uniq], dtype=float)
    atm = atm_u[inv]
    cum = trading_calendar(calendar)
    t_min, c_min, minute = build_times(time, exp_ord, cum)
    check_clock_and_prices(time, exp_ord, t_min, c_min, calendar)
    log("time basis matches trading_minutes_to_expiry / calendar_minutes_to_expiry")
    day_ord = ((time + IST_S) // 86400 + EPOCH_ORD).astype(np.int32)
    dte = np.where(exp_ord < day_ord, 0, cum[exp_ord] - cum[day_ord]).astype(np.int16)
    rng = np.random.default_rng(2)
    for i in rng.choice(len(time), size=400, replace=False):
        day = date.fromordinal(int(day_ord[i]))
        exp = date.fromordinal(int(exp_ord[i]))
        n = 0
        d = day
        while d < exp:
            d += timedelta(days=1)
            if calendar.is_trading_day(d):
                n += 1
        if n != int(dte[i]):
            raise SystemExit(f"DTE mismatch {day} -> {exp}: {n} vs {int(dte[i])}")
    dte_code = np.full(len(dte), 4, dtype=np.int8)
    dte_code[dte <= 0] = 0
    dte_code[dte == 1] = 1
    dte_code[dte == 2] = 2
    dte_code[(dte >= 3) & (dte <= 4)] = 3
    is_ce = df["kind"].to_numpy() == "CE"
    steps = np.rint((df["strike"].to_numpy(dtype=float) - atm) / STEP).astype(np.int16)
    money = np.full(len(steps), 3, dtype=np.int8)
    money[~is_ce] = 7
    one = np.abs(steps) == 1
    otm = (is_ce & (steps > 0)) | (~is_ce & (steps < 0))
    itm = (is_ce & (steps < 0)) | (~is_ce & (steps > 0))
    money[is_ce & (steps == 0)] = 0
    money[is_ce & one & itm] = 1
    money[is_ce & one & otm] = 2
    money[~is_ce & (steps == 0)] = 4
    money[~is_ce & one & itm] = 5
    money[~is_ce & one & otm] = 6
    tod = np.where(minute < 11 * 60, 0, np.where(minute < 14 * 60, 1, 2)).astype(np.int8)
    return {
        "time": time,
        "exp_ord": exp_ord.astype(np.int32),
        "strike": df["strike"].to_numpy(dtype=float),
        "is_ce": is_ce,
        "prem": df["prem"].to_numpy(dtype=float),
        "spot": spot,
        "vix": df["vix"].to_numpy(dtype=float),
        "T_vol": t_min.astype(float) / TRADING_MIN_PER_YEAR,
        "T_carry": c_min.astype(float) / CALENDAR_MIN_PER_YEAR,
        "minute": minute,
        "dte": dte,
        "dte_code": dte_code,
        "money": money,
        "tod": tod,
    }


def fit_buckets(a: dict[str, np.ndarray]) -> np.ndarray:
    scales = np.empty(5, dtype=float)
    for code, name in enumerate(DTE_NAMES):
        m = a["dte_code"] == code
        real, S, K = a["prem"][m], a["spot"][m], a["strike"][m]
        tv, tc, vix, ce = a["T_vol"][m], a["T_carry"][m], a["vix"][m], a["is_ce"][m]

        def loss(k: float, real=real, S=S, K=K, tv=tv, tc=tc, vix=vix, ce=ce) -> float:
            return huber(real, bs_forward(ce, S, K, tv, tc, k * vix / 100.0, CARRY))

        t0 = time.perf_counter()
        scales[code] = fit_scale(loss)
        log(f"  DTE {name} n={int(m.sum())} scale={scales[code]:.6f} ({time.perf_counter() - t0:.1f}s)")
        if not (0.31 < scales[code] < 2.99):
            log(f"  warning: DTE {name} scale is against the search boundary")
    return scales


def price_with(a: dict[str, np.ndarray], scale: np.ndarray, r: float, carry_time: bool) -> np.ndarray:
    sig = scale * a["vix"] / 100.0
    tc = a["T_carry"] if carry_time else a["T_vol"]
    return bs_forward(a["is_ce"], a["spot"], a["strike"], a["T_vol"], tc, sig, r)


def bucket_table(real: np.ndarray, model: np.ndarray, code: np.ndarray, names: tuple[str, ...]) -> dict[str, dict[str, float]]:
    out = {"all": error_stats(real, model)}
    for i, name in enumerate(names):
        m = code == i
        out[name] = error_stats(real[m], model[m])
    return out


def side_by_side(old: dict[str, dict], new: dict[str, dict], names: tuple[str, ...]) -> list[dict]:
    rows = []
    for name in ("all",) + names:
        o, n = old[name], new[name]
        rows.append({"bucket": name, "old": o, "new": n})
    return rows


def change_errors(a: dict[str, np.ndarray], new_px: np.ndarray, old_px: np.ndarray) -> dict[str, dict]:
    """Same-contract holds. Entry on a 15-minute clock from 09:30 to 15:15.

    Both minutes are in the kept sample (volume > 0, premium ≥ ₹5, not 09:15–09:17).
    """
    time_a = a["time"]
    key = (a["exp_ord"].astype(np.int64) << 20) | (np.rint(a["strike"]).astype(np.int64) << 1) | a["is_ce"].astype(np.int64)
    order = np.lexsort((time_a, key))
    key_s = key[order]
    breaks = np.flatnonzero(key_s[1:] != key_s[:-1]) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [len(order)]))
    bags: dict[str, list[tuple[np.ndarray, ...]]] = {h: [] for h in HORIZONS}
    attempts = {h: 0 for h in HORIZONS}
    for s, e in zip(starts, ends):
        idx = order[s:e]
        t = time_a[idx]
        minute = a["minute"][idx]
        ei = np.flatnonzero(np.isin(minute, GRID))
        if len(ei) == 0:
            continue
        et = t[ei]
        for horizon, secs in (("15m", 15 * 60), ("30m", 30 * 60), ("60m", 60 * 60)):
            xt = et + secs
            xmod = ((xt + IST_S) % 86400) // 60
            j = np.searchsorted(t, xt)
            feasible = xmod < CLOSE_MIN
            attempts[horizon] += int(feasible.sum())
            good = feasible & (j < len(t)) & (t[np.minimum(j, len(t) - 1)] == xt)
            if np.any(good):
                bags[horizon].append((idx[ei[good]], idx[j[good]]))
        sec = (et + IST_S) % 86400
        eod = et - sec + (15 * 60 + 29) * 60
        j = np.searchsorted(t, eod)
        good = (et < eod) & (j < len(t)) & (t[np.minimum(j, len(t) - 1)] == eod)
        attempts["EOD"] += int(len(et))
        if np.any(good):
            bags["EOD"].append((idx[ei[good]], idx[j[good]]))
    out: dict[str, dict] = {}
    for h in HORIZONS:
        if not bags[h]:
            out[h] = {"attempts": attempts[h], "n": 0, "rows": []}
            continue
        gi = np.concatenate([p[0] for p in bags[h]])
        gj = np.concatenate([p[1] for p in bags[h]])
        real_d = a["prem"][gj] - a["prem"][gi]
        err_new = (new_px[gj] - new_px[gi]) - real_d
        err_old = (old_px[gj] - old_px[gi]) - real_d
        out[h] = {
            "attempts": attempts[h],
            "n": int(len(gi)),
            "rows": change_rows(a["dte_code"][gi], a["money"][gi], err_new, err_old, real_d),
        }
        log(f"  holds {h}: attempts={attempts[h]} paired={len(gi)}")
    return out


def _chg_stats(err: np.ndarray, real: np.ndarray) -> dict[str, float]:
    n = int(len(err))
    if n == 0:
        return {"n": 0, "bias": math.nan, "med_abs": math.nan, "med_pct": math.nan, "med_abs_real": math.nan, "n_pct": 0}
    big = np.abs(real) >= 1.0
    med_pct = float(np.median(np.abs(err[big]) / np.abs(real[big])) * 100.0) if np.any(big) else math.nan
    return {
        "n": n,
        "bias": float(np.mean(err)),
        "med_abs": float(np.median(np.abs(err))),
        "med_pct": med_pct,
        "med_abs_real": float(np.median(np.abs(real))),
        "n_pct": int(big.sum()),
    }


def change_rows(
    dte_code: np.ndarray, money: np.ndarray, err_new: np.ndarray, err_old: np.ndarray, real: np.ndarray
) -> list[dict]:
    rows = []

    def add(mask: np.ndarray, dlabel: str, mlabel: str) -> None:
        if not np.any(mask):
            return
        rows.append({
            "dte": dlabel,
            "moneyness": mlabel,
            "new": _chg_stats(err_new[mask], real[mask]),
            "old": _chg_stats(err_old[mask], real[mask]),
        })

    add(np.ones(len(err_new), dtype=bool), "all", "all")
    for i, name in enumerate(DTE_NAMES):
        add(dte_code == i, name, "all")
    for i, name in enumerate(MONEY_NAMES):
        add(money == i, "all", name)
    for di, dname in enumerate(DTE_NAMES):
        for mi, mname in enumerate(MONEY_NAMES):
            add((dte_code == di) & (money == mi), dname, mname)
    return rows


def coverage(con: duckdb.DuckDBPyConnection, calendar) -> dict:
    """Fraction of sessions since 2024-10-03 with ATM and ATM±1 CE/PE of the nearest weekly at every minute."""
    start = date(2024, 10, 3)
    start_ts = int(datetime(start.year, start.month, start.day, tzinfo=IST).timestamp())
    nifty = str(DATA / "candles" / "NIFTY50" / "1m.parquet")
    bars = con.execute(
        f"""
        SELECT time, open AS spot, session_type
        FROM read_parquet('{nifty}')
        WHERE session_type IN ('normal', 'weekend_full') AND time >= {start_ts}
        ORDER BY time
        """
    ).df()
    t = bars["time"].to_numpy(dtype=np.int64)
    spot = bars["spot"].to_numpy(dtype=float)
    minute = ((t + IST_S) % 86400) // 60
    in_sess = (minute >= OPEN_MIN) & (minute < CLOSE_MIN)
    t, spot, minute = t[in_sess], spot[in_sess], minute[in_sess]
    day_ord = (t + IST_S) // 86400 + EPOCH_ORD
    days = np.unique(day_ord)
    expiry_of: dict[int, date] = {}
    for o in days:
        d = date.fromordinal(int(o))
        expiry_of[int(o)] = calendar.next_expiry(d, "weekly").date
    if EPOCH_ORD != 719163:
        raise SystemExit(f"expected 1970-01-01 ordinal 719163, got {EPOCH_ORD}")
    sample_exp = expiry_of[int(days[0])]
    sample_sql = con.execute(
        f"SELECT date_diff('day', DATE '1970-01-01', DATE '{sample_exp.isoformat()}')"
    ).fetchone()[0]
    if int(sample_sql) + EPOCH_ORD != sample_exp.toordinal():
        raise SystemExit(
            f"duckdb day count {sample_sql}+{EPOCH_ORD} != toordinal {sample_exp.toordinal()} for {sample_exp}"
        )
    # one row per index minute
    _, keep = np.unique(t, return_index=True)
    t, spot, minute, day_ord = t[keep], spot[keep], minute[keep], day_ord[keep]
    log(f"coverage: {len(np.unique(day_ord))} index sessions since {start.isoformat()}, {len(t)} minutes")
    uniq, inv = np.unique(spot, return_inverse=True)
    atm_u = np.array([atm_strike(float(s), STEP) for s in uniq], dtype=float)
    atm = np.rint(atm_u[inv]).astype(np.int32)
    # strict 375-minute grid per session; a missing index minute fails the day
    exp_ord = np.array([expiry_of[int(o)].toordinal() for o in day_ord], dtype=np.int32)
    strikes = np.stack([atm - STEP, atm, atm + STEP], axis=1)
    times = np.repeat(t, 6)
    exps = np.repeat(exp_ord, 6)
    sk = np.repeat(strikes, 2, axis=1).reshape(-1)
    kinds = np.tile(np.array(["CE", "PE"], dtype=object), len(t) * 3)
    need = pd.DataFrame({
        "time": times.astype(np.int64),
        "exp_ord": exps.astype(np.int32),
        "strike": sk.astype(np.int32),
        "kind": kinds.astype(str),
        "day_ord": np.repeat(day_ord.astype(np.int32), 6),
    })
    con.register("need", need)
    hit = con.execute(
        """
        WITH keys AS (
          SELECT DISTINCT time, date_diff('day', DATE '1970-01-01', expiry) + 719163 AS exp_ord,
                 CAST(round(strike) AS INTEGER) AS strike, kind
          FROM opt
        )
        SELECT n.day_ord, n.time, count(k.time) AS hits
        FROM need n
        LEFT JOIN keys k
          ON k.time = n.time AND k.exp_ord = n.exp_ord AND k.strike = n.strike AND k.kind = n.kind
        GROUP BY n.day_ord, n.time
        """
    ).df()
    by_day_index: dict[int, int] = {}
    for o in day_ord:
        by_day_index[int(o)] = by_day_index.get(int(o), 0) + 1
    complete = hit[hit["hits"] >= 6]
    by_day_ok: dict[int, int] = {}
    for o in complete["day_ord"].to_numpy():
        by_day_ok[int(o)] = by_day_ok.get(int(o), 0) + 1
    full = 0
    short_index = 0
    minutes_ok = 0
    minutes_all = 0
    missing_expiry_days = 0
    stored = {date.fromisoformat(p.stem[6:]) for p in (DATA / "option_history").glob("NIFTY_*.parquet")}
    for o, n_idx in by_day_index.items():
        minutes_all += n_idx
        minutes_ok += by_day_ok.get(o, 0)
        if expiry_of[o] not in stored:
            missing_expiry_days += 1
        if n_idx != SESSION_MIN:
            short_index += 1
            continue
        if by_day_ok.get(o, 0) == SESSION_MIN:
            full += 1
    regular = len(by_day_index) - short_index
    return {
        "since": start.isoformat(),
        "sessions": len(by_day_index),
        "regular_375": regular,
        "short_sessions": short_index,
        "full_regular": full,
        "fraction_regular": full / regular if regular else math.nan,
        "minutes": minutes_all,
        "minutes_complete": minutes_ok,
        "fraction_minutes": minutes_ok / minutes_all if minutes_all else math.nan,
        "days_nearest_expiry_not_stored": missing_expiry_days,
        "definition": (
            "A regular session is 375 Nifty 1m bars, 09:15–15:29 IST, session_type normal or weekend_full. "
            "A minute is complete when the parquet has the 1m bar (any volume) for ATM−1, ATM and ATM+1, "
            "both CE and PE, of the nearest weekly on or after that day. ATM is the index open, step 50, half-up. "
            "A session counts only if all 375 minutes are complete."
        ),
    }


def _pp(x: float) -> str:
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "—"
    return f"{100.0 * x:+.2f}%"


def _ap(x: float) -> str:
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "—"
    return f"{100.0 * x:.2f}%"


def _rs(x: float, signed: bool = False) -> str:
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "—"
    return f"{x:+.2f}" if signed else f"{x:.2f}"


def _level_md(title: str, rows: list[dict]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| bucket | n | bias old | bias new | med APE old | med APE new | MAPE old | MAPE new | RMSE old | RMSE new |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        o, n = row["old"], row["new"]
        lines.append(
            f"| {row['bucket']} | {n['n']} | {_pp(o['bias'])} | {_pp(n['bias'])} | {_ap(o['median_ape'])} | "
            f"{_ap(n['median_ape'])} | {_ap(o['mape'])} | {_ap(n['mape'])} | {_ap(o['rmse'])} | {_ap(n['rmse'])} |"
        )
    lines.append("")
    return lines


def _chg_md(holds: dict[str, dict]) -> list[str]:
    lines = [
        "## Premium-change error",
        "",
        "Same contract, entry at 09:30, 09:45, …, 15:15. Exit is 15, 30 or 60 minutes later, or the 15:29 bar (EOD). "
        "Both minutes are in the kept sample. Error = (model exit − model entry) − (real exit − real entry), "
        "rupees of premium per unit, not multiplied by lot. Bias is the mean of that error. "
        "Median abs % is the median of |error| / |real change| on pairs whose |real change| is at least ₹1.",
        "",
        "Positive bias: the model change is too high (premium rises too much, or falls too little).",
        "",
    ]
    for h in HORIZONS:
        block = holds[h]
        lines.append(
            f"### {h}  (paired {block['n']} of {block['attempts']} entries whose exit still falls inside 09:15–15:29)"
        )
        lines.append("")
        lines.append("| DTE | moneyness | n | bias ₹ new | med \\|e\\| ₹ new | med \\|e\\|/\\|Δ\\| % new | med \\|Δreal\\| ₹ | bias ₹ old | med \\|e\\| ₹ old | med \\|e\\|/\\|Δ\\| % old |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in block["rows"]:
            n, o = row["new"], row["old"]
            lines.append(
                f"| {row['dte']} | {row['moneyness']} | {n['n']} | {_rs(n['bias'], True)} | {_rs(n['med_abs'])} | "
                f"{_rs(n['med_pct'])} | {_rs(n['med_abs_real'])} | {_rs(o['bias'], True)} | {_rs(o['med_abs'])} | {_rs(o['med_pct'])} |"
            )
        lines.append("")
    return lines


def write_outputs(
    scales: np.ndarray,
    level: dict[str, list[dict]],
    holds: dict[str, dict],
    cover: dict,
    published_scales: dict[str, float],
) -> tuple[Path, Path]:
    as_of = date.today().isoformat()
    payload = {
        "as_of": as_of,
        "carry": CARRY,
        "q": 0.0,
        "vol_time": "trading",
        "carry_time": "calendar",
        "old_global_scale": OLD_SCALE,
        "scale_by_dte": {name: float(scales[i]) for i, name in enumerate(DTE_NAMES)},
        "published_r0_scale_by_dte": published_scales,
        "level_errors": level,
        "change_errors": holds,
        "coverage": cover,
        "config_written": False,
    }
    dest = DATA / "validation"
    dest.mkdir(parents=True, exist_ok=True)
    js = dest / f"option_model_carry_{as_of}.json"
    md = dest / f"option_model_carry_{as_of}.md"
    js.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    lines = [
        f"# Option model, carry 7.2% ({as_of})",
        "",
        "Nothing in this file was written into the model config. `r` and `q` stay 0 and `vix_scale` stays 1.0.",
        "",
        f"Forward = S · exp({CARRY} · T_calendar), q = 0. Volatility time = trading minutes / (375 × 250). "
        "The old column is the published fit: one scale "
        f"**{OLD_SCALE:.4f}**, r = q = 0, trading-minute T, same scale in every bucket.",
        "",
        "The new column refits the VIX scale inside each DTE bucket on the Huber relative-error loss "
        "(same loss, bounds and sample as the published fit: 8,695,739 kept minutes).",
        "",
        "## Scales",
        "",
        "| DTE | scale old (r=0, own bucket) | scale new (carry 7.2%) |",
        "|---|---:|---:|",
    ]
    for name, sc in zip(DTE_NAMES, scales):
        old = published_scales.get(name)
        old_s = f"{old:.4f}" if old is not None else "—"
        lines.append(f"| {name} | {old_s} | {sc:.4f} |")
    lines.append("")
    lines.append("The old *error* table below does **not** use those per-bucket r=0 scales. It uses the single published scale 0.8875 everywhere. The per-bucket r=0 scales are only here so the move in the scale is visible.")
    lines.append("")
    lines.append("## Level error, old vs new")
    lines.append("")
    lines.append("Relative error (model − real) / real. Bias is the mean. APE is the absolute relative error.")
    lines.append("")
    lines += _level_md("All / days to expiry", level["dte"])
    lines += _level_md("Moneyness", level["moneyness"])
    lines += _level_md("Time of day", level["tod"])
    lines += _chg_md(holds)
    lines += [
        "## Real-premium coverage",
        "",
        cover["definition"],
        "",
        f"Sessions since {cover['since']}: **{cover['sessions']}**. "
        f"Regular 375-minute sessions: **{cover['regular_375']}**. "
        f"Of those, **{cover['full_regular']}** ({100 * cover['fraction_regular']:.1f}%) have all six contracts at every minute.",
        "",
        f"Minute level: **{cover['minutes_complete']}** / {cover['minutes']} "
        f"({100 * cover['fraction_minutes']:.1f}%) index minutes are complete.",
        "",
        f"Sessions whose nearest weekly is not in the store: {cover['days_nearest_expiry_not_stored']}. "
        f"Sessions with other than 375 index bars: {cover['short_sessions']}.",
        "",
        "Real-premium mode is not wired into the overlay. A trade would be marked real when "
        "`data/option_history` has that contract's 1m bar at the fill minute, and modelled otherwise. "
        "No backtest was rerun, so there is no live count of real vs modelled trades yet.",
        "",
    ]
    md.write_text("\n".join(lines))
    return md, js


def main() -> None:
    t0 = time.perf_counter()
    calendar = load_default_calendar()
    published = json.loads(PUBLISHED.read_text())
    published_scales = {b: float(published["time_basis"][b]["trading"]["scale"]) for b in DTE_NAMES}
    con = duckdb.connect()
    df = load_frame(con)
    log("building clocks, DTE, moneyness")
    a = arrays(df, calendar)
    del df
    log("pricing the published model (r=0, scale 0.8875)")
    old_px = price_with(a, np.full(len(a["prem"]), OLD_SCALE), 0.0, carry_time=False)
    old_dte = bucket_table(a["prem"], old_px, a["dte_code"], DTE_NAMES)
    old_money = bucket_table(a["prem"], old_px, a["money"], MONEY_NAMES)
    old_tod = bucket_table(a["prem"], old_px, a["tod"], TOD_NAMES)
    pub_all = published["errors"]["all"]["median_ape"]
    if abs(old_dte["all"]["median_ape"] - pub_all) > 5e-4:
        raise SystemExit(
            f"recomputed median APE {old_dte['all']['median_ape']} != published {pub_all}; pricer or sample drifted"
        )
    if abs(old_money["CE:ATM"]["bias"] - published["errors"]["moneyness"]["CE:ATM"]["bias"]) > 5e-3:
        raise SystemExit("CE:ATM bias did not reproduce")
    if abs(old_money["PE:ATM"]["bias"] - published["errors"]["moneyness"]["PE:ATM"]["bias"]) > 5e-3:
        raise SystemExit("PE:ATM bias did not reproduce")
    log(
        f"published table reproduced (median APE {old_dte['all']['median_ape']:.4f}, "
        f"CE:ATM bias {old_money['CE:ATM']['bias']:+.4f}, PE:ATM bias {old_money['PE:ATM']['bias']:+.4f})"
    )
    log("fitting per-DTE scales with carry 7.2%")
    scales = fit_buckets(a)
    scale_row = scales[a["dte_code"]]
    log("pricing the carry model")
    new_px = price_with(a, scale_row, CARRY, carry_time=True)
    level = {
        "dte": side_by_side(old_dte, bucket_table(a["prem"], new_px, a["dte_code"], DTE_NAMES), DTE_NAMES),
        "moneyness": side_by_side(old_money, bucket_table(a["prem"], new_px, a["money"], MONEY_NAMES), MONEY_NAMES),
        "tod": side_by_side(old_tod, bucket_table(a["prem"], new_px, a["tod"], TOD_NAMES), TOD_NAMES),
    }
    log("premium changes")
    holds = change_errors(a, new_px, old_px)
    log("coverage")
    cover = coverage(con, calendar)
    md, js = write_outputs(scales, level, holds, cover, published_scales)
    log(f"wrote {md}")
    log(f"wrote {js}")
    log(f"done in {time.perf_counter() - t0:.0f}s")
    log(f"coverage fraction of regular sessions = {cover['fraction_regular']:.4f}")


if __name__ == "__main__":
    main()
